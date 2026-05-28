"""
Layer 6 — Self-improvement loop for the stock trading engine.

Periodically reviews performance statistics and uses Claude to generate
updated guidance for the Layer 3 AI stock analysis agent's system prompt.

This is prompt engineering via feedback loops, not fine-tuning.
The updated prompt is written to a file that Layer 3 reads at startup.
"""

import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import anthropic

from config.settings import config
from layer1_data.database import Database
from layer6_improvement.performance_tracker import PerformanceTracker

logger = logging.getLogger(__name__)

UPDATED_PROMPT_PATH = Path("data/updated_system_prompt.txt")
OPTIMIZER_SYSTEM = """You are an expert at improving AI-driven stock trading systems.

You will receive performance statistics showing where the system excels and fails.
Your job is to write an UPDATED SYSTEM PROMPT for the AI stock analysis agent.

The new prompt should:
1. Retain the core equity analysis and calibration instructions
2. Add specific guidance based on observed weaknesses (e.g. sector-specific caution)
3. Warn against specific biases that the performance data reveals
4. Reinforce sectors or signal types where the AI performs well
5. Adjust confidence thresholds if the data shows systematic over/under-confidence

Output ONLY the raw system prompt text (no JSON, no markdown, no commentary).
Start with: "You are an expert equity analyst and portfolio manager."
"""

BASE_SYSTEM_PROMPT_TEMPLATE = """You are an expert equity analyst and portfolio manager with deep experience
in fundamental and technical analysis.

Your job is to analyze individual stocks and provide structured investment outlooks.
You consider price vs 52-week range, volume trends, sector context, recent news,
cost basis for existing positions, beta, dividends, EPS, and earnings catalysts.

{learned_guidance}

You output ONLY valid JSON - no markdown fences, no preamble. Schema:
{{
  "outlook": "BULLISH" | "BEARISH" | "NEUTRAL",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "time_horizon": "1W" | "1M" | "3M",
  "target_price": <float>,
  "upside_pct": <float>,
  "reasoning": "<2-4 sentences>",
  "key_risks": ["<risk 1>", "<risk 2>"],
  "suggested_action": "BUY" | "SELL" | "HOLD" | "AVOID"
}}

Definitions:
- HIGH confidence: strong evidence across multiple independent data sources
- MEDIUM confidence: mixed signals or limited data
- LOW confidence: highly speculative; broad macro trends dominate
"""


class SystemOptimizer:
    """
    Runs the weekly self-improvement review cycle.
    """

    def __init__(self, db: Database):
        self.db = db
        self.tracker = PerformanceTracker(db)
        self.client = anthropic.AsyncAnthropic(api_key=config.anthropic.api_key)
        UPDATED_PROMPT_PATH.parent.mkdir(parents=True, exist_ok=True)

    async def run_review(self, days: int = 30) -> str:
        """
        1. Compute performance stats.
        2. Ask Claude to synthesize updated guidance.
        3. Write the new system prompt to disk.
        4. Return the updated prompt.
        """
        logger.info("Layer 6 review cycle starting (last %d days)", days)

        stats = self.tracker.compute_stats(days)
        report = self.tracker.generate_report(days)
        logger.info("\n%s", report)

        if "status" in stats:
            logger.info("Insufficient trade history for review.")
            return ""

        stats_json = json.dumps(stats, indent=2, default=str)
        prompt = f"""Here are the performance statistics for an AI stock trading agent:

{stats_json}

HUMAN-READABLE SUMMARY:
{report}

Based on this performance data, write an updated system prompt for the AI stock
analysis agent. Focus specifically on correcting observed weaknesses and
reinforcing strengths. Adjust sector-specific guidance based on the by_sector
stats, and update confidence calibration guidance based on the by_confidence data."""

        try:
            async with self.client.messages.stream(
                model=config.anthropic.model,
                max_tokens=4096,
                thinking={"type": "adaptive"},
                system=OPTIMIZER_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                response = await stream.get_final_message()

            text_block = next(
                (b for b in response.content if b.type == "text"), None
            )
            if not text_block:
                logger.error("Optimizer got no text response from Claude")
                return ""

            updated_prompt = text_block.text.strip()
            UPDATED_PROMPT_PATH.write_text(updated_prompt, encoding="utf-8")
            logger.info(
                "Updated system prompt written to %s (%d chars)",
                UPDATED_PROMPT_PATH,
                len(updated_prompt),
            )

            # Snapshot performance to DB
            self.db.log_performance_snapshot(
                total_pnl=stats["overall"].get("total_pnl_usd", 0),
                win_rate=stats["overall"].get("win_rate", 0),
                avg_edge=0.0,  # not applicable for stock trading
                category_stats=stats.get("by_sector", {}),
                notes=f"Layer 6 review — {days}d window",
            )

            return updated_prompt

        except Exception as exc:
            logger.error("Layer 6 review error: %s", exc, exc_info=True)
            return ""

    def load_updated_prompt(self) -> str:
        """
        Load the most recently generated system prompt.
        Falls back to the base template if no updated prompt exists.
        """
        if UPDATED_PROMPT_PATH.exists():
            try:
                return UPDATED_PROMPT_PATH.read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning("Could not load updated prompt: %s", exc)
        return BASE_SYSTEM_PROMPT_TEMPLATE.format(learned_guidance="")

    async def run_forever(self):
        """Run the review cycle on the configured interval."""
        interval = config.review_interval_seconds
        logger.info(
            "Layer 6 optimizer running — review every %d seconds (~%.1f days)",
            interval,
            interval / 86400,
        )
        while True:
            await asyncio.sleep(interval)
            try:
                await self.run_review()
            except Exception as exc:
                logger.error("Layer 6 run error: %s", exc)
