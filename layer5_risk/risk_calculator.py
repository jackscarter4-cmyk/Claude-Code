"""
Layer 5 — Offline risk calculator.

Pure-logic position sizing and portfolio risk checks. No broker, no network.
Given a scored stock, your portfolio value, and current holdings, it computes:

  - Recommended position size (by confidence: HIGH=5%, MEDIUM=3%)
  - Stop-loss price (-8% from entry, configurable)
  - Single-stock concentration check (max 8% per name)
  - Sector concentration check (max 40% per sector)
  - Total-deployment check (max 80% of portfolio)

Returns a RiskDecision describing whether the trade is within limits and
the sized recommendation — advisory only, since nothing trades live.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from config.settings import config

logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    symbol: str
    approved: bool
    side: str                       # "BUY" | "SELL" | "HOLD"
    size_usd: float = 0.0
    shares: float = 0.0
    stop_loss_price: Optional[float] = None
    reasons: list = field(default_factory=list)


class RiskCalculator:
    """Stateless risk sizing and limit checks over a snapshot of holdings."""

    def __init__(self, risk_config=None):
        self.cfg = risk_config or config.risk

    def _size_from_confidence(self, confidence: str, portfolio_value: float) -> float:
        alloc = {"HIGH": 0.05, "MEDIUM": 0.03, "LOW": 0.0}.get(confidence, 0.0)
        return round(portfolio_value * alloc, 2)

    def evaluate(
        self,
        result,
        portfolio_value: float,
        positions: Optional[dict] = None,
        sector_by_symbol: Optional[dict] = None,
    ) -> RiskDecision:
        """
        Evaluate a ScoreResult against risk limits.

        Args:
            result:          a ScoreResult (has .symbol, .verdict, .confidence,
                             .price, .sector, .shares_held).
            portfolio_value: total portfolio value in USD.
            positions:       {symbol: market_value} for current holdings.
            sector_by_symbol:{symbol: sector} to compute sector concentration.
        """
        positions = positions or {}
        sector_by_symbol = sector_by_symbol or {}
        reasons: list[str] = []

        verdict = result.verdict
        symbol = result.symbol

        # Only BUY/SELL verdicts produce sized actions
        if verdict in ("HOLD",):
            return RiskDecision(symbol=symbol, approved=False, side="HOLD",
                                reasons=["Neutral verdict — no action"])

        if verdict in ("SELL", "AVOID"):
            held = result.shares_held or 0.0
            if held <= 0:
                return RiskDecision(symbol=symbol, approved=False, side="SELL",
                                    reasons=["Bearish but no position to sell"])
            return RiskDecision(
                symbol=symbol, approved=True, side="SELL",
                shares=held, size_usd=round(held * result.price, 2),
                reasons=[f"{verdict} verdict — trim/exit {held:g} shares"],
            )

        # BUY path
        if portfolio_value <= 0:
            return RiskDecision(symbol=symbol, approved=False, side="BUY",
                                reasons=["Portfolio value unknown"])

        size_usd = self._size_from_confidence(result.confidence, portfolio_value)
        if size_usd <= 0:
            return RiskDecision(symbol=symbol, approved=False, side="BUY",
                                reasons=["LOW confidence — size is zero"])

        # Single-stock concentration
        existing = float(positions.get(symbol, 0.0))
        max_single = portfolio_value * self.cfg.max_single_stock_fraction
        if existing + size_usd > max_single:
            size_usd = max(0.0, round(max_single - existing, 2))
            reasons.append(
                f"Capped to single-stock limit ({self.cfg.max_single_stock_fraction*100:.0f}%)"
            )
        if size_usd <= 0:
            return RiskDecision(symbol=symbol, approved=False, side="BUY",
                                reasons=["Already at single-stock limit"])

        # Total deployment
        deployed = sum(float(v) for v in positions.values())
        max_deployed = portfolio_value * self.cfg.max_deployed_fraction
        if deployed + size_usd > max_deployed:
            return RiskDecision(symbol=symbol, approved=False, side="BUY",
                                reasons=[f"Would exceed max deployment "
                                         f"({self.cfg.max_deployed_fraction*100:.0f}%)"])

        # Sector concentration
        sector = result.sector or sector_by_symbol.get(symbol)
        if sector:
            sector_value = sum(
                float(mv) for sym, mv in positions.items()
                if (sector_by_symbol.get(sym) or "") == sector
            )
            projected = (sector_value + size_usd) / portfolio_value
            if projected > self.cfg.max_sector_concentration:
                return RiskDecision(symbol=symbol, approved=False, side="BUY",
                                    reasons=[f"Sector '{sector}' would reach "
                                             f"{projected*100:.0f}% (limit "
                                             f"{self.cfg.max_sector_concentration*100:.0f}%)"])

        stop = round(result.price * (1.0 - self.cfg.stop_loss_pct), 2)
        shares = round(size_usd / result.price, 4) if result.price > 0 else 0.0
        reasons.insert(0, f"{result.confidence} confidence → {size_usd:g} USD")

        return RiskDecision(
            symbol=symbol, approved=True, side="BUY",
            size_usd=size_usd, shares=shares, stop_loss_price=stop,
            reasons=reasons,
        )
