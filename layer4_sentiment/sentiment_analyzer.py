"""
Layer 4 — Sentiment and hype detection.

Monitors Twitter/X, Reddit, and stored news for spikes in emotional
language around specific markets. When hype is detected, it signals
Layer 3 to discount the probability estimate for the hyped side.

Hype score = normalised mention velocity over a rolling window.
"""

import logging
import re
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Optional

from config.settings import config
from layer1_data.database import Database

logger = logging.getLogger(__name__)

# Emotional / hype language patterns
HYPE_PATTERNS = [
    r"\b(moon|pump|surge|skyrocket|explode|100x|to the moon)\b",
    r"\b(crash|collapse|plummet|tank|dump|rug pull|dead)\b",
    r"\b(guaranteed|certain|obvious|no way|impossible|never)\b",
    r"\b(everyone knows|everyone is|obviously|clearly|definitely)\b",
    r"!{3,}",          # Three or more exclamation marks
    r"\b(FOMO|FUD|shill|ape in|all in)\b",
]

HYPE_REGEX = re.compile("|".join(HYPE_PATTERNS), re.IGNORECASE)


def compute_hype_score(texts: list[str]) -> float:
    """
    Return a hype score 0–1 based on the fraction of texts containing
    hype language. A score > 0.3 is considered significant.
    """
    if not texts:
        return 0.0
    hits = sum(1 for t in texts if HYPE_REGEX.search(t))
    raw = hits / len(texts)
    # Amplify: small fractions → larger signal when volume is high
    amplified = raw * min(len(texts) / 10.0, 3.0)
    return min(amplified, 1.0)


class SentimentMonitor:
    """
    Continuously scans stored news/tweets for sentiment spikes
    related to active markets, then writes signals to the database.

    The Layer 3 agent reads these signals when building its analysis prompt.
    """

    def __init__(self, db: Database):
        self.db = db
        # Rolling window: market_id -> deque of (timestamp, hype_score)
        self._window: dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self._running = False

    def _extract_market_keywords(self, market: dict) -> list[str]:
        """Extract meaningful keywords from a market question."""
        question = market.get("question", "")
        # Strip common prediction-market filler words
        stop = {
            "will", "the", "a", "an", "in", "of", "to", "be", "or", "and",
            "is", "on", "by", "at", "for", "that", "this", "have", "has",
            "yes", "no", "win", "lose",
        }
        words = [w.strip("?,.!") for w in question.lower().split()]
        keywords = [w for w in words if len(w) > 3 and w not in stop]
        return keywords[:8]  # top 8 most relevant

    def _score_market_sentiment(
        self, market: dict, news_items: list[dict]
    ) -> tuple[int, float]:
        """
        Score how much hype is present for a specific market in recent news.

        Returns (mention_count, hype_score).
        """
        keywords = self._extract_market_keywords(market)
        if not keywords:
            return 0, 0.0

        # Find news that mentions this market's keywords
        relevant_texts = []
        for item in news_items:
            text = (item.get("headline", "") + " " + item.get("body", "")).lower()
            if any(kw in text for kw in keywords):
                relevant_texts.append(item.get("headline", "") + " " + item.get("body", ""))

        score = compute_hype_score(relevant_texts)
        return len(relevant_texts), score

    async def _run_cycle(self, window_hours: int = 4):
        """
        One sentiment analysis cycle over all active markets.
        """
        import asyncio

        markets = self.db.get_active_markets()
        # Get news from the last window
        news_items = self.db.get_recent_news(hours=window_hours, limit=500)

        if not news_items:
            return

        for market in markets:
            market_id = market["id"]
            try:
                count, score = self._score_market_sentiment(market, news_items)

                if count > 0:
                    # Store in DB for Layer 3 to read
                    self.db.insert_sentiment(
                        market_id=market_id,
                        keyword=",".join(self._extract_market_keywords(market)[:3]),
                        platform="news_aggregate",
                        count=count,
                        score=score,
                    )
                    # Update rolling window
                    self._window[market_id].append(
                        (datetime.now(timezone.utc), score)
                    )

                    if score > 0.3:
                        logger.info(
                            "HYPE SIGNAL: %s | score=%.2f mentions=%d",
                            market.get("question", "")[:60],
                            score,
                            count,
                        )
            except Exception as exc:
                logger.debug("Sentiment error for %s: %s", market_id[:8], exc)

        logger.debug("Sentiment cycle complete for %d markets", len(markets))

    def get_hype_adjusted_probability(
        self, base_probability: float, market_id: str
    ) -> float:
        """
        Apply hype discount to a base probability estimate.

        If there is a strong hype signal, shift the probability towards 0.5
        (i.e., towards uncertainty) proportional to the hype score.
        """
        sentiment = self.db.get_latest_sentiment(market_id)
        if not sentiment:
            return base_probability

        hype_score = float(sentiment.get("hype_score", 0))
        if hype_score < 0.2:
            return base_probability

        discount = hype_score * config.sentiment.hype_discount_factor
        # Pull the estimate toward 0.5
        adjusted = base_probability + discount * (0.5 - base_probability)
        logger.debug(
            "Hype adjustment for %s: %.3f → %.3f (hype=%.2f)",
            market_id[:8], base_probability, adjusted, hype_score,
        )
        return max(0.01, min(0.99, adjusted))

    async def run_forever(self, interval: int = 300):
        """Run sentiment cycles every `interval` seconds."""
        import asyncio
        self._running = True
        while self._running:
            try:
                await self._run_cycle()
            except Exception as exc:
                logger.error("Sentiment monitor error: %s", exc)
            await asyncio.sleep(interval)

    def stop(self):
        self._running = False
