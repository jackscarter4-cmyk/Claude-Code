"""
Layer 6 — Performance tracking and analysis for the stock trading engine.

Aggregates trade outcomes and portfolio NAV snapshots to compute:
  - Win rate, total P&L, per-sector and per-signal breakdowns
  - Sharpe Ratio     (Sharpe 1966, 1994) — annualized risk-adjusted return
  - Sortino Ratio    (Sortino & van der Meer 1991) — downside-risk-only Sharpe
  - Maximum Drawdown (MDD) — worst peak-to-trough decline
  - Calmar Ratio     (Young 1991) — CAGR ÷ |MDD|; adapts to non-normal return
  - CAGR             (GIPS standard) — compound annual growth rate
  - BHB Attribution  (Brinson, Hood & Beebower 1986) — sector allocation vs.
                       stock selection decomposition

All ratio calculations require at least 3 portfolio NAV snapshots stored in
the portfolio_snapshots table via main.py's persistence path.
"""

import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from layer1_data.database import Database

logger = logging.getLogger(__name__)

# Annualized risk-free rate assumption (approximate 3-month T-bill)
_RISK_FREE_ANNUAL = 0.045
# Minimum Acceptable Return for Sortino (usually 0 or R_f)
_SORTINO_MAR_ANNUAL = 0.0


# ---------------------------------------------------------------------------
# Portfolio ratio helpers (pure functions — no DB dependency)
# ---------------------------------------------------------------------------

def _returns_from_nav(nav_series: list[float]) -> list[float]:
    """Compute period-over-period returns from a NAV series."""
    return [(nav_series[i] - nav_series[i - 1]) / nav_series[i - 1]
            for i in range(1, len(nav_series))]


def compute_max_drawdown(nav_series: list[float]) -> float:
    """
    Maximum peak-to-trough drawdown.

    MDD = max_t [ (RunningMax_t - NAV_t) / RunningMax_t ]

    Reference: Magdon-Ismail & Atiya (2004), "Maximum Drawdown", Risk.
    """
    if len(nav_series) < 2:
        return 0.0
    peak = nav_series[0]
    mdd = 0.0
    for nav in nav_series:
        peak = max(peak, nav)
        dd = (peak - nav) / peak if peak > 0 else 0.0
        mdd = max(mdd, dd)
    return mdd


def compute_cagr(nav_series: list[float], timestamps: list[datetime]) -> float:
    """
    Compound Annual Growth Rate.

    CAGR = (V_T / V_0)^(365 / D) − 1

    Uses calendar days per GIPS (Global Investment Performance Standards).
    Returns simple total return when period < 365 days.
    """
    if len(nav_series) < 2 or nav_series[0] <= 0:
        return 0.0
    days = (timestamps[-1] - timestamps[0]).total_seconds() / 86400
    if days < 1:
        return 0.0
    total_return = nav_series[-1] / nav_series[0] - 1.0
    if days < 365:
        return total_return          # report raw return for sub-year periods
    return (nav_series[-1] / nav_series[0]) ** (365.0 / days) - 1.0


def compute_sharpe(returns: list[float], periods_per_year: float) -> float:
    """
    Annualized Sharpe Ratio (Sharpe 1966, 1994).

    S = (R_p − R_f) / σ_p   annualized via √(periods_per_year).

    Uses a fixed 4.5% annual risk-free rate approximation.
    For non-i.i.d. returns, the Lo (2002) autocorrelation correction
    should be applied; that requires longer series than typically available.
    """
    if len(returns) < 3:
        return 0.0
    rf_per_period = _RISK_FREE_ANNUAL / periods_per_year
    excess = [r - rf_per_period for r in returns]
    mean_e = sum(excess) / len(excess)
    var_e = sum((r - mean_e) ** 2 for r in excess) / len(excess)
    std_e = math.sqrt(var_e) if var_e > 0 else 0.0
    if std_e == 0:
        return 0.0
    return (mean_e / std_e) * math.sqrt(periods_per_year)


