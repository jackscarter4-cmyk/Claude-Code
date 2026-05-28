"""
Layer 3 — Offline quantitative stock scorer.

Replaces the Claude-based analyzer with a fully offline 5-factor model
(Alpha Picks / Seeking Alpha Quant style). No API keys, no network.

Scoring methodology:
  - Each factor is scored 0-10 via winsorized z-score normalization
    (Fama-French / MSCI Barra USE4 standard: clip at 3σ before scoring).
  - Factors: Value (HML-aligned), Growth (CMA-aligned), Profitability (RMW),
    EPS Revisions (Chan, Jegadeesh & Lakonishok 1996), Price Momentum
    (Jegadeesh & Titman 1993 — 12-1 month skip-month return).
  - Composite is the equal-weighted average of available factor scores.
  - Verdict: composite ≥ 6.5 → BUY, ≤ 3.5 → SELL/AVOID.
    Red-flag cap: growth ≤ 3.0 OR momentum ≤ 3.0 blocks BUY regardless.

Shared by `daily_check.py` (CLI screener), `main.py`, and `serve.py` (web UI).
"""

import math
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class StockData:
    symbol: str
    price: float

    # Valuation (maps to Fama-French HML factor)
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    pb_ratio: Optional[float] = None
    ev_ebitda: Optional[float] = None

    # Growth (maps to Fama-French CMA factor, inverted)
    revenue_growth_yoy: Optional[float] = None
    eps_growth_yoy: Optional[float] = None
    eps_growth_3y: Optional[float] = None

    # Profitability (maps to Fama-French RMW factor)
    roe: Optional[float] = None
    roic: Optional[float] = None
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None

    # EPS Revisions (Chan, Jegadeesh & Lakonishok 1996)
    eps_rev_30d: Optional[float] = None
    eps_rev_60d: Optional[float] = None
    eps_rev_90d: Optional[float] = None

    # Momentum (Jegadeesh & Titman 1993 — 12-1 month skip-month)
    price_12m_ago: Optional[float] = None   # price 12 months ago (formation start)
    price_1m_ago: Optional[float] = None    # price 1 month ago (skip most recent month)
    rsi_14: Optional[float] = None          # Wilder (1978) 14-day RSI

    # Context
    sector: Optional[str] = None
    market_cap_b: Optional[float] = None
    dividend_yield: Optional[float] = None
    cost_basis: Optional[float] = None
    shares_held: Optional[float] = None
    notes: Optional[str] = None

    # Layer 2 signal inputs (basic — all optional)
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    low_20d: Optional[float] = None
    avg_volume: Optional[float] = None
    current_volume: Optional[float] = None
    prev_close: Optional[float] = None
    price_2d_ago: Optional[float] = None
    earnings_date: Optional[str] = None
    target_weight: Optional[float] = None

    # Risk quantification (enables Kelly Criterion + VaR in Layer 5)
    annual_volatility: Optional[float] = None  # annualized σ, e.g. 0.25 = 25%
    beta: Optional[float] = None               # market beta vs S&P 500
    atr_14: Optional[float] = None             # 14-day Average True Range ($ per share)

    # Enhanced Layer 2 signals (Bollinger Bands, VWAP, OBV)
    sma_20: Optional[float] = None             # 20-day simple moving average
    bollinger_upper: Optional[float] = None    # SMA_20 + 2σ_20 upper band
    bollinger_lower: Optional[float] = None    # SMA_20 − 2σ_20 lower band
    obv_trend: Optional[float] = None         # OBV slope: + = accumulation, − = distribution
    vwap: Optional[float] = None              # session VWAP (institutional reference price)


@dataclass
class ScoreResult:
    symbol: str
    price: float
    sector: Optional[str]

    score_value: Optional[float] = None
    score_growth: Optional[float] = None
    score_profitability: Optional[float] = None
    score_eps_revisions: Optional[float] = None
    score_momentum: Optional[float] = None
    composite: Optional[float] = None

    # Sub-metrics for report detail
    momentum_12_1: Optional[float] = None
    rsi_14: Optional[float] = None
    roe: Optional[float] = None
    net_margin: Optional[float] = None
    eps_rev_30d: Optional[float] = None
    revenue_growth_yoy: Optional[float] = None

    cost_basis: Optional[float] = None
    shares_held: Optional[float] = None
    notes: Optional[str] = None

    # Passed through for Layer 5 risk sizing (Kelly + VaR)
    annual_volatility: Optional[float] = None
    atr_14: Optional[float] = None

    verdict: str = "HOLD"
    confidence: str = "LOW"


