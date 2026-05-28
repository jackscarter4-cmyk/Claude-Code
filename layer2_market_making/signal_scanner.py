"""
Layer 2 — Stock signal scanner.

Rule-based trading signals for equities:

  MOMENTUM_BREAKOUT   — price near 52W high + elevated volume
  OVERSOLD_REBOUND    — price far below 52W high + at 20-day low
  VOLUME_SPIKE        — unusual volume with meaningful price move
  REBALANCE           — position weight has drifted from target
  EARNINGS_DRIFT      — large price move near upcoming earnings date
  BOLLINGER_OVERSOLD  — price below lower Bollinger Band (%B < 0)
  BOLLINGER_OVERBOUGHT— price above upper Bollinger Band (%B > 1)
  VWAP_BREAKOUT       — close above VWAP on elevated volume (institutional buying)
  VWAP_BREAKDOWN      — close below VWAP on elevated volume (institutional selling)

Bollinger Bands: Bollinger (1983, 2001) — bands at SMA_20 ± 2σ_20 contain ~95%
  of prices; %B = (price − lower) / (upper − lower).
VWAP: Berkowitz, Logue & Noser (1988) — standard institutional execution benchmark.
OBV: Granville (1963) — obv_trend (+ = accumulation, − = distribution) modulates
  signal strength when provided.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from config.settings import config

logger = logging.getLogger(__name__)


@dataclass
class TradingSignal:
    symbol: str
    signal_type: str       # "MOMENTUM_BREAKOUT" | "OVERSOLD_REBOUND" | "VOLUME_SPIKE" | "REBALANCE" | "EARNINGS_DRIFT"
    direction: str         # "BUY" | "SELL"
    strength: float        # 0–1
    price: float
    reasoning: str
    suggested_size_usd: float


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


class SignalScanner:
    """
    Scans a set of symbols against current price/position data and emits
    TradingSignal objects for any conditions that meet the configured
    thresholds.
    """

    def __init__(self, signal_config=None, risk_config=None):
        self.cfg = signal_config or config.signal
        self.risk_cfg = risk_config or config.risk

    # ------------------------------------------------------------------
    # Individual signal checks
    # ------------------------------------------------------------------

    def _momentum_breakout(
        self,
        symbol: str,
        price: float,
        high_52w: float,
        volume: float,
        avg_volume: float,
        portfolio_value: float,
    ) -> Optional[TradingSignal]:
        """
        MOMENTUM_BREAKOUT: price within 2% of 52W high AND volume > 1.5× avg.
        """
        if high_52w <= 0 or avg_volume <= 0:
            return None

        pct_from_high = (high_52w - price) / high_52w
        vol_ratio = volume / avg_volume

        if pct_from_high <= 0.02 and vol_ratio >= 1.5:
            strength = min(1.0, 0.5 + (vol_ratio - 1.5) * 0.2 + (0.02 - pct_from_high) * 10)
            size = portfolio_value * (0.02 + 0.01 * strength)
            return TradingSignal(
                symbol=symbol,
                signal_type="MOMENTUM_BREAKOUT",
                direction="BUY",
                strength=round(strength, 3),
                price=price,
                reasoning=(
                    f"{symbol} is {pct_from_high*100:.1f}% below 52W high (${high_52w:.2f}) "
                    f"with volume at {vol_ratio:.1f}x average — momentum breakout signal."
                ),
                suggested_size_usd=round(size, 2),
            )
        return None

    def _oversold_rebound(
        self,
        symbol: str,
        price: float,
        high_52w: float,
        low_20d: float,
        portfolio_value: float,
    ) -> Optional[TradingSignal]:
        """
        OVERSOLD_REBOUND: price > 15% below 52W high AND price <= 20-day low.
        """
        if high_52w <= 0:
            return None

        pct_from_high = (high_52w - price) / high_52w

        if pct_from_high >= abs(self.cfg.oversold_threshold) and price <= low_20d * 1.01:
            strength = min(1.0, 0.4 + (pct_from_high - 0.15) * 2)
            size = portfolio_value * (0.015 + 0.01 * strength)
            return TradingSignal(
                symbol=symbol,
                signal_type="OVERSOLD_REBOUND",
                direction="BUY",
                strength=round(strength, 3),
                price=price,
                reasoning=(
                    f"{symbol} is {pct_from_high*100:.1f}% below its 52W high "
                    f"and at/near its 20-day low (${low_20d:.2f}) — contrarian rebound candidate."
                ),
                suggested_size_usd=round(size, 2),
            )
        return None

    def _volume_spike(
        self,
        symbol: str,
        price: float,
        prev_close: float,
        volume: float,
        avg_volume: float,
        portfolio_value: float,
    ) -> Optional[TradingSignal]:
        """
        VOLUME_SPIKE: volume > 2× avg AND |price_change| > 2%.
        Direction depends on whether price moved up or down.
        """
        if avg_volume <= 0 or prev_close <= 0:
            return None

        vol_ratio = volume / avg_volume
        price_chg = (price - prev_close) / prev_close

        if vol_ratio < self.cfg.volume_spike_threshold:
            return None
        if abs(price_chg) < 0.02:
            return None

        direction = "BUY" if price_chg > 0 else "SELL"
        strength = min(1.0, 0.5 + (vol_ratio - 2.0) * 0.1 + abs(price_chg) * 5)
        size = portfolio_value * (0.02 + 0.01 * strength)

        return TradingSignal(
            symbol=symbol,
            signal_type="VOLUME_SPIKE",
            direction=direction,
            strength=round(strength, 3),
            price=price,
            reasoning=(
                f"{symbol} volume is {vol_ratio:.1f}x average with a "
                f"{price_chg*100:+.1f}% price move — unusual activity spike."
            ),
            suggested_size_usd=round(size, 2),
        )

    def _rebalance_signal(
        self,
        symbol: str,
        price: float,
        current_weight: float,
        target_weight: float,
        portfolio_value: float,
    ) -> Optional[TradingSignal]:
        """
        REBALANCE: position weight has drifted > 5% from target weight.
        """
        drift = current_weight - target_weight
        if abs(drift) < self.cfg.rebalance_drift_threshold:
            return None

        direction = "SELL" if drift > 0 else "BUY"
        # Size = amount needed to bring position back to target
        target_value = target_weight * portfolio_value
        current_value = current_weight * portfolio_value
        rebalance_usd = abs(current_value - target_value)
        strength = min(1.0, abs(drift) / 0.10)  # full strength at 10% drift

        return TradingSignal(
            symbol=symbol,
            signal_type="REBALANCE",
            direction=direction,
            strength=round(strength, 3),
            price=price,
            reasoning=(
                f"{symbol} weight is {current_weight*100:.1f}% vs target {target_weight*100:.1f}% "
                f"(drift={drift*100:+.1f}%) — rebalance {direction.lower()} needed."
            ),
            suggested_size_usd=round(rebalance_usd, 2),
        )

    def _bollinger_signal(
        self,
        symbol: str,
        price: float,
        bollinger_upper: float,
        bollinger_lower: float,
        sma_20: float,
        obv_trend: Optional[float],
        portfolio_value: float,
    ) -> Optional[TradingSignal]:
        """
        Bollinger Band mean-reversion signal.

        %B = (price − lower) / (upper − lower)  [Bollinger 1983]
        %B < 0: price below lower band → oversold, contrarian BUY candidate.
        %B > 1: price above upper band → overbought, potential SELL / trim.

        OBV trend (Granville 1963) modulates strength:
        + OBV on a BUY signal = volume accumulation confirms reversal.
        − OBV on a BUY signal = distribution divergence, reduce confidence.
        """
        band_width = bollinger_upper - bollinger_lower
        if band_width <= 0:
            return None

        pct_b = (price - bollinger_lower) / band_width

        if pct_b < 0:
            strength = min(1.0, 0.5 + abs(pct_b) * 2.0)
            if obv_trend is not None:
                strength = min(1.0, strength * (1.15 if obv_trend > 0 else 0.80))
            size = portfolio_value * (0.015 + 0.01 * strength)
            obv_note = " [OBV confirms accumulation]" if obv_trend and obv_trend > 0 else (
                " [OBV divergence — caution]" if obv_trend and obv_trend < 0 else "")
            return TradingSignal(
                symbol=symbol,
                signal_type="BOLLINGER_OVERSOLD",
                direction="BUY",
                strength=round(strength, 3),
                price=price,
                reasoning=(
                    f"{symbol} %B={pct_b:.2f} — below lower Bollinger Band "
                    f"(${bollinger_lower:.2f}, SMA20=${sma_20:.2f}) — "
                    f"mean-reversion entry.{obv_note}"
                ),
                suggested_size_usd=round(size, 2),
            )

        if pct_b > 1:
            strength = min(1.0, 0.5 + (pct_b - 1.0) * 2.0)
            if obv_trend is not None and obv_trend < 0:
                strength = min(1.0, strength * 1.15)
            size = portfolio_value * 0.015
            return TradingSignal(
                symbol=symbol,
                signal_type="BOLLINGER_OVERBOUGHT",
                direction="SELL",
                strength=round(strength, 3),
                price=price,
                reasoning=(
                    f"{symbol} %B={pct_b:.2f} — above upper Bollinger Band "
                    f"(${bollinger_upper:.2f}) — overbought trim signal."
                ),
                suggested_size_usd=round(size, 2),
            )
        return None

    def _vwap_signal(
        self,
        symbol: str,
        price: float,
        vwap: float,
        volume: float,
        avg_volume: float,
        obv_trend: Optional[float],
        portfolio_value: float,
    ) -> Optional[TradingSignal]:
        """
        VWAP institutional signal.

        VWAP is the standard institutional execution benchmark (Berkowitz et al. 1988).
        A close ≥1.2% above VWAP on ≥1.2× avg volume signals institutional buying.
        A close ≥1.2% below VWAP on ≥1.2× avg volume signals institutional selling.

        OBV trend confirmation adjusts strength: matching OBV direction = +15%,
        conflicting OBV = −10%.
        """
        if vwap <= 0 or avg_volume <= 0:
            return None

        vol_ratio = volume / avg_volume
        if vol_ratio < 1.2:
            return None

        deviation = (price - vwap) / vwap

        if deviation >= 0.012:
            strength = min(1.0, 0.4 + abs(deviation) * 12.0 + (vol_ratio - 1.2) * 0.15)
            if obv_trend is not None:
                strength = min(1.0, strength * (1.10 if obv_trend > 0 else 0.90))
            return TradingSignal(
                symbol=symbol,
                signal_type="VWAP_BREAKOUT",
                direction="BUY",
                strength=round(strength, 3),
                price=price,
                reasoning=(
                    f"{symbol} closed {deviation*100:.1f}% above VWAP (${vwap:.2f}) "
                    f"on {vol_ratio:.1f}× avg volume — institutional buying confirmed."
                ),
                suggested_size_usd=round(portfolio_value * (0.02 + 0.01 * strength), 2),
            )

        if deviation <= -0.012:
            strength = min(1.0, 0.4 + abs(deviation) * 12.0 + (vol_ratio - 1.2) * 0.15)
            if obv_trend is not None:
                strength = min(1.0, strength * (1.10 if obv_trend < 0 else 0.90))
            return TradingSignal(
                symbol=symbol,
                signal_type="VWAP_BREAKDOWN",
                direction="SELL",
                strength=round(strength, 3),
                price=price,
                reasoning=(
                    f"{symbol} closed {abs(deviation)*100:.1f}% below VWAP (${vwap:.2f}) "
                    f"on {vol_ratio:.1f}× avg volume — institutional selling pressure."
                ),
                suggested_size_usd=round(portfolio_value * 0.015, 2),
            )
        return None

    def _earnings_drift(
        self,
        symbol: str,
        price: float,
        price_2d_ago: float,
        earnings_date_str: Optional[str],
        portfolio_value: float,
    ) -> Optional[TradingSignal]:
        """
        EARNINGS_DRIFT: stock moved > 5% in 2 days AND earnings within 7 days.
        Flags for AI review rather than auto-trading.
        """
        if not earnings_date_str or price_2d_ago <= 0:
            return None

        try:
            earnings_dt = datetime.strptime(earnings_date_str[:10], "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            return None

        now = datetime.now(timezone.utc)
        days_to_earnings = (earnings_dt - now).days

        if days_to_earnings < 0 or days_to_earnings > 7:
            return None

        price_move = (price - price_2d_ago) / price_2d_ago
        if abs(price_move) < 0.05:
            return None

        direction = "BUY" if price_move > 0 else "SELL"
        strength = min(1.0, 0.5 + abs(price_move) * 5)
        size = portfolio_value * 0.02  # small size; flag for AI review

        return TradingSignal(
            symbol=symbol,
            signal_type="EARNINGS_DRIFT",
            direction=direction,
            strength=round(strength, 3),
            price=price,
            reasoning=(
                f"{symbol} moved {price_move*100:+.1f}% over 2 days with earnings in "
                f"{days_to_earnings} days ({earnings_date_str}) — flagged for AI review."
            ),
            suggested_size_usd=round(size, 2),
        )

    # ------------------------------------------------------------------
    # Main scan entry point
    # ------------------------------------------------------------------

    def scan(
        self,
        symbols: list[str],
        prices: dict[str, float],
        position_data: dict[str, dict],
        market_data: Optional[dict[str, dict]] = None,
    ) -> list[TradingSignal]:
        """
        Scan all symbols and return a list of TradingSignals sorted by
        strength (strongest first).

        Args:
            symbols:       List of symbols to evaluate.
            prices:        {symbol: current_price}.
            position_data: {symbol: {qty, avg_entry_price, market_value, ...}}.
            market_data:   Optional {symbol: {high_52w, low_52w, avg_volume,
                           current_volume, prev_close, low_20d, earnings_date,
                           target_weight}}.  Missing keys are handled gracefully.
        """
        if market_data is None:
            market_data = {}

        signals: list[TradingSignal] = []

        # Compute portfolio value from positions
        portfolio_value = sum(
            _safe_float(pos.get("market_value"))
            for pos in position_data.values()
        )
        if portfolio_value <= 0:
            portfolio_value = 100_000.0  # fallback for paper trading

        total_position_value = portfolio_value  # used for weight calculations

        for symbol in symbols:
            price = _safe_float(prices.get(symbol))
            if price <= 0:
                continue

            md = market_data.get(symbol, {})
            pos = position_data.get(symbol, {})

            high_52w = _safe_float(md.get("high_52w") or md.get("fifty_two_week_high"))
            low_20d = _safe_float(md.get("low_20d"), price)
            avg_volume = _safe_float(md.get("avg_volume"))
            current_volume = _safe_float(md.get("current_volume") or md.get("volume"))
            prev_close = _safe_float(md.get("prev_close"), price)
            price_2d_ago = _safe_float(md.get("price_2d_ago"), price)
            earnings_date = md.get("earnings_date")
            target_weight = _safe_float(md.get("target_weight"), 0.0)

            bollinger_upper = _safe_float(md.get("bollinger_upper"))
            bollinger_lower = _safe_float(md.get("bollinger_lower"))
            sma_20 = _safe_float(md.get("sma_20"), price)
            obv_trend_raw = md.get("obv_trend")
            obv_trend = float(obv_trend_raw) if obv_trend_raw is not None else None
            vwap = _safe_float(md.get("vwap"))

            market_value = _safe_float(pos.get("market_value"))
            current_weight = market_value / total_position_value if total_position_value > 0 else 0.0

            # 1. Momentum breakout
            sig = self._momentum_breakout(symbol, price, high_52w, current_volume, avg_volume, portfolio_value)
            if sig:
                signals.append(sig)

            # 2. Oversold rebound
            sig = self._oversold_rebound(symbol, price, high_52w, low_20d, portfolio_value)
            if sig:
                signals.append(sig)

            # 3. Volume spike
            sig = self._volume_spike(symbol, price, prev_close, current_volume, avg_volume, portfolio_value)
            if sig:
                signals.append(sig)

            # 4. Rebalance (only if we hold the stock and have a target weight)
            if target_weight > 0 and market_value > 0:
                sig = self._rebalance_signal(symbol, price, current_weight, target_weight, portfolio_value)
                if sig:
                    signals.append(sig)

            # 5. Earnings drift
            sig = self._earnings_drift(symbol, price, price_2d_ago, earnings_date, portfolio_value)
            if sig:
                signals.append(sig)

            # 6. Bollinger Band signals (requires bollinger_upper + bollinger_lower)
            if bollinger_upper > 0 and bollinger_lower > 0:
                sig = self._bollinger_signal(
                    symbol, price, bollinger_upper, bollinger_lower,
                    sma_20, obv_trend, portfolio_value
                )
                if sig:
                    signals.append(sig)

            # 7. VWAP institutional signals (requires vwap + volume data)
            if vwap > 0 and avg_volume > 0 and current_volume > 0:
                sig = self._vwap_signal(
                    symbol, price, vwap, current_volume, avg_volume,
                    obv_trend, portfolio_value
                )
                if sig:
                    signals.append(sig)

        # Sort by strength descending
        signals.sort(key=lambda s: s.strength, reverse=True)
        logger.info(
            "Signal scan complete: %d symbols scanned, %d signals generated",
            len(symbols),
            len(signals),
        )
        return signals
