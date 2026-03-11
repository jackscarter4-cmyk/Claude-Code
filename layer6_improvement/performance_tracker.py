"""
Layer 6 — Performance tracking and analysis.

Aggregates trade outcomes, computes statistics by category, model,
confidence level, and time period. Results are used by the optimizer
to update the AI agent's system prompt.
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
    Analyses historical trade data and AI predictions to identify
    systematic biases and improvement opportunities.
    """

    def __init__(self, db: Database):
        self.db = db

    def compute_stats(self, days: int = 30) -> dict:
        """
        Compute comprehensive performance statistics over the last N days.

        Returns a dict with:
          - overall: win_rate, total_pnl, avg_edge, num_trades
          - by_category: per-category breakdown
          - by_confidence: performance at each confidence level
          - calibration: how accurate AI probabilities are vs actual outcomes
          - worst_markets: markets where AI consistently underperforms
        """
        trades = self.db.get_trade_history(limit=2000)
        predictions = self.db.get_predictions_for_review(days=days)

        # Filter to completed trades
        completed = [
            t for t in trades
            if t.get("outcome") in ("WIN", "LOSS")
            and t.get("layer") == "layer3"
        ]

        if not completed:
            return {"status": "no_completed_trades", "days": days}

        # Overall stats
        wins = [t for t in completed if t.get("outcome") == "WIN"]
        losses = [t for t in completed if t.get("outcome") == "LOSS"]
        total_pnl = sum(float(t.get("pnl_usdc", 0) or 0) for t in completed)
        win_rate = len(wins) / len(completed) if completed else 0

        # By category
        category_stats: dict[str, dict] = defaultdict(lambda: {
            "trades": 0, "wins": 0, "pnl": 0.0, "avg_edge": 0.0
        })
        for t in completed:
            market = self.db.get_market(t.get("market_id", ""))
            cat = market.get("category", "unknown") if market else "unknown"
            category_stats[cat]["trades"] += 1
            if t.get("outcome") == "WIN":
                category_stats[cat]["wins"] += 1
            category_stats[cat]["pnl"] += float(t.get("pnl_usdc", 0) or 0)
            edge = float(t.get("edge", 0) or 0)
            n = category_stats[cat]["trades"]
            prev_avg = category_stats[cat]["avg_edge"]
            category_stats[cat]["avg_edge"] = prev_avg + (edge - prev_avg) / n

        for cat in category_stats:
            n = category_stats[cat]["trades"]
            w = category_stats[cat]["wins"]
            category_stats[cat]["win_rate"] = w / n if n > 0 else 0

        # By confidence level (using stored AI confidence)
        confidence_stats: dict[str, dict] = defaultdict(lambda: {
            "trades": 0, "wins": 0, "pnl": 0.0
        })
        for pred in predictions:
            if pred.get("outcome") in ("WIN", "LOSS"):
                # approximate confidence from edge
                edge = abs(float(pred.get("edge", 0) or 0))
                conf = "HIGH" if edge > 0.20 else "MEDIUM" if edge > 0.10 else "LOW"
                confidence_stats[conf]["trades"] += 1
                if pred.get("outcome") == "WIN":
                    confidence_stats[conf]["wins"] += 1
                confidence_stats[conf]["pnl"] += float(pred.get("pnl_usdc", 0) or 0)

        # Calibration: bucket predictions by stated probability
        calibration = self._compute_calibration(predictions)

        # Worst-performing categories
        sorted_cats = sorted(
            category_stats.items(),
            key=lambda x: x[1].get("win_rate", 0),
        )
        worst_categories = [
            {"category": cat, **stats}
            for cat, stats in sorted_cats[:3]
            if stats.get("trades", 0) >= 5
        ]

        result = {
            "period_days": days,
            "overall": {
                "num_trades": len(completed),
                "win_rate": round(win_rate, 3),
                "total_pnl_usdc": round(total_pnl, 2),
                "avg_pnl_per_trade": round(total_pnl / len(completed), 2) if completed else 0,
                "avg_edge": round(
                    sum(abs(float(t.get("edge", 0) or 0)) for t in completed) / len(completed),
                    3,
                ) if completed else 0,
            },
            "by_category": dict(category_stats),
            "by_confidence": dict(confidence_stats),
            "calibration": calibration,
            "worst_categories": worst_categories,
        }
        return result

    def _compute_calibration(self, predictions: list[dict]) -> list[dict]:
        """
        Group predictions by probability bucket and compute actual resolution rate.

        A well-calibrated forecaster's 70% predictions resolve YES ~70% of the time.
        """
        buckets: dict[str, list[float]] = defaultdict(list)
        for pred in predictions:
            ai_prob = pred.get("ai_probability")
            outcome = pred.get("outcome")
            if ai_prob is None or outcome not in ("WIN", "LOSS"):
                continue
            bucket = round(float(ai_prob) * 10) / 10  # 0.1 buckets
            resolved_yes = 1 if outcome == "WIN" else 0
            buckets[str(bucket)].append(resolved_yes)

        calibration = []
        for bucket_str, outcomes in sorted(buckets.items()):
            if len(outcomes) >= 3:
                calibration.append({
                    "stated_probability": float(bucket_str),
                    "actual_rate": round(sum(outcomes) / len(outcomes), 3),
                    "sample_size": len(outcomes),
                    "calibration_error": round(
                        abs(float(bucket_str) - sum(outcomes) / len(outcomes)), 3
                    ),
                })
        return calibration

    def generate_report(self, days: int = 30) -> str:
        """Generate a human-readable performance report."""
        stats = self.compute_stats(days)
        if "status" in stats:
            return f"No completed trades in the last {days} days."

        overall = stats["overall"]
        lines = [
            f"=== Performance Report (last {days} days) ===",
            f"Total trades: {overall['num_trades']}",
            f"Win rate:     {overall['win_rate']*100:.1f}%",
            f"Total PnL:    ${overall['total_pnl_usdc']:+.2f}",
            f"Avg edge:     {overall['avg_edge']*100:.1f}%",
            "",
            "--- Performance by Category ---",
        ]

        for cat, cat_stats in sorted(
            stats["by_category"].items(),
            key=lambda x: x[1].get("pnl", 0),
            reverse=True,
        ):
            lines.append(
                f"  {cat:<30} win={cat_stats.get('win_rate',0)*100:.0f}%"
                f"  pnl=${cat_stats.get('pnl',0):+.2f}"
                f"  n={cat_stats.get('trades',0)}"
            )

        if stats.get("calibration"):
            lines.append("\n--- Calibration ---")
            for c in stats["calibration"]:
                error = c["calibration_error"]
                flag = " ← MISCALIBRATED" if error > 0.10 else ""
                lines.append(
                    f"  Stated {c['stated_probability']*100:.0f}% → "
                    f"Actual {c['actual_rate']*100:.1f}% "
                    f"(n={c['sample_size']}){flag}"
                )

        return "\n".join(lines)
