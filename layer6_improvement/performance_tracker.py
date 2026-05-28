"""
Layer 6 — Performance tracking and analysis for the stock trading engine.

Aggregates trade outcomes, computes statistics by sector and signal_type,
calibrates AI target-price accuracy, and surfaces best/worst performers.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from layer1_data.database import Database

logger = logging.getLogger(__name__)


class PerformanceTracker:
    """
    Analyses historical trade data and AI stock analyses to identify
    systematic biases and improvement opportunities.
    """

    def __init__(self, db: Database):
        self.db = db

    def compute_stats(self, days: int = 30) -> dict:
        """
        Compute comprehensive performance statistics over the last N days.

        Returns a dict with:
          - overall: win_rate, total_pnl, num_trades, avg_pnl_per_trade
          - by_sector: per-sector breakdown using watchlist metadata
          - by_signal_type: breakdown by Layer 2 strategy tag
          - by_confidence: performance at each AI confidence level
          - calibration: compare AI target_price to actual price N days later
          - best_performers: top 3 stocks by PnL
          - worst_performers: bottom 3 stocks by PnL
        """
        trades = self.db.get_trade_history(limit=5000)
        analyses = self.db.get_analyses_for_review(days=days)

        # Filter to completed trades within the window
        from datetime import timedelta
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()
        completed = [
            t for t in trades
            if t.get("outcome") in ("WIN", "LOSS")
            and (t.get("placed_at", "") >= cutoff)
        ]

        if not completed:
            return {"status": "no_completed_trades", "days": days}

        wins = [t for t in completed if t.get("outcome") == "WIN"]
        total_pnl = sum(float(t.get("pnl_usd", 0) or 0) for t in completed)
        win_rate = len(wins) / len(completed) if completed else 0

        # By sector
        sector_stats: dict = defaultdict(lambda: {
            "trades": 0, "wins": 0, "pnl": 0.0,
        })
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
            w = sector_stats[s]["wins"]
            sector_stats[s]["win_rate"] = round(w / n, 3) if n > 0 else 0

        # By signal type (Layer 2 strategy field)
        signal_stats: dict = defaultdict(lambda: {
            "trades": 0, "wins": 0, "pnl": 0.0,
        })
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
            w = signal_stats[s]["wins"]
            signal_stats[s]["win_rate"] = round(w / n, 3) if n > 0 else 0

        # By confidence (Layer 3 AI trades)
        confidence_stats: dict = defaultdict(lambda: {
            "trades": 0, "wins": 0, "pnl": 0.0,
        })
        for t in completed:
            if t.get("layer") != "layer3":
                continue
            strategy = t.get("strategy", "")
            # strategy format: "AI_BULLISH_HIGH" etc.
            parts = strategy.split("_")
            conf = parts[-1] if parts else "UNKNOWN"
            confidence_stats[conf]["trades"] += 1
            if t.get("outcome") == "WIN":
                confidence_stats[conf]["wins"] += 1
            confidence_stats[conf]["pnl"] += float(t.get("pnl_usd", 0) or 0)
        for c in confidence_stats:
            n = confidence_stats[c]["trades"]
            w = confidence_stats[c]["wins"]
            confidence_stats[c]["win_rate"] = round(w / n, 3) if n > 0 else 0

        # Per-stock PnL for best/worst performers
        stock_pnl: dict = defaultdict(float)
        for t in completed:
            sym = t.get("symbol", "")
            stock_pnl[sym] += float(t.get("pnl_usd", 0) or 0)

        sorted_stocks = sorted(stock_pnl.items(), key=lambda x: x[1], reverse=True)
        best_performers = [
            {"symbol": sym, "total_pnl": round(pnl, 2)}
            for sym, pnl in sorted_stocks[:3]
        ]
        worst_performers = [
            {"symbol": sym, "total_pnl": round(pnl, 2)}
            for sym, pnl in sorted_stocks[-3:]
        ]

        # Calibration: compare AI target_price to price_at_analysis
        calibration = self._compute_target_price_calibration(analyses)

        return {
            "period_days": days,
            "overall": {
                "num_trades": len(completed),
                "win_rate": round(win_rate, 3),
                "total_pnl_usd": round(total_pnl, 2),
                "avg_pnl_per_trade": round(total_pnl / len(completed), 2) if completed else 0,
            },
            "by_sector": dict(sector_stats),
            "by_signal_type": dict(signal_stats),
            "by_confidence": dict(confidence_stats),
            "calibration": calibration,
            "best_performers": best_performers,
            "worst_performers": worst_performers,
        }

    def _compute_target_price_calibration(self, analyses: list) -> list:
        """
        Compare the AI's target_price to the actual price movement.
        Groups by outlook (BULLISH/BEARISH/NEUTRAL) and shows average
        upside vs. actual trade outcome.
        """
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
                "outlook": outlook,
                "avg_target_upside_pct": round(avg_upside, 2),
                "actual_win_rate": round(actual_win_rate, 3),
                "avg_pnl_usd": round(avg_pnl, 2),
                "sample_size": len(records),
            })
        return calibration

    def generate_report(self, days: int = 30) -> str:
        """Generate a human-readable performance report."""
        stats = self.compute_stats(days)
        if "status" in stats:
            return f"No completed trades in the last {days} days."

        overall = stats["overall"]
        lines = [
            f"=== Stock Trading Performance Report (last {days} days) ===",
            f"Total trades: {overall['num_trades']}",
            f"Win rate:     {overall['win_rate']*100:.1f}%",
            f"Total PnL:    ${overall['total_pnl_usd']:+.2f}",
            f"Avg per trade:${overall['avg_pnl_per_trade']:+.2f}",
            "",
            "--- Performance by Sector ---",
        ]

        for sector, s_stats in sorted(
            stats["by_sector"].items(),
            key=lambda x: x[1].get("pnl", 0),
            reverse=True,
        ):
            lines.append(
                f"  {sector:<30} win={s_stats.get('win_rate', 0)*100:.0f}%"
                f"  pnl=${s_stats.get('pnl', 0):+.2f}"
                f"  n={s_stats.get('trades', 0)}"
            )

        lines.append("\n--- Performance by Signal Type (Layer 2) ---")
        for sig, s_stats in sorted(
            stats["by_signal_type"].items(),
            key=lambda x: x[1].get("pnl", 0),
            reverse=True,
        ):
            lines.append(
                f"  {sig:<30} win={s_stats.get('win_rate', 0)*100:.0f}%"
                f"  pnl=${s_stats.get('pnl', 0):+.2f}"
                f"  n={s_stats.get('trades', 0)}"
            )

        lines.append("\n--- AI Confidence Calibration ---")
        for conf, c_stats in sorted(stats["by_confidence"].items()):
            lines.append(
                f"  {conf:<8} win={c_stats.get('win_rate', 0)*100:.0f}%"
                f"  pnl=${c_stats.get('pnl', 0):+.2f}"
                f"  n={c_stats.get('trades', 0)}"
            )

        if stats.get("best_performers"):
            lines.append("\n--- Best Performers ---")
            for p in stats["best_performers"]:
                lines.append(f"  {p['symbol']:<10} ${p['total_pnl']:+.2f}")

        if stats.get("worst_performers"):
            lines.append("\n--- Worst Performers ---")
            for p in stats["worst_performers"]:
                lines.append(f"  {p['symbol']:<10} ${p['total_pnl']:+.2f}")

        return "\n".join(lines)