# ---------------------------------------------------------------------------
# Quant scorer
# ---------------------------------------------------------------------------

class QuantScorer:
    """
    Implements the Alpha Picks / Seeking Alpha Quant style 5-factor framework.

    Each factor is scored 0-10 using winsorized z-score normalization:
      1. Clip values at μ ± 3σ (MSCI Barra USE4 / Fama-French standard).
         This prevents a single extreme outlier (e.g. a negative P/E) from
         compressing every other stock's score.
      2. Recompute μ and σ on the winsorized universe.
      3. Map z-score to [0, 10]: z=-3 → 0, z=0 → 5, z=+3 → 10.

    Factors align with Fama-French 5-factor terminology:
      Value         → HML (Book-to-Market proxy)
      Growth        → CMA-inverted (asset growth proxy)
      Profitability → RMW (operating profitability proxy)
      EPS Revisions → Chan, Jegadeesh & Lakonishok (1996) revision signal
      Momentum      → Jegadeesh & Titman (1993) 12-1 month cross-sectional rank
    """

    def _safe(self, values: list) -> list:
        return [v for v in values if v is not None]

    def _mean(self, values: list) -> float:
        v = self._safe(values)
        return sum(v) / len(v) if v else 0.0

    def _std(self, values: list) -> float:
        v = self._safe(values)
        if len(v) < 2:
            return 1.0
        m = self._mean(v)
        variance = sum((x - m) ** 2 for x in v) / len(v)
        return math.sqrt(variance) or 1.0

    def _zscore_to_score(self, value: float, universe: list, invert: bool = False) -> float:
        """
        0-10 score via winsorized z-score normalization.

        Winsorizes at 3σ (MSCI Barra USE4 standard) before computing z to
        prevent extreme outliers from distorting peer comparisons.
        invert=True for metrics where lower is better (P/E, P/B, EV/EBITDA).
        """
        u = self._safe(universe)
        if not u:
            return 5.0
        mu = self._mean(u)
        sigma = self._std(u)
        # Winsorize at 3σ
        lo, hi = mu - 3.0 * sigma, mu + 3.0 * sigma
        value_w = max(lo, min(hi, value))
        u_w = [max(lo, min(hi, x)) for x in u]
        # Recompute on winsorized universe
        mu_w = self._mean(u_w)
        sigma_w = self._std(u_w)
        z = (value_w - mu_w) / sigma_w
        if invert:
            z = -z
        # Map z-score to 0-10: z=-3 → 0, z=0 → 5, z=+3 → 10
        score = 5.0 + (z * 5.0 / 3.0)
        return max(0.0, min(10.0, score))

    def _score_value(self, stock: StockData, universe: list[StockData]) -> Optional[float]:
        """Lower valuation multiples relative to peers → higher score."""
        parts = []

        if stock.pe_ratio is not None and stock.pe_ratio > 0:
            all_pe = [s.pe_ratio for s in universe if s.pe_ratio and s.pe_ratio > 0]
            parts.append(self._zscore_to_score(stock.pe_ratio, all_pe, invert=True))

        if stock.forward_pe is not None and stock.forward_pe > 0:
            all_fpe = [s.forward_pe for s in universe if s.forward_pe and s.forward_pe > 0]
            parts.append(self._zscore_to_score(stock.forward_pe, all_fpe, invert=True))

        if stock.pb_ratio is not None and stock.pb_ratio > 0:
            all_pb = [s.pb_ratio for s in universe if s.pb_ratio and s.pb_ratio > 0]
            parts.append(self._zscore_to_score(stock.pb_ratio, all_pb, invert=True))

        if stock.ev_ebitda is not None and stock.ev_ebitda > 0:
            all_ev = [s.ev_ebitda for s in universe if s.ev_ebitda and s.ev_ebitda > 0]
            parts.append(self._zscore_to_score(stock.ev_ebitda, all_ev, invert=True))

        if not parts:
            return None
        return round(sum(parts) / len(parts), 2)

    def _score_growth(self, stock: StockData, universe: list[StockData]) -> Optional[float]:
        """Higher revenue and EPS growth → higher score."""
        parts = []

        if stock.revenue_growth_yoy is not None:
            all_rev = [s.revenue_growth_yoy for s in universe if s.revenue_growth_yoy is not None]
            parts.append(self._zscore_to_score(stock.revenue_growth_yoy, all_rev))

        if stock.eps_growth_yoy is not None:
            all_eps = [s.eps_growth_yoy for s in universe if s.eps_growth_yoy is not None]
            parts.append(self._zscore_to_score(stock.eps_growth_yoy, all_eps))

        if stock.eps_growth_3y is not None:
            all_3y = [s.eps_growth_3y for s in universe if s.eps_growth_3y is not None]
            parts.append(self._zscore_to_score(stock.eps_growth_3y, all_3y))

        if not parts:
            return None
        return round(sum(parts) / len(parts), 2)

    def _score_profitability(self, stock: StockData, universe: list[StockData]) -> Optional[float]:
        """Higher ROE, ROIC, margins vs peers → higher score."""
        parts = []

        if stock.roe is not None:
            all_roe = [s.roe for s in universe if s.roe is not None]
            parts.append(self._zscore_to_score(stock.roe, all_roe))

        if stock.roic is not None:
            all_roic = [s.roic for s in universe if s.roic is not None]
            parts.append(self._zscore_to_score(stock.roic, all_roic))

        if stock.gross_margin is not None:
            all_gm = [s.gross_margin for s in universe if s.gross_margin is not None]
            parts.append(self._zscore_to_score(stock.gross_margin, all_gm))

        if stock.net_margin is not None:
            all_nm = [s.net_margin for s in universe if s.net_margin is not None]
            parts.append(self._zscore_to_score(stock.net_margin, all_nm))

        if not parts:
            return None
        return round(sum(parts) / len(parts), 2)

    def _score_eps_revisions(self, stock: StockData, universe: list[StockData]) -> Optional[float]:
        """
        Positive analyst estimate revisions → higher score.
        30-day revisions get highest weight (most predictive).
        60d and 90d get lower weight.
        """
        parts = []
        weights = []

        if stock.eps_rev_30d is not None:
            all_30 = [s.eps_rev_30d for s in universe if s.eps_rev_30d is not None]
            parts.append(self._zscore_to_score(stock.eps_rev_30d, all_30))
            weights.append(0.5)

        if stock.eps_rev_60d is not None:
            all_60 = [s.eps_rev_60d for s in universe if s.eps_rev_60d is not None]
            parts.append(self._zscore_to_score(stock.eps_rev_60d, all_60))
            weights.append(0.3)

        if stock.eps_rev_90d is not None:
            all_90 = [s.eps_rev_90d for s in universe if s.eps_rev_90d is not None]
            parts.append(self._zscore_to_score(stock.eps_rev_90d, all_90))
            weights.append(0.2)

        if not parts:
            return None

        total_weight = sum(weights)
        weighted = sum(p * w for p, w in zip(parts, weights)) / total_weight
        return round(weighted, 2)

    def _score_momentum(self, stock: StockData, universe: list[StockData]) -> Optional[float]:
        """
        12-1 month momentum: return from 12 months ago to 1 month ago.
        Excludes last month (short-term reversal bias).
        RSI secondary component.
        """
        parts = []
        weights = []

        if stock.price_12m_ago and stock.price_1m_ago and stock.price_12m_ago > 0:
            # 12-1 month momentum = (price_1m_ago / price_12m_ago) - 1
            momentum_12_1 = (stock.price_1m_ago / stock.price_12m_ago) - 1.0
            all_mom = []
            for s in universe:
                if s.price_12m_ago and s.price_1m_ago and s.price_12m_ago > 0:
                    all_mom.append((s.price_1m_ago / s.price_12m_ago) - 1.0)
            parts.append(self._zscore_to_score(momentum_12_1, all_mom))
            weights.append(0.7)

        if stock.rsi_14 is not None:
            # RSI: oversold (<30) is bullish opportunity, overbought (>70) is bearish.
            # Institutions buy momentum INTO strength but avoid extreme overbought.
            # Optimal RSI range is 50-65 (score ~6-7).
            rsi = stock.rsi_14
            if rsi <= 30:
                rsi_score = 7.5  # Oversold — potential reversal
            elif rsi <= 50:
                rsi_score = 4.0 + (rsi - 30) * 0.15
            elif rsi <= 65:
                rsi_score = 7.0 + (rsi - 50) * 0.067  # Sweet spot
            elif rsi <= 80:
                rsi_score = 8.0 - (rsi - 65) * 0.27   # Overbought caution
            else:
                rsi_score = 4.0  # Extreme overbought
            parts.append(max(0.0, min(10.0, rsi_score)))
            weights.append(0.3)

        if not parts:
            return None

        total_weight = sum(weights)
        weighted = sum(p * w for p, w in zip(parts, weights)) / total_weight
        return round(weighted, 2)

    def score(self, stocks: list[StockData]) -> list[ScoreResult]:
        results = []
        for stock in stocks:
            sv = self._score_value(stock, stocks)
            sg = self._score_growth(stock, stocks)
            sp = self._score_profitability(stock, stocks)
            se = self._score_eps_revisions(stock, stocks)
            sm = self._score_momentum(stock, stocks)

            available = [s for s in [sv, sg, sp, se, sm] if s is not None]
            composite = round(sum(available) / len(available), 2) if available else None

            # Momentum (12-1)
            mom_12_1 = None
            if stock.price_12m_ago and stock.price_1m_ago and stock.price_12m_ago > 0:
                mom_12_1 = round((stock.price_1m_ago / stock.price_12m_ago - 1) * 100, 1)

            verdict, confidence = verdict_from_scores(sv, sg, sp, se, sm, composite)

            results.append(ScoreResult(
                symbol=stock.symbol,
                price=stock.price,
                sector=stock.sector,
                score_value=sv,
                score_growth=sg,
                score_profitability=sp,
                score_eps_revisions=se,
                score_momentum=sm,
                composite=composite,
                momentum_12_1=mom_12_1,
                rsi_14=stock.rsi_14,
                roe=stock.roe,
                net_margin=stock.net_margin,
                eps_rev_30d=stock.eps_rev_30d,
                revenue_growth_yoy=stock.revenue_growth_yoy,
                cost_basis=stock.cost_basis,
                shares_held=stock.shares_held,
                notes=stock.notes,
                annual_volatility=stock.annual_volatility,
                atr_14=stock.atr_14,
                verdict=verdict,
                confidence=confidence,
            ))

        results.sort(key=lambda r: r.composite or 0, reverse=True)
        return results


def verdict_from_scores(sv, sg, sp, se, sm, composite) -> tuple[str, str]:
    """
    Derive a BUY/SELL/HOLD/AVOID verdict and HIGH/MEDIUM/LOW confidence.

    Buy threshold:  composite >= 6.5
    Sell threshold: composite <= 3.5
    Red flags: growth <= 3 OR momentum <= 3 caps at HOLD (Seeking Alpha rule)
    """
    if composite is None:
        return "HOLD", "LOW"

    # Red-flag caps (mirrors SA: D+ or worse on key factors → no buy)
    has_red_flag = (sg is not None and sg <= 3.0) or (sm is not None and sm <= 3.0)

    if composite >= 7.5 and not has_red_flag:
        return "BUY", "HIGH"
    elif composite >= 6.5 and not has_red_flag:
        return "BUY", "MEDIUM"
    elif composite <= 2.5:
        return "AVOID", "HIGH"
    elif composite <= 3.5:
        return "SELL", "MEDIUM"
    elif composite <= 4.5:
        return "HOLD", "LOW"
    else:
        return "HOLD", "MEDIUM"
