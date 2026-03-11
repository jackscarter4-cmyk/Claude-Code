"""
Layer 3 — Directional trade executor.

Takes a MarketAnalysis, validates it against risk parameters,
and places the trade if approved.
"""

import logging

from config.settings import config
from layer1_data.database import Database
from layer3_ai_agent.market_analyzer import MarketAnalysis
from utils.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)


class TradeExecutor:
    """
    Executes directional trades recommended by the AI analyzer.
    Checks risk limits before every trade.
    """

    def __init__(self, db: Database, client: PolymarketClient, risk_guardian=None):
        self.db = db
        self.client = client
        self.risk_guardian = risk_guardian

    async def execute(self, analysis: MarketAnalysis) -> bool:
        """
        Place a directional trade based on a MarketAnalysis.
        Returns True if the order was submitted successfully.
        """
        edge = abs(analysis.edge)
        if edge < config.risk.min_edge_threshold:
            logger.debug(
                "Edge %.3f below threshold %.3f — skipping %s",
                edge, config.risk.min_edge_threshold, analysis.market_id[:8],
            )
            return False

        if analysis.confidence == "LOW":
            logger.debug("LOW confidence — skipping %s", analysis.market_id[:8])
            return False

        # Ask risk guardian
        if self.risk_guardian:
            allowed = await self.risk_guardian.check_layer3_trade(analysis)
            if not allowed:
                logger.info("Risk blocked Layer 3 trade: %s", analysis.market_id[:8])
                return False

        # Determine token ID and trade price
        market = self.db.get_market(analysis.market_id)
        if not market:
            logger.warning("Market %s not in DB — cannot trade", analysis.market_id[:8])
            return False

        if analysis.recommended_side == "YES":
            token_id = market.get("yes_token_id", "")
            trade_price = analysis.market_price  # buy YES at current ask
        else:
            token_id = market.get("no_token_id", "")
            trade_price = 1.0 - analysis.market_price  # NO token price

        if not token_id:
            logger.warning("No token ID for %s side", analysis.recommended_side)
            return False

        # Place the order
        order = await self.client.place_limit_order(
            token_id=token_id,
            side="BUY",
            price=round(trade_price, 4),
            size=analysis.recommended_size_usdc,
        )

        if not order:
            logger.error("Order placement failed for %s", analysis.market_id[:8])
            return False

        trade_id = self.db.log_trade({
            "layer": "layer3",
            "market_id": analysis.market_id,
            "token_id": token_id,
            "side": f"BUY_{analysis.recommended_side}",
            "price": trade_price,
            "size_usdc": analysis.recommended_size_usdc,
            "order_id": order.get("id", ""),
            "reasoning": analysis.reasoning,
            "ai_probability": analysis.ai_probability,
            "market_price": analysis.market_price,
            "edge": analysis.edge,
            "outcome": "OPEN",
        })

        logger.info(
            "Layer 3 trade placed | %s | side=%s price=%.4f size=$%.2f edge=%.3f [trade_id=%d]",
            analysis.question[:50],
            analysis.recommended_side,
            trade_price,
            analysis.recommended_size_usdc,
            analysis.edge,
            trade_id,
        )
        return True