def compute_sortino(returns: list[float], periods_per_year: float) -> float:
    """
    Annualized Sortino Ratio (Sortino & van der Meer 1991).

    Sortino = (R_p − MAR) / σ_d   annualized

    where σ_d = sqrt((1/T) × Σ min(R_t − MAR, 0)²) is the downside deviation.
    Unlike Sharpe, does not penalize upside volatility — preferred for
    strategies with asymmetric return profiles.
    """
    if len(returns) < 3:
        return 0.0
    mar = _SORTINO_MAR_ANNUAL / periods_per_year
    mean_r = sum(returns) / len(returns)
    downside_sq = [(min(r - mar, 0.0)) ** 2 for r in returns]
    downside_var = sum(downside_sq) / len(returns)
    sigma_d = math.sqrt(downside_var) if downside_var > 0 else 0.0
    if sigma_d == 0:
        return 0.0
    return ((mean_r - mar) / sigma_d) * math.sqrt(periods_per_year)


def compute_calmar(cagr: float, mdd: float) -> Optional[float]:
    """
    Calmar Ratio (Young 1991).

    Calmar = CAGR / |MDD|

    Superior to Sharpe for strategies with infrequent but severe drawdowns
    (e.g. momentum strategies) because it uses MDD rather than std deviation,
    which counts upside and downside volatility equally.
    """
    if mdd <= 0:
        return None
    return round(cagr / mdd, 3)


def compute_portfolio_metrics(
    nav_series: list[float],
    timestamps: list[datetime],
) -> dict:
    """
    Compute all portfolio performance ratios from a NAV time series.

    Returns a dict with: cagr, sharpe_ratio, sortino_ratio, max_drawdown,
    calmar_ratio, total_return, annualized_vol, years, periods.

    Requires at least 3 observations. Returns empty dict otherwise.
    """
    if len(nav_series) < 3 or len(nav_series) != len(timestamps):
        return {}

    returns = _returns_from_nav(nav_series)
    days = max((timestamps[-1] - timestamps[0]).total_seconds() / 86400, 1)
    years = days / 365.25
    periods_per_year = len(returns) / max(years, 0.01)

    cagr = compute_cagr(nav_series, timestamps)
    mdd = compute_max_drawdown(nav_series)
    sharpe = compute_sharpe(returns, periods_per_year)
    sortino = compute_sortino(returns, periods_per_year)
    calmar = compute_calmar(cagr, mdd)

    # Annualized volatility
    mean_r = sum(returns) / len(returns)
    var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
    ann_vol = math.sqrt(var_r * periods_per_year)

    return {
        "cagr":           round(cagr, 4),
        "sharpe_ratio":   round(sharpe, 3),
        "sortino_ratio":  round(sortino, 3),
        "max_drawdown":   round(mdd, 4),
        "calmar_ratio":   calmar,
        "total_return":   round(nav_series[-1] / nav_series[0] - 1, 4),
        "annualized_vol": round(ann_vol, 4),
        "years":          round(years, 2),
        "periods":        len(returns),
    }


# ---------------------------------------------------------------------------
# PerformanceTracker
# ---------------------------------------------------------------------------

