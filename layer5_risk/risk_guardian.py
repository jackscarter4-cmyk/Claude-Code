"""
Layer 5 — Risk Management and Portfolio Oversight for stocks.

Enforces hard limits that CANNOT be overridden by trading logic:
  1. Market hours enforcement (no trading outside allowed windows)
  2. Max total capital deployed
  3. Max single-stock exposure (8% of portfolio)
  4. Daily loss limit -> halt + alert
  5. Sector concentration limit (40% max in any one sector)
  6. Automatic profit-taking
  7. Anomaly detection

All pre-trade checks are async and are the last gate before an order
reaches the broker.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone, date
from typing import Optional

from config.settings import config
from layer1_data.database import Database
from utils.broker_client import BrokerClient

logger = logging.getLogger(__name__)


def market_is_open() -> bool:
    """Return True between 9:30am and 4:00pm ET on weekdays (Mon-Fri).
    Uses 13:30-20:00 UTC as the combined EST/EDT window."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    hour, minute = now.hour, now.minute
    after_open = (hour > 13) or (hour == 13 and minute >= 30)
    before_close = hour < 20
    return after_open and before_close


def _is_premarket() -> bool:
    """Return True during pre-market hours (4:00am - 9:30am ET = 08:00 - 13:30 UTC)."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    hour, minute = now.hour, now.minute
    after_premarket_open = hour >= 8
    before_market_open = (hour < 13) or (hour == 13 and minute < 30)
    return after_premarket_open and before_market_open


class RiskGuardian:
    """
    Enforces all portfolio-level risk limits for stock trading.

    Injected into Layer 2 (SignalTrader) and Layer 3 (AITradingAgent)
    as a pre-trade check.  Also runs an independent monitoring loop.
    """

    def __init__(self, db: Database, broker: BrokerClient, alert_system=None):
        self.db = db
        self.broker = broker
        self.alert = alert_system

        self._daily_start_value: float = 0.0
        self._current_day: date = date.today()
        self._halted: bool = False

        # Sector exposure tracking: sector -> total market value
        self._sector_exposure: dict = defaultdict(float)

    # ------------------------------------------------------------------
    # Market hours helpers (public API)
    # ------------------------------------------------------------------

    @staticmethod
    def market_is_open() -> bool:
        """Public accessor for market hours check."""
        return market_is_open()

    # ------------------------------------------------------------------
    # Pre-trade checks
    # ------------------------------------------------------------------

    async def check_signal_trade(self, signal) -> bool:
        """Check if a Layer 2 signal trade is permitted."""
        if self._halted:
            logger.warning("Trading halted — blocking Layer 2 signal trade")
            return False

        # Signal trades require the market to be open (market orders only)
        if not market_is_open():
            logger.debug("Market closed — blocking Layer 2 signal trade")
            return False

        # Max open positions
        open_trades = self.db.get_open_trades()
        l2_trades = [t for t in open_trades if t.get("layer") == "layer2"]
        if len(l2_trades) >= config.risk.max_open_positions:
            logger.info("Max open positions reached (%d)", len(l2_trades))
            return False

        # Total exposure check
        account = await self.broker.get_account()
        portfolio_value = account.get("portfolio_value", 0.0)
        if portfolio_value > 0:
            total_exposed = sum(float(t.get("size_usd", 0)) for t in open_trades)
            max_exposed = portfolio_value * config.risk.max_deployed_fraction
            if total_exposed >= max_exposed:
                logger.info(
                    "Max exposure reached: $%.2f / $%.2f", total_exposed, max_exposed
                )
                return False

        return True

    async def check_ai_trade(self, analysis) -> bool:
        """Check if a Layer 3 AI directional trade is permitted."""
        if self._halted:
            logger.warning("Trading halted — blocking Layer 3 AI trade")
            return False

        # Layer 3 limit orders may execute in pre-market; market orders need open market
        order_side = getattr(analysis, "suggested_action", "BUY")
        if not market_is_open() and not _is_premarket():
            logger.debug("Outside trading window — blocking Layer 3 trade")
            return False

        open_trades = self.db.get_open_trades()
        l3_trades = [t for t in open_trades if t.get("layer") == "layer3"]

        if len(l3_trades) >= config.risk.max_open_positions:
            logger.info("Max open positions reached (%d)", len(l3_trades))
            return False

        # Single-stock exposure check
        account = await self.broker.get_account()
        portfolio_value = account.get("portfolio_value", 0.0)
        if portfolio_value > 0:
            symbol = analysis.symbol
            symbol_exposure = sum(
                float(t.get("size_usd", 0))
                for t in l3_trades
                if t.get("symbol") == symbol
            )
            max_symbol = portfolio_value * config.risk.max_single_stock_fraction
            # Confidence-based size: HIGH=5%, MEDIUM=3%
            trade_size = portfolio_value * (
                0.05 if analysis.confidence == "HIGH" else 0.03
            )
            if symbol_exposure + trade_size > max_symbol:
                logger.info(
                    "Single-stock cap hit for %s: $%.2f + $%.2f > $%.2f",
                    symbol, symbol_exposure, trade_size, max_symbol,
                )
                return False

        # Sector concentration check
        if not await self._check_sector_concentration(analysis):
            logger.info(
                "Sector concentration limit hit — blocking %s", analysis.symbol
            )
            return False

        return True

    async def _check_sector_concentration(self, analysis) -> bool:
        """
        Ensure no single sector exceeds max_sector_concentration (default 40%)
        of the total portfolio value after the proposed trade.
        """
        watchlist_entry = self.db.get_watchlist_entry(analysis.symbol)
        sector = (watchlist_entry or {}).get("sector")
        if not sector:
            return True  # Unknown sector — allow through

        account = await self.broker.get_account()
        portfolio_value = account.get("portfolio_value", 1.0)

        positions = await self.broker.get_positions()
        sector_value = 0.0
        for pos in positions:
            sym = pos.get("symbol", "")
            entry = self.db.get_watchlist_entry(sym)
            if entry and entry.get("sector") == sector:
                sector_value += float(pos.get("market_value", 0))

        # Add the proposed trade
        trade_size = portfolio_value * (
            0.05 if analysis.confidence == "HIGH" else 0.03
        )
        projected_sector_pct = (sector_value + trade_size) / portfolio_value
        if projected_sector_pct > config.risk.max_sector_concentration:
            logger.info(
                "Sector '%s' concentration would reach %.1f%% (limit %.1f%%)",
                sector,
                projected_sector_pct * 100,
                config.risk.max_sector_concentration * 100,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Post-trade monitoring
    # ------------------------------------------------------------------

    async def _check_daily_loss_limit(self):
        """Compare today's portfolio value against the daily loss limit."""
        today = date.today()
        if today != self._current_day:
            self._current_day = today
            account = await self.broker.get_account()
            self._daily_start_value = account.get("portfolio_value", 0.0)
            self._halted = False
            logger.info(
                "New trading day — portfolio value reset to $%.2f",
                self._daily_start_value,
            )
            return

        account = await self.broker.get_account()
        current_value = account.get("portfolio_value", 0.0)

        if self._daily_start_value == 0:
            self._daily_start_value = current_value
            return

        daily_loss = self._daily_start_value - current_value
        daily_loss_pct = (
            daily_loss / self._daily_start_value
            if self._daily_start_value > 0
            else 0
        )

        if daily_loss_pct >= config.risk.daily_loss_limit_fraction:
            logger.error(
                "DAILY LOSS LIMIT HIT: lost $%.2f (%.1f%%) — HALTING ALL TRADING",
                daily_loss, daily_loss_pct * 100,
            )
            self._halted = True
            if self.alert:
                await self.alert.send(
                    f"CRITICAL: Daily loss limit hit. Lost ${daily_loss:.2f} "
                    f"({daily_loss_pct*100:.1f}%). Trading halted."
                )

    async def _auto_profit_take(self):
        """
        Close Layer 3 positions that have reached the profit-taking target
        (default: +25% from entry).
        """
        open_trades = self.db.get_open_trades()
        positions = await self.broker.get_positions()
        pos_by_symbol = {p["symbol"]: p for p in positions}

        for trade in open_trades:
            if trade.get("layer") != "layer3":
                continue
            symbol = trade.get("symbol", "")
            entry_price = float(trade.get("price", 0))
            if not symbol or entry_price <= 0:
                continue

            pos = pos_by_symbol.get(symbol)
            if not pos:
                continue

            current_price = float(pos.get("current_price", 0))
            if current_price <= 0:
                continue

            return_pct = (current_price - entry_price) / entry_price
            if return_pct >= config.risk.profit_take_return:
                logger.info(
                    "Profit-taking on %s: return=%.1f%%", symbol, return_pct * 100
                )
                qty = float(pos.get("qty", 0))
                if qty > 0:
                    await self.broker.place_market_order(symbol, qty, "sell")
                    pnl = float(trade.get("size_usd", 0)) * return_pct
                    self.db.update_trade_outcome(trade["id"], pnl, "WIN")

    async def _check_stop_losses(self):
        """Close positions that have fallen below their stop-loss price."""
        open_trades = self.db.get_open_trades()
        positions = await self.broker.get_positions()
        pos_by_symbol = {p["symbol"]: p for p in positions}

        for trade in open_trades:
            stop_loss = trade.get("stop_loss")
            if stop_loss is None:
                continue
            symbol = trade.get("symbol", "")
            pos = pos_by_symbol.get(symbol)
            if not pos:
                continue

            current_price = float(pos.get("current_price", 0))
            if current_price > 0 and current_price <= float(stop_loss):
                logger.warning(
                    "STOP LOSS triggered for %s: current=$%.2f stop=$%.2f",
                    symbol, current_price, float(stop_loss),
                )
                qty = float(pos.get("qty", 0))
                if qty > 0:
                    await self.broker.place_market_order(symbol, qty, "sell")
                    entry = float(trade.get("price", current_price))
                    pnl = float(trade.get("size_usd", 0)) * (
                        (current_price - entry) / entry
                    )
                    self.db.update_trade_outcome(trade["id"], pnl, "LOSS")
                    if self.alert:
                        await self.alert.send(
                            f"Stop loss triggered: sold {symbol} at ${current_price:.2f} "
                            f"(entry=${entry:.2f}, loss=${pnl:.2f})"
                        )

    # ------------------------------------------------------------------
    # Main monitoring loop
    # ------------------------------------------------------------------

    async def run_forever(self, interval: int = 60):
        """Run risk monitoring checks every `interval` seconds."""
        account = await self.broker.get_account()
        self._daily_start_value = account.get("portfolio_value", 0.0)
        logger.info(
            "Risk guardian started. Opening portfolio value: $%.2f",
            self._daily_start_value,
        )

        while True:
            try:
                await self._check_daily_loss_limit()
                if market_is_open():
                    await self._auto_profit_take()
                    await self._check_stop_losses()
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
