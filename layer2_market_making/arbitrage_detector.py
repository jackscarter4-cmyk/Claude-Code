"""
Layer 2 — Rule-based trading signal scanner.

Detects high-conviction trading signals using technical and fundamental
criteria inspired by the Alpha Picks quant methodology:

  MOMENTUM_BREAKOUT  — price near 52W high + volume spike → BUY
  OVERSOLD_REBOUND   — price well below 52W high + cost basis → review
  VOLUME_SPIKE       — unusual volume with directional price move
  REBALANCE          — position weight drifted from target → rebalance
  EARNINGS_DRIFT     — large move near an earnings date → flag for AI
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from config.settings import config

logger = logging.getLogger(__name__)


@dataclass
class TradingSignal:
    symbol: str
    signal_type: str    # see module docstring for types
    direction: str      # "BUY" or "SELL"
    strength: float     # 0.0–1.0
    price: float
    reasoning: str
    suggested_size_usd: float
    metadata: dict = field(default_factory=dict)


class SignalScanner:
    """
    Scans watchlist symbols for rule-based trading signals.
    No AI required — pure price/volume/position math.
    """

    def __init__(self, portfolio_value: float = 0):
        self.portfolio_value = portfolio_value
        self.cfg = config.signal

    def _momentum_breakout(self, symbol: str, price: float, bars: list[dict]) -> Optional[TradingSignal]:
        """
        BUY signal: price within N% of 52W high AND volume > threshold × avg.
        Stocks breaking out to new highs on strong volume tend to continue higher.
        """
        if len(bars) < 20:
            return None

        highs = [float(b.get("h", b.get("high", price))) for b in bars]
        vols  = [float(b.get("v", b.get("volume", 0))) for b in bars]
        week52_high = max(highs)
        avg_vol = sum(vols) / len(vols) if vols else 0
        today_vol = vols[0] if vols else 0
        vol_ratio = today_vol / avg_vol if avg_vol > 0 else 1

        pct_from_high = (week52_high - price) / week52_high if week52_high > 0 else 1

        if pct_from_high <= 0.03 and vol_ratio >= 1.5:
            strength = min(1.0, (1.5 / pct_from_high * 0.01 + vol_ratio / 3) / 2)
            return TradingSignal(
                symbol=symbol,
                signal_type="MOMENTUM_BREAKOUT",
                direction="BUY",
                strength=round(min(strength, 0.95), 2),
                price=price,
                reasoning=(
                    f"{symbol} is {pct_from_high*100:.1f}% from 52W high "
                    f"with {vol_ratio:.1f}x average volume — breakout signal."
                ),
                suggested_size_usd=self.portfolio_value * 0.03,
                metadata={"pct_from_52w_high": pct_from_high, "vol_ratio": vol_ratio},
            )
        return None

    def _oversold_rebound(self, symbol: str, price: float, bars: list[dict],
                          cost_basis: Optional[float] = None) -> Optional[TradingSignal]:
        """
        Contrarian BUY signal: price significantly below 52W high AND 20-day low.
        Flags potential mean-reversion opportunities (like PG in the sample portfolio).
        """
        if len(bars) < 20:
            return None

        highs  = [float(b.get("h", b.get("high", price))) for b in bars]
        closes = [float(b.get("c", b.get("close", price))) for b in bars]
        week52_high = max(highs)
        low_20d = min(closes[:20])

        pct_from_high = (week52_high - price) / week52_high if week52_high > 0 else 0
        at_20d_low = price <= low_20d * 1.02

        threshold = abs(config.signal.oversold_threshold)  # e.g. 0.15
        if pct_from_high >= threshold and at_20d_low:
            # Extra signal if we're below our own cost basis
            below_cost = cost_basis and price < cost_basis
            strength = min(0.75, pct_from_high * 2)
            if below_cost:
                strength = min(strength + 0.10, 0.85)
            return TradingSignal(
                symbol=symbol,
                signal_type="OVERSOLD_REBOUND",
                direction="BUY",
                strength=round(strength, 2),
                price=price,
                reasoning=(
                    f"{symbol} is {pct_from_high*100:.1f}% below 52W high "
                    f"and at a 20-day low — potential mean-reversion."
                    + (f" Currently {(price/cost_basis-1)*100:.1f}% vs cost basis." if cost_basis else "")
                ),
                suggested_size_usd=self.portfolio_value * 0.02,
                metadata={"pct_from_52w_high": pct_from_high, "at_20d_low": at_20d_low},
            )
        return None

    def _volume_spike(self, symbol: str, price: float, prev_close: float,
                      bars: list[dict]) -> Optional[TradingSignal]:
        """
        Unusual volume + directional move → something is happening.
        High vol + up price → BUY signal. High vol + down price → SELL signal.
        """
        if len(bars) < 10:
            return None

        vols = [float(b.get("v", b.get("volume", 0))) for b in bars]
        avg_vol = sum(vols[1:]) / (len(vols) - 1) if len(vols) > 1 else 0
        today_vol = vols[0] if vols else 0
        vol_ratio = today_vol / avg_vol if avg_vol > 0 else 1

        if vol_ratio < config.signal.volume_spike_threshold:
            return None

        price_chg = (price - prev_close) / prev_close if prev_close > 0 else 0
        if abs(price_chg) < 0.02:  # need at least 2% move
            return None

        direction = "BUY" if price_chg > 0 else "SELL"
        strength = min(0.85, vol_ratio / 5 + abs(price_chg) * 3)
        return TradingSignal(
            symbol=symbol,
            signal_type="VOLUME_SPIKE",
            direction=direction,
            strength=round(strength, 2),
            price=price,
            reasoning=(
                f"{symbol} volume is {vol_ratio:.1f}x average with "
                f"price {price_chg*100:+.1f}% — unusual activity."
            ),
            suggested_size_usd=self.portfolio_value * 0.025,
            metadata={"vol_ratio": vol_ratio, "price_chg": price_chg},
        )

    def _rebalance_signal(self, symbol: str, price: float,
                          current_weight: float, target_weight: float) -> Optional[TradingSignal]:
        """
        Generate a rebalance signal when position weight has drifted
        more than `rebalance_drift_threshold` from target.
        """
        drift = current_weight - target_weight
        threshold = config.signal.rebalance_drift_threshold

        if abs(drift) < threshold:
            return None

        direction = "SELL" if drift > 0 else "BUY"
        trim_size = abs(drift) * self.portfolio_value
        return TradingSignal(
            symbol=symbol,
            signal_type="REBALANCE",
            direction=direction,
            strength=min(0.9, abs(drift) / threshold * 0.5),
            price=price,
            reasoning=(
                f"{symbol} weight {current_weight*100:.1f}% vs target {target_weight*100:.1f}% "
                f"— drift of {drift*100:+.1f}% exceeds threshold."
            ),
            suggested_size_usd=trim_size,
            metadata={"current_weight": current_weight, "target_weight": target_weight, "drift": drift},
        )

    def scan(
        self,
        symbol: str,
        price: float,
        prev_close: float,
        bars: list[dict],
        cost_basis: Optional[float] = None,
        current_weight: float = 0.0,
        target_weight: float = 0.0,
    ) -> list[TradingSignal]:
        """
        Run all signal detectors for one symbol.
        Returns a list of signals sorted by strength.
        """
        signals: list[TradingSignal] = []

        for detector in [
            lambda: self._momentum_breakout(symbol, price, bars),
            lambda: self._volume_spike(symbol, price, prev_close, bars),
            lambda: self._oversold_rebound(symbol, price, bars, cost_basis),
            lambda: self._rebalance_signal(symbol, price, current_weight, target_weight),
        ]:
            try:
                sig = detector()
                if sig:
                    signals.append(sig)
            except Exception as exc:
                logger.debug("Signal detector error for %s: %s", symbol, exc)

        signals.sort(key=lambda s: s.strength, reverse=True)
        if signals:
            logger.info(
                "Signals for %s: %s",
                symbol,
                [(s.signal_type, s.direction, f"{s.strength:.2f}") for s in signals],
            )
        return signals