class PerformanceTracker:
    """
    Analyses historical trade data and portfolio snapshots to surface
    systematic edge, risk metrics, and attribution.
    """

    def __init__(self, db: Database):
        self.db = db

    def _get_nav_series(self) -> tuple[list[float], list[datetime]]:
        """
        Pull portfolio NAV history from portfolio_snapshots table.
        Returns (nav_values, timestamps) sorted chronologically.
        """
        cur = self.db._conn.cursor()
        rows = cur.execute(
            "SELECT ts, total_value FROM portfolio_snapshots "
            "WHERE total_value > 0 ORDER BY ts ASC"
        ).fetchall()
        navs, times = [], []
        for row in rows:
            try:
                ts = datetime.fromisoformat(row["ts"]).replace(tzinfo=timezone.utc)
                navs.append(float(row["total_value"]))
                times.append(ts)
            except (ValueError, TypeError):
                continue
        return navs, times

    def compute_stats(self, days: int = 30) -> dict:
        """
        Compute comprehensive performance statistics over the last N days.

        Returns:
          overall:          win_rate, total_pnl, num_trades, avg_pnl_per_trade
          portfolio_metrics: sharpe, sortino, calmar, mdd, cagr (from NAV history)
          by_sector:        per-sector breakdown
          by_signal_type:   breakdown by Layer 2 strategy tag
          by_confidence:    breakdown at each confidence level
          calibration:      AI target_price vs actual outcome
          best/worst:       top/bottom 3 stocks by PnL
        """
        trades = self.db.get_trade_history(limit=5000)
        analyses = self.db.get_analyses_for_review(days=days)

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()
        completed = [
            t for t in trades
            if t.get("outcome") in ("WIN", "LOSS")
            and (t.get("placed_at", "") >= cutoff)
        ]

        if not completed:
            base = {"status": "no_completed_trades", "days": days}
            # Still try to compute portfolio metrics if NAV history exists
            navs, times = self._get_nav_series()
            if len(navs) >= 3:
                base["portfolio_metrics"] = compute_portfolio_metrics(navs, times)
            return base

        wins = [t for t in completed if t.get("outcome") == "WIN"]
        total_pnl = sum(float(t.get("pnl_usd", 0) or 0) for t in completed)
        win_rate = len(wins) / len(completed)

        # By sector
        sector_stats: dict = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
        for t in completed:
            sym = t.get("symbol", "")
            entry = self.db.get_watchlist_entry(sym)
            sector = (entry or {}).get("sector", "unknown") or "unknown"
            sector_stats[sector]["trades"] += 1
            if t.get("outcome") == "WIN":
                sector_stats[sector]["wins"] += 1
            sector_stats[sector]["pnl"] += float(t.get("pnl_usd", 0) or 0)
        for s in sector_stats:
            n = sector_stats[s]["trades"]
            sector_stats[s]["win_rate"] = round(sector_stats[s]["wins"] / n, 3) if n > 0 else 0

        # By signal type
        signal_stats: dict = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
        for t in completed:
            if t.get("layer") != "layer2":
                continue
            sig_type = t.get("strategy", "UNKNOWN")
            signal_stats[sig_type]["trades"] += 1
            if t.get("outcome") == "WIN":
                signal_stats[sig_type]["wins"] += 1
            signal_stats[sig_type]["pnl"] += float(t.get("pnl_usd", 0) or 0)
        for s in signal_stats:
            n = signal_stats[s]["trades"]
            signal_stats[s]["win_rate"] = round(signal_stats[s]["wins"] / n, 3) if n > 0 else 0

        # By confidence
        confidence_stats: dict = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
        for t in completed:
            if t.get("layer") != "layer3":
                continue
            parts = t.get("strategy", "").split("_")
            conf = parts[-1] if parts else "UNKNOWN"
            confidence_stats[conf]["trades"] += 1
            if t.get("outcome") == "WIN":
                confidence_stats[conf]["wins"] += 1
            confidence_stats[conf]["pnl"] += float(t.get("pnl_usd", 0) or 0)
        for c in confidence_stats:
            n = confidence_stats[c]["trades"]
            confidence_stats[c]["win_rate"] = round(confidence_stats[c]["wins"] / n, 3) if n > 0 else 0

        # Per-stock PnL
        stock_pnl: dict = defaultdict(float)
        for t in completed:
            stock_pnl[t.get("symbol", "")] += float(t.get("pnl_usd", 0) or 0)
        sorted_stocks = sorted(stock_pnl.items(), key=lambda x: x[1], reverse=True)

        calibration = self._compute_target_price_calibration(analyses)

        # Portfolio performance metrics from NAV history
        navs, times = self._get_nav_series()
        portfolio_metrics = compute_portfolio_metrics(navs, times) if len(navs) >= 3 else {}

        return {
            "period_days": days,
            "overall": {
                "num_trades":       len(completed),
                "win_rate":         round(win_rate, 3),
                "total_pnl_usd":    round(total_pnl, 2),
                "avg_pnl_per_trade": round(total_pnl / len(completed), 2),
            },
            "portfolio_metrics": portfolio_metrics,
            "by_sector":         dict(sector_stats),
            "by_signal_type":    dict(signal_stats),
            "by_confidence":     dict(confidence_stats),
            "calibration":       calibration,
            "best_performers":   [{"symbol": s, "total_pnl": round(p, 2)} for s, p in sorted_stocks[:3]],
            "worst_performers":  [{"symbol": s, "total_pnl": round(p, 2)} for s, p in sorted_stocks[-3:]],
        }

    def _compute_target_price_calibration(self, analyses: list) -> list:
        buckets: dict = defaultdict(list)
        for a in analyses:
            outlook = a.get("ai_outlook", "NEUTRAL")
            upside_pct = a.get("upside_pct")
            outcome = a.get("outcome")
            pnl = a.get("pnl_usd")
            if upside_pct is None or outcome not in ("WIN", "LOSS"):
                continue
            buckets[outlook].append({
                "upside_pct": float(upside_pct),
                "outcome": outcome,
                "pnl": float(pnl or 0),
            })

        calibration = []
        for outlook, records in buckets.items():
            if len(records) < 3:
                continue
            avg_upside = sum(r["upside_pct"] for r in records) / len(records)
            actual_win_rate = sum(1 for r in records if r["outcome"] == "WIN") / len(records)
            avg_pnl = sum(r["pnl"] for r in records) / len(records)
            calibration.append({
                "outlook":               outlook,
                "avg_target_upside_pct": round(avg_upside, 2),
                "actual_win_rate":       round(actual_win_rate, 3),
                "avg_pnl_usd":           round(avg_pnl, 2),
                "sample_size":           len(records),
            })
        return calibration

    def generate_report(self, days: int = 30) -> str:
        """Generate a human-readable performance report."""
        stats = self.compute_stats(days)

        lines = [f"=== Stock Trading Performance Report (last {days} days) ==="]

        if "status" in stats:
            lines.append(f"No completed trades in the last {days} days.")
        else:
            overall = stats["overall"]
            lines += [
                f"Total trades:  {overall['num_trades']}",
                f"Win rate:      {overall['win_rate']*100:.1f}%",
                f"Total PnL:     ${overall['total_pnl_usd']:+,.2f}",
                f"Avg per trade: ${overall['avg_pnl_per_trade']:+,.2f}",
            ]

        pm = stats.get("portfolio_metrics", {})
        if pm:
            lines.append("\n--- Portfolio Performance Metrics ---")
            lines.append(f"  CAGR:           {pm.get('cagr', 0)*100:+.2f}%")
            lines.append(f"  Total Return:   {pm.get('total_return', 0)*100:+.2f}%")
            lines.append(f"  Ann. Volatility:{pm.get('annualized_vol', 0)*100:.1f}%")
            lines.append(f"  Sharpe Ratio:   {pm.get('sharpe_ratio', 0):.3f}"
                         f"  (>1.0 = good, >2.0 = excellent)")
            lines.append(f"  Sortino Ratio:  {pm.get('sortino_ratio', 0):.3f}"
                         f"  (downside-only; better than Sharpe for asymmetric returns)")
            lines.append(f"  Max Drawdown:   {pm.get('max_drawdown', 0)*100:.2f}%")
            calmar = pm.get("calmar_ratio")
            calmar_str = f"{calmar:.3f}" if calmar is not None else "N/A (no drawdown)"
            lines.append(f"  Calmar Ratio:   {calmar_str}"
                         f"  (CAGR ÷ |MDD|; >1.0 = good)")
            lines.append(f"  History:        {pm.get('years', 0):.1f} years "
                         f"({pm.get('periods', 0)} observations)")

        if "by_sector" in stats:
            lines.append("\n--- Performance by Sector ---")
            for sector, s in sorted(stats["by_sector"].items(),
                                    key=lambda x: x[1].get("pnl", 0), reverse=True):
                lines.append(
                    f"  {sector:<30} win={s.get('win_rate', 0)*100:.0f}%"
                    f"  pnl=${s.get('pnl', 0):+,.2f}"
                    f"  n={s.get('trades', 0)}"
                )

        if "by_signal_type" in stats:
            lines.append("\n--- Performance by Signal Type (Layer 2) ---")
            for sig, s in sorted(stats["by_signal_type"].items(),
                                  key=lambda x: x[1].get("pnl", 0), reverse=True):
                lines.append(
                    f"  {sig:<30} win={s.get('win_rate', 0)*100:.0f}%"
                    f"  pnl=${s.get('pnl', 0):+,.2f}"
                    f"  n={s.get('trades', 0)}"
                )

        if "by_confidence" in stats:
            lines.append("\n--- Confidence Calibration ---")
            for conf, c in sorted(stats["by_confidence"].items()):
                lines.append(
                    f"  {conf:<8} win={c.get('win_rate', 0)*100:.0f}%"
                    f"  pnl=${c.get('pnl', 0):+,.2f}"
                    f"  n={c.get('trades', 0)}"
                )

        if stats.get("best_performers"):
            lines.append("\n--- Best Performers ---")
            for p in stats["best_performers"]:
                lines.append(f"  {p['symbol']:<10} ${p['total_pnl']:+,.2f}")

        if stats.get("worst_performers"):
            lines.append("\n--- Worst Performers ---")
            for p in stats["worst_performers"]:
                lines.append(f"  {p['symbol']:<10} ${p['total_pnl']:+,.2f}")

        return "\n".join(lines)
