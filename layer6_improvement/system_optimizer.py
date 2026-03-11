"""
Layer 6 — Self-improvement loop.

Periodically reviews performance statistics and uses Claude to generate
updated guidance for the Layer 3 AI agent's system prompt.

This is not fine-tuning — it's prompt engineering via feedback loops.
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
OPTIMIZER_SYSTEM = """You are an expert at improving AI prediction market trading systems.

You will receive performance statistics showing where the system excels and fails.
Your job is to write an UPDATED SYSTEM PROMPT for the AI forecasting agent.

The new prompt should:
1. Retain the core calibration and reasoning instructions
2. Add specific guidance based on observed weaknesses
3. Include category-specific cautions where the AI underperforms
4. Warn against specific biases that the performance data reveals
5. Reinforce categories where the AI performs well

Output ONLY the raw system prompt text (no JSON, no markdown, no commentary).
Start with: "You are an expert probability forecaster and prediction market analyst."
"""

BASE_SYSTEM_PROMPT_TEMPLATE = """You are an expert probability forecaster and prediction market analyst.
Your job is to estimate the true probability of events resolving YES on Polymarket.

You approach each market with:
- Rigorous base-rate reasoning
- Careful reading of resolution criteria
- Awareness of common biases (recency, availability, narrative)
- Calibration: you are not overconfident; your 70% estimates are right ~70% of the time

{learned_guidance}

You output ONLY valid JSON — no markdown fences, no preamble. Schema:
{{
  "probability": <float 0.0–1.0>,
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "reasoning": "<2–4 sentences explaining your estimate>",
  "key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"]
}}

Definitions:
- HIGH confidence: you have strong evidence and the resolution criteria are clear
- MEDIUM confidence: some uncertainty in data or interpretation
- LOW confidence: highly speculative, thin information, ambiguous criteria
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

        # Ask Claude to generate updated guidance
        stats_json = json.dumps(stats, indent=2, default=str)
        prompt = f"""Here are the performance statistics for an AI prediction market trading agent:

{stats_json}

HUMAN-READABLE SUMMARY:
{report}

Based on this performance data, write an updated system prompt for the AI forecasting agent.
Focus specifically on correcting observed weaknesses and reinforcing strengths."""

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
            self.db._conn.execute("""
                INSERT INTO performance_snapshots (ts, total_pnl, win_rate, avg_edge, category_stats, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(),
                stats["overall"].get("total_pnl_usdc", 0),
                stats["overall"].get("win_rate", 0),
                stats["overall"].get("avg_edge", 0),
                json.dumps(stats.get("by_category", {})),
                f"Layer 6 review — {days}d window",
            ))
            self.db._conn.commit()

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
