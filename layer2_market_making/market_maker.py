"""
Layer 2 — Rule-based signal trader (SignalTrader).

Runs every 5 minutes during market hours (9:30am–4pm ET on weekdays),
scans watchlist symbols for trading signals, and executes those with
strength > 0.6 that pass risk checks.

Position sizing:
  strength >= 0.8 → 5% of portfolio
  strength >= 0.6 → 2–3% of portfolio (interpolated)
"""

import asyncio
import logging
from datetime import datetime, timezone

from config.settings import config
from layer1_data.database import Database
from layer2_market_making.signal_scanner import SignalScanner, TradingSignal
from utils.broker_client import BrokerClient, YahooFinanceClient

logger = logging.getLogger(__name__)


def _is_market_hours() -> bool:
    """Return True between 9:30am and 4:00pm ET on weekdays (UTC-4/UTC-5).
    Uses 13:30–20:00 UTC as the combined EST/EDT window."""
    now = datetime.now(timezone.utc)
    # Monday=0 … Friday=4
    if now.weekday() >= 5:
        return False
    hour, minute = now.hour, now.minute
    # 13:30 UTC = 9:30 ET (EDT, UTC-4)
    after_open = (hour > 13) or (hour == 13 and minute >= 30)
    # 20:00 UTC = 4:00 PM EDT
    before_close = hour < 20
    return after_open and before_close


class SignalTrader:
    """
    Layer 2 signal-driven trading engine.

    Continuously scans the watchlist for rule-based signals and executes
    qualifying trades through the broker.
    """

    def __init__(self, db: Database, broker: BrokerClient, risk_guardian=None):
        self.db = db
        self.broker = broker
        self.risk_guardian = risk_guardian
        self.scanner = SignalScanner()
        self.yahoo = YahooFinanceClient()
        self._running = False

    # ------------------------------------------------------------------
    # Data gathering
    # ------------------------------------------------------------------

    async def _gather_market_data(self, symbols: list[str]) -> dict[str, dict]:
        """
        Fetch market data (52W range, volume, prev close, earnings date)
        from Yahoo Finance for each symbol.
        """
        market_data: dict[str, dict] = {}
        for symbol in symbols:
            try:
                info = await self.yahoo.get_quote_summary(symbol)
                # Also grab 20-day bars for low_20d calculation
                bars = await self.broker.get_bars(symbol, timeframe="1Day", limit=22)
                low_20d = min((b["l"] for b in bars), default=0.0) if bars else 0.0
                price_2d_ago = bars[-3]["c"] if len(bars) >= 3 else 0.0
                prev_close = bars[-2]["c"] if len(bars) >= 2 else 0.0
                current_volume = bars[-1]["v"] if bars else 0.0

                market_data[symbol] = {
                    "high_52w": info.get("fifty_two_week_high") or 0.0,
                    "low_52w": info.get("fifty_two_week_low") or 0.0,
                    "avg_volume": info.get("avg_volume") or 0.0,
                    "current_volume": current_volume,
                    "prev_close": prev_close,
                    "low_20d": low_20d,
                    "price_2d_ago": price_2d_ago,
                    "earnings_date": info.get("earnings_date"),
                    "beta": info.get("beta"),
                }
                await asyncio.sleep(0.2)
            except Exception as exc:
                logger.warning("market data fetch failed for %s: %s", symbol, exc)
                market_data[symbol] = {}
        return market_data

    # ------------------------------------------------------------------
    # Signal execution
    # ------------------------------------------------------------------

    def _compute_order_size(self, signal: TradingSignal, portfolio_value: float) -> float:
        """
        Translate signal strength into a dollar order size.
        strength 0.6 → 2% of portfolio
        strength 0.8 → 3.5%
        strength 1.0 → 5%
        """
        min_strength = config.signal.min_signal_strength
        if signal.strength < min_strength:
            return 0.0
        # Linear interpolation between min and max allocation
        alloc_pct = 0.02 + (signal.strength - 0.6) / 0.4 * 0.03  # 2% → 5%
        alloc_pct = max(0.02, min(0.05, alloc_pct))
        return round(portfolio_value * alloc_pct, 2)

    async def _execute_signal(self, signal: TradingSignal, portfolio_value: float) -> bool:
        """
        Place an order for a qualifying signal.
        Returns True if the order was submitted.
        """
        size_usd = self._compute_order_size(signal, portfolio_value)
        if size_usd <= 0:
            return False

        # Risk guardian pre-check
        if self.risk_guardian:
            allowed = await self.risk_guardian.check_signal_trade(signal)
            if not allowed:
                logger.debug("Risk guardian blocked signal: %s %s", signal.signal_type, signal.symbol)
                return False

        # Compute share qty
        if signal.price <= 0:
            return False
        qty = size_usd / signal.price
        side = signal.direction.lower()  # "buy" or "sell"

        # Place market order
        order = await self.broker.place_market_order(signal.symbol, qty, side)
        if not order:
            logger.error("Order failed for signal %s %s", signal.signal_type, signal.symbol)
            return False

        # Log to database
        stop_loss_price = round(signal.price * (1.0 - config.risk.stop_loss_pct), 4)
        self.db.log_trade({
            "layer": "layer2",
            "symbol": signal.symbol,
            "side": side,
            "price": signal.price,
            "size_usd": size_usd,
            "order_id": order.get("id", ""),
            "reasoning": f"[{signal.signal_type}] {signal.reasoning}",
            "stop_loss": stop_loss_price if side == "buy" else None,
            "strategy": signal.signal_type,
            "outcome": "OPEN",
        })

        logger.info(
            "Layer 2 order placed | %s %s %s qty=%.4f @ $%.2f size=$%.2f strength=%.2f",
            signal.signal_type, side.upper(), signal.symbol,
            qty, signal.price, size_usd, signal.strength,
        )
        return True

    # ------------------------------------------------------------------
    # Main scan-and-trade cycle
    # ------------------------------------------------------------------

    async def run_once(self):
        """Execute one full scan-and-trade cycle."""
        if not _is_market_hours():
            logger.debug("SignalTrader: outside market hours, skipping scan")
            return

        symbols = self.db.get_active_symbols()
        if not symbols:
            logger.warning("SignalTrader: no active symbols in watchlist")
            return

        # Fetch current prices
        prices = await self.yahoo.get_prices_bulk(symbols)

        # Fetch positions
        raw_positions = await self.broker.get_positions()
        position_data: dict[str, dict] = {p["symbol"]: p for p in raw_positions}

        # Account info for sizing
        account = await self.broker.get_account()
        portfolio_value = account.get("portfolio_value", 100_000.0)

        # Fetch market data
        market_data = await self._gather_market_data(symbols)

        # Generate signals
        signals = self.scanner.scan(symbols, prices, position_data, market_data)

        qualifying = [s for s in signals if s.strength >= config.signal.min_signal_strength]
        logger.info(
            "SignalTrader: %d signals generated, %d qualifying (strength >= %.1f)",
            len(signals), len(qualifying), config.signal.min_signal_strength,
        )

        trades_placed = 0
        for signal in qualifying:
            placed = await self._execute_signal(signal, portfolio_value)
            if placed:
                trades_placed += 1
                await asyncio.sleep(0.5)  # brief pause between orders

        logger.info("SignalTrader cycle complete: %d trades placed", trades_placed)

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    async def run_forever(self):
        self._running = True
        interval = config.signal.scan_interval_seconds

        while self._running:
            try:
                await self.run_once()
            except Exception as exc:
                logger.error("SignalTrader loop error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

    def stop(self):
        self._running = False
        logger.info("SignalTrader stopped")
