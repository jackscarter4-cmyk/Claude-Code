"""
Layer 5 — Risk Management and Portfolio Oversight.

This layer wraps around all trading layers and enforces hard limits
that CANNOT be overridden by trading logic:

  1. Max total capital deployed
  2. Max single-market exposure
  3. Daily loss limit → halt + alert
  4. Correlation limits (multiple positions on same underlying)
  5. Automatic profit-taking
  6. Anomaly detection (price data outages, flash crashes)

All checks are synchronous at decision time. The guardian is
the last gate before any order hits the exchange.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone, date
from typing import Optional

from config.settings import config
from layer1_data.database import Database
from utils.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)


class RiskGuardian:
    """
    Enforces all portfolio-level risk limits.

    Injected into Layer 2 and Layer 3 as a pre-trade check.
    Also runs an independent monitoring loop for post-trade oversight.
    """

    def __init__(self, db: Database, client: PolymarketClient, alert_system=None):
        self.db = db
        self.client = client
        self.alert = alert_system

        # Track daily PnL for the loss-limit
        self._daily_start_balance: float = 0.0
        self._current_day: date = date.today()
        self._halted: bool = False

        # Correlation tracking: topic_key -> list of market_ids
        self._correlated_groups: dict[str, list[str]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Pre-trade checks
    # ------------------------------------------------------------------

    async def check_layer2_trade(self, opportunity) -> bool:
        """Check if a Layer 2 market-making trade is permitted."""
        if self._halted:
            logger.warning("Trading halted — blocking Layer 2 trade")
            return False

        # Check position count
        open_trades = self.db.get_open_trades()
        l2_trades = [t for t in open_trades if t.get("layer") == "layer2"]
        if len(l2_trades) >= config.market_making.max_mm_positions:
            logger.debug("Max MM positions reached (%d)", len(l2_trades))
            return False

        # Check total exposure
        balance = await self.client.get_balance()
        if balance > 0:
            total_exposed = sum(float(t.get("size_usdc", 0)) for t in open_trades)
            max_exposed = balance * config.risk.max_deployed_fraction
            if total_exposed >= max_exposed:
                logger.info(
                    "Max exposure reached: $%.2f / $%.2f", total_exposed, max_exposed
                )
                return False

        return True

    async def check_layer3_trade(self, analysis) -> bool:
        """Check if a Layer 3 directional trade is permitted."""
        if self._halted:
            logger.warning("Trading halted — blocking Layer 3 trade")
            return False

        open_trades = self.db.get_open_trades()
        l3_trades = [t for t in open_trades if t.get("layer") == "layer3"]

        # Max open positions
        if len(l3_trades) >= config.risk.max_open_positions:
            logger.info("Max open positions reached (%d)", len(l3_trades))
            return False

        # Single-market exposure
        balance = await self.client.get_balance()
        if balance > 0:
            market_exposure = sum(
                float(t.get("size_usdc", 0))
                for t in l3_trades
                if t.get("market_id") == analysis.market_id
            )
            max_market = balance * config.risk.max_single_market_fraction
            if market_exposure + analysis.recommended_size_usdc > max_market:
                logger.info(
                    "Single-market cap hit for %s: $%.2f + $%.2f > $%.2f",
                    analysis.market_id[:8],
                    market_exposure,
                    analysis.recommended_size_usdc,
                    max_market,
                )
                return False

        # Correlation check
        if not self._check_correlation(analysis.market_id, analysis.question):
            logger.info("Correlation limit hit — blocking %s", analysis.market_id[:8])
            return False

        return True

    def _check_correlation(self, market_id: str, question: str) -> bool:
        """
        Detect if this market is too correlated with existing positions.

        Uses keyword clustering: markets sharing high-frequency keywords
        (trump, fed, btc, etc.) are considered correlated.
        """
        CORRELATION_KEYWORDS = [
            "trump", "biden", "harris", "fed", "federal reserve",
            "bitcoin", "btc", "ethereum", "eth", "crypto",
            "election", "senate", "house", "supreme court",
            "ukraine", "russia", "china", "taiwan",
        ]

        question_lower = question.lower()
        for keyword in CORRELATION_KEYWORDS:
            if keyword in question_lower:
                group = self._correlated_groups[keyword]
                if market_id not in group:
                    if len(group) >= config.risk.max_correlated_positions:
                        return False
                    group.append(market_id)
        return True

    # ------------------------------------------------------------------
    # Post-trade monitoring
    # ------------------------------------------------------------------

    async def _check_daily_loss_limit(self):
        """Compare today's PnL against the daily loss limit."""
        today = date.today()
        if today != self._current_day:
            # New day — reset
            self._current_day = today
            self._daily_start_balance = await self.client.get_balance()
            self._halted = False
            logger.info("New trading day — balance reset to $%.2f", self._daily_start_balance)
            return

        current_balance = await self.client.get_balance()
        if self._daily_start_balance == 0:
            self._daily_start_balance = current_balance
            return

        daily_loss = self._daily_start_balance - current_balance
        daily_loss_pct = daily_loss / self._daily_start_balance if self._daily_start_balance > 0 else 0

        if daily_loss_pct >= config.risk.daily_loss_limit_fraction:
            logger.error(
                "DAILY LOSS LIMIT HIT: lost $%.2f (%.1f%%) — HALTING ALL TRADING",
                daily_loss,
                daily_loss_pct * 100,
            )
            self._halted = True
            if self.alert:
                await self.alert.send(
                    f"CRITICAL: Daily loss limit hit. Lost ${daily_loss:.2f} "
                    f"({daily_loss_pct*100:.1f}%). Trading halted."
                )

    async def _auto_profit_take(self):
        """Close positions that have reached the profit-taking target."""
        open_trades = self.db.get_open_trades()
        for trade in open_trades:
            if trade.get("layer") != "layer3":
                continue
            token_id = trade.get("token_id", "")
            entry_price = float(trade.get("price", 0))
            if not token_id or entry_price == 0:
                continue

            current_price = await self.client.get_midpoint_price(token_id)
            if current_price is None:
                continue

            return_pct = (current_price - entry_price) / entry_price
            if return_pct >= config.risk.profit_take_return:
                logger.info(
                    "Profit-taking on trade %s: return=%.1f%%",
                    trade.get("id"),
                    return_pct * 100,
                )
                # Place a SELL order at current market price
                size = float(trade.get("size_usdc", 0))
                await self.client.place_limit_order(token_id, "SELL", current_price, size)
                pnl = size * return_pct
                self.db.update_trade_outcome(trade["id"], pnl, "WIN")

    async def _check_anomalies(self):
        """
        Detect platform-level anomalies (e.g., all prices simultaneously
        jumping to extremes — possible data issue or platform outage).
        """
        markets = self.db.get_active_markets()[:20]  # sample check
        extreme_count = 0
        for market in markets:
            ticks = self.db.get_recent_prices(market["id"], limit=5)
            if ticks:
                recent = [float(t["price"]) for t in ticks]
                if any(p < 0.01 or p > 0.99 for p in recent):
                    extreme_count += 1

        if extreme_count > len(markets) * 0.5 and len(markets) > 5:
            logger.warning(
                "ANOMALY: %d/%d markets showing extreme prices — possible data issue",
                extreme_count,
                len(markets),
            )
            self._halted = True
            if self.alert:
                await self.alert.send(
                    f"WARNING: {extreme_count}/{len(markets)} markets at extreme prices. "
                    "Trading paused for investigation."
                )

    # ------------------------------------------------------------------
    # Main monitoring loop
    # ------------------------------------------------------------------

    async def run_forever(self, interval: int = 60):
        """Run risk monitoring checks every `interval` seconds."""
        # Initialize starting balance
        self._daily_start_balance = await self.client.get_balance()
        logger.info(
            "Risk guardian started. Opening balance: $%.2f", self._daily_start_balance
        )

        while True:
            try:
                await self._check_daily_loss_limit()
                await self._auto_profit_take()
                await self._check_anomalies()
            except Exception as exc:
                logger.error("Risk monitor error: %s", exc)
            await asyncio.sleep(interval)

    @property
    def is_halted(self) -> bool:
        return self._halted

    def resume_trading(self):
        """Manually resume trading after a halt (operator action)."""
        self._halted = False
        logger.info("Trading manually resumed")
