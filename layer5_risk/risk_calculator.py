"""
Layer 5 — Offline risk calculator.

Pure-logic position sizing and portfolio risk checks. No broker, no network.
Given a scored stock, portfolio value, and current holdings, computes:

  Position sizing (best available method in priority order):
    1. Fractional Kelly Criterion (Kelly 1956; Thorp 1969) when annual_volatility
       is provided: f* = μ/σ² then half-Kelly applied as standard discount.
    2. Confidence-tier fixed allocation as fallback (HIGH=5%, MEDIUM=3%).

  Stop-loss placement:
    - ATR-based: price − 2×ATR_14 (Wilder 1978) when atr_14 is available.
    - Fixed pct:  price × (1 − stop_loss_pct) otherwise.

  Risk analytics (when annual_volatility provided):
    - 1-day parametric VaR at 95% confidence (Jorion 2006, Basel II).
    - 1-day CVaR / Expected Shortfall at 95% (Artzner et al. 1999).
      CVaR is a coherent risk measure and answers "average loss given we
      exceed VaR", which VaR alone cannot answer.

  Portfolio constraints:
    - Single-stock cap: max 8% of portfolio per name.
    - Sector cap:       max 40% per GICS sector.
    - Deployment cap:   max 80% total invested.

All output is advisory only — nothing trades live.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from config.settings import config

logger = logging.getLogger(__name__)

# Standard-normal quantiles z_α for common confidence levels
_Z_QUANTILE = {0.90: 1.282, 0.95: 1.645, 0.99: 2.326}

# Standard-normal PDF φ(z_α) values used in CVaR formula
# φ(z) = exp(-z²/2) / √(2π)
_PHI_AT_Z = {0.90: 0.1755, 0.95: 0.1031, 0.99: 0.0267}


@dataclass
class RiskDecision:
    symbol: str
    approved: bool
    side: str                        # "BUY" | "SELL" | "HOLD"
    size_usd: float = 0.0
    shares: float = 0.0
    stop_loss_price: Optional[float] = None
    reasons: list = field(default_factory=list)
    # Risk analytics (populated when annual_volatility is provided)
    kelly_fraction: Optional[float] = None   # half-Kelly f* as fraction of portfolio
    var_95_usd: Optional[float] = None       # 1-day 95% parametric VaR in USD
    cvar_95_usd: Optional[float] = None      # 1-day 95% CVaR (Expected Shortfall) in USD


class RiskCalculator:
    """Stateless risk sizing and limit checks over a snapshot of holdings."""

    def __init__(self, risk_config=None):
        self.cfg = risk_config or config.risk

    # ------------------------------------------------------------------
    # Kelly Criterion
    # ------------------------------------------------------------------

    def _expected_excess_return(self, verdict: str, confidence: str) -> float:
        """
        Map verdict/confidence to an annualized expected excess return estimate.
        Calibrated against historical AQR factor return quintile spreads.
        """
        lookup = {
            ("BUY",   "HIGH"):   0.15,
            ("BUY",   "MEDIUM"): 0.10,
            ("BUY",   "LOW"):    0.05,
            ("HOLD",  "MEDIUM"): 0.02,
            ("HOLD",  "LOW"):    0.00,
            ("SELL",  "MEDIUM"): -0.05,
            ("AVOID", "HIGH"):   -0.10,
        }
        return lookup.get((verdict, confidence), 0.0)

    def _kelly_fraction(
        self, expected_excess_return: float, annual_vol: float
    ) -> float:
        """
        Compute half-Kelly optimal fraction.

        Continuous-return Kelly formula (Thorp 1969):
            f* = μ / σ²
        Half-Kelly (standard practitioner discount):
            f_practical = f* / 2

        Half-Kelly reduces geometric-growth by only ~12% vs. full Kelly
        while cutting volatility and drawdown roughly in half.
        Bounded at max_single_stock_fraction regardless.
        """
        if annual_vol <= 0 or expected_excess_return <= 0:
            return 0.0
        full_kelly = expected_excess_return / (annual_vol ** 2)
        half_kelly = full_kelly / 2.0
        return max(0.0, min(self.cfg.max_single_stock_fraction, half_kelly))

    # ------------------------------------------------------------------
    # Parametric VaR and CVaR
    # ------------------------------------------------------------------

    def _parametric_var(
        self,
        size_usd: float,
        annual_vol: float,
        confidence: float = 0.95,
        horizon_days: int = 1,
    ) -> float:
        """
        1-day parametric VaR at given confidence level (zero-drift approximation).

        VaR_α = size × z_α × (σ_annual / √252) × √h

        Assumes normally distributed returns. The Basel II/III framework
        uses 99% 10-day VaR; for intraday risk management 95% 1-day is standard.
        Reference: Jorion (2006), "Value at Risk", Chapter 5.
        """
        z = _Z_QUANTILE.get(confidence, 1.645)
        daily_vol = annual_vol / math.sqrt(252)
        return size_usd * z * daily_vol * math.sqrt(horizon_days)

    def _parametric_cvar(
        self,
        size_usd: float,
        annual_vol: float,
        confidence: float = 0.95,
        horizon_days: int = 1,
    ) -> float:
        """
        1-day parametric CVaR / Expected Shortfall under normality.

        ES_α = size × φ(z_α) / (1−α) × (σ_annual / √252) × √h

        CVaR is a coherent risk measure (Artzner et al. 1999) satisfying
        subadditivity — unlike VaR, it cannot increase when positions are
        merged. It answers: "given we do exceed VaR, what is the average loss?"

        At 95% confidence: CVaR ≈ 2.06 × 1-day σ × position_size
        (versus VaR ≈ 1.645 × 1-day σ × position_size).
        """
        phi = _PHI_AT_Z.get(confidence, 0.1031)
        daily_vol = annual_vol / math.sqrt(252)
        return size_usd * (phi / (1.0 - confidence)) * daily_vol * math.sqrt(horizon_days)

    # ------------------------------------------------------------------
    # Stop-loss placement
    # ------------------------------------------------------------------

    def _stop_loss_price(self, price: float, atr_14: Optional[float]) -> float:
        """
        ATR-based trailing stop when ATR_14 is available (Wilder 1978):
            stop = price − 2 × ATR_14

        Adapts stop width to the stock's own volatility regime — tight for
        stable names, wider for volatile ones. Falls back to fixed pct stop.
        """
        if atr_14 and atr_14 > 0:
            return round(max(price - 2.0 * atr_14, 0.01), 2)
        return round(price * (1.0 - self.cfg.stop_loss_pct), 2)

    # ------------------------------------------------------------------
    # Confidence-based fallback sizing
    # ------------------------------------------------------------------

    def _size_from_confidence(self, confidence: str, portfolio_value: float) -> float:
        alloc = {"HIGH": 0.05, "MEDIUM": 0.03, "LOW": 0.0}.get(confidence, 0.0)
        return round(portfolio_value * alloc, 2)

    # ------------------------------------------------------------------
    # Main evaluation
    # ------------------------------------------------------------------

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
            result:           ScoreResult — needs .symbol, .verdict, .confidence,
                              .price, .sector, .shares_held.
                              Optional: .annual_volatility, .atr_14.
            portfolio_value:  Total portfolio value in USD.
            positions:        {symbol: market_value} for current holdings.
            sector_by_symbol: {symbol: sector} for sector concentration check.
        """
        positions = positions or {}
        sector_by_symbol = sector_by_symbol or {}
        reasons: list[str] = []

        verdict = result.verdict
        symbol = result.symbol

        if verdict == "HOLD":
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

        # Position sizing: fractional Kelly preferred, confidence tier as fallback
        annual_vol = getattr(result, "annual_volatility", None)
        atr_14 = getattr(result, "atr_14", None)
        kelly_frac: Optional[float] = None

        if annual_vol and annual_vol > 0:
            mu = self._expected_excess_return(verdict, result.confidence)
            kelly_frac = self._kelly_fraction(mu, annual_vol)
            if kelly_frac > 0:
                size_usd = round(portfolio_value * kelly_frac, 2)
                reasons.append(
                    f"½-Kelly: f*={kelly_frac*100:.1f}% "
                    f"(μ={mu*100:.0f}% / σ²={annual_vol**2*100:.1f}%²) → ${size_usd:,.0f}"
                )
            else:
                size_usd = self._size_from_confidence(result.confidence, portfolio_value)
                reasons.append("Kelly→0 (low expected return); using confidence tier")
        else:
            size_usd = self._size_from_confidence(result.confidence, portfolio_value)
            reasons.append(f"{result.confidence} confidence → ${size_usd:,.0f}")

        if size_usd <= 0:
            return RiskDecision(symbol=symbol, approved=False, side="BUY",
                                reasons=["LOW confidence — size is zero"])

        # Single-stock concentration check
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

        # Total deployment check
        deployed = sum(float(v) for v in positions.values())
        max_deployed = portfolio_value * self.cfg.max_deployed_fraction
        if deployed + size_usd > max_deployed:
            return RiskDecision(symbol=symbol, approved=False, side="BUY",
                                reasons=[f"Would exceed max deployment "
                                         f"({self.cfg.max_deployed_fraction*100:.0f}%)"])

        # Sector concentration check
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

        stop = self._stop_loss_price(result.price, atr_14)
        shares = round(size_usd / result.price, 4) if result.price > 0 else 0.0

        stop_method = "2×ATR" if (atr_14 and atr_14 > 0) else f"{self.cfg.stop_loss_pct*100:.0f}%"
        reasons.append(f"Stop-loss: ${stop:.2f} ({stop_method})")

        # Risk analytics (parametric, normality assumed)
        var_95 = None
        cvar_95 = None
        if annual_vol and annual_vol > 0:
            var_95 = round(self._parametric_var(size_usd, annual_vol, 0.95), 2)
            cvar_95 = round(self._parametric_cvar(size_usd, annual_vol, 0.95), 2)

        return RiskDecision(
            symbol=symbol, approved=True, side="BUY",
            size_usd=size_usd, shares=shares, stop_loss_price=stop,
            reasons=reasons,
            kelly_fraction=kelly_frac,
            var_95_usd=var_95,
            cvar_95_usd=cvar_95,
        )
