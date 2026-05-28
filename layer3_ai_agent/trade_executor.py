"""
Layer 3 — Stock trade executor.

Takes a StockAnalysis from the AI analyzer, validates it against risk
parameters, and executes a buy or sell order via the broker if approved.

Rules:
  - BUY only if outlook is BULLISH and confidence is MEDIUM or HIGH.
  - SELL only if outlook is BEARISH and we currently hold the stock.
  - Position size: HIGH=5% of portfolio, MEDIUM=3% of portfolio, LOW=skip.
  - Always attach a stop-loss at -8% from entry on new buys.
"""

import logging
from typing import Optional

from config.settings import config
from layer1_data.database import Database
from layer3_ai_agent.market_analyzer import StockAnalysis
from utils.broker_client import BrokerClient

logger = logging.getLogger(__name__)


class TradeExecutor:
    """
    Executes directional stock trades recommended by the AI analyzer.
    Checks risk limits before every trade.
    """

    def __init__(self, db: Database, broker: BrokerClient, risk_guardian=None):
        self.db = db
        self.broker = broker
        self.risk_guardian = risk_guardian

    def _size_from_confidence(self, confidence: str, portfolio_value: float) -> float:
        """Return dollar allocation based on confidence level."""
        alloc = {
            "HIGH": 0.05,
            "MEDIUM": 0.03,
            "LOW": 0.0,
        }.get(confidence, 0.0)
        return round(portfolio_value * alloc, 2)

    async def execute(
        self,
        analysis: StockAnalysis,
        portfolio_value: float,
        current_positions: Optional[dict] = None,
    ) -> bool:
        """
        Place a directional trade based on a StockAnalysis.
        Returns True if the order was submitted successfully.
        """
        if current_positions is None:
            current_positions = {}

        if analysis.outlook == "NEUTRAL":
            logger.debug("NEUTRAL outlook — skipping %s", analysis.symbol)
            return False

        if analysis.confidence == "LOW":
            logger.debug("LOW confidence — skipping %s", analysis.symbol)
            return False

        if analysis.outlook == "BULLISH" and analysis.suggested_action == "BUY":
            side = "buy"
        elif analysis.outlook == "BEARISH" and analysis.suggested_action == "SELL":
            if analysis.symbol not in current_positions:
                logger.debug("BEARISH on %s but no position to sell", analysis.symbol)
                return False
            side = "sell"
        else:
            logger.debug(
                "Outlook %s / action %s — no trade for %s",
                analysis.outlook, analysis.suggested_action, analysis.symbol,
            )
            return False

        size_usd = self._size_from_confidence(analysis.confidence, portfolio_value)
        if size_usd <= 0:
            return False

        if self.risk_guardian:
            allowed = await self.risk_guardian.check_ai_trade(analysis)
            if not allowed:
                logger.info("Risk guardian blocked Layer 3 trade: %s", analysis.symbol)
                return False

        if analysis.current_price <= 0:
            logger.warning("Invalid price for %s — cannot compute qty", analysis.symbol)
            return False

        if side == "sell":
            pos = current_positions.get(analysis.symbol, {})
            qty = float(pos.get("qty", 0))
            if qty <= 0:
                logger.warning("No shares to sell for %s", analysis.symbol)
                return False
        else:
            qty = size_usd / analysis.current_price

        order = await self.broker.place_market_order(analysis.symbol, qty, side)
        if not order:
            logger.error("Order placement failed for %s", analysis.symbol)
            return False

        stop_loss_price = None
        if side == "buy":
            stop_loss_price = round(
                analysis.current_price * (1.0 - config.risk.stop_loss_pct), 4
            )

        trade_id = self.db.log_trade({
            "layer": "layer3",
            "symbol": analysis.symbol,
            "side": side,
            "price": analysis.current_price,
            "size_usd": size_usd,
            "order_id": order.get("id", ""),
            "reasoning": analysis.reasoning,
            "target_price": analysis.target_price,
            "stop_loss": stop_loss_price,
            "strategy": f"AI_{analysis.outlook}_{analysis.confidence}",
            "outcome": "OPEN",
        })

        logger.info(
            "Layer 3 trade placed | %s %s %s qty=%.4f @ $%.2f size=$%.2f "
            "target=$%.2f stop=$%.2f [trade_id=%d]",
            analysis.outlook, side.upper(), analysis.symbol,
            qty, analysis.current_price, size_usd,
            analysis.target_price,
            stop_loss_price or 0.0,
            trade_id,
        )
        return True
