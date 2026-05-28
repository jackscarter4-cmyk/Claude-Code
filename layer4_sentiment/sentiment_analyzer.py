"""
Layer 4 — Sentiment and hype detection for stocks.

Monitors stored news, Twitter, and Reddit for sentiment spikes around
watchlist symbols. When hype is detected, the Layer 3 agent can use
this signal to adjust its conviction level.

Hype score = normalised mention velocity over a rolling window.
"""

import asyncio
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
    r"!{3,}",
    r"\b(FOMO|FUD|shill|ape in|all in)\b",
]

HYPE_REGEX = re.compile("|".join(HYPE_PATTERNS), re.IGNORECASE)


def compute_hype_score(texts: list) -> float:
    """
    Return a hype score 0-1 based on the fraction of texts containing
    hype language. A score > 0.3 is considered significant.
    """
    if not texts:
        return 0.0
    hits = sum(1 for t in texts if HYPE_REGEX.search(t))
    raw = hits / len(texts)
    amplified = raw * min(len(texts) / 10.0, 3.0)
    return min(amplified, 1.0)


class SentimentMonitor:
    """
    Continuously scans stored news/tweets for sentiment spikes
    related to watchlist symbols, then writes signals to the database.

    The Layer 3 agent reads these signals when building its analysis prompt.
    """

    def __init__(self, db: Database):
        self.db = db
        # Rolling window: symbol -> deque of (timestamp, hype_score)
        self._window: dict = defaultdict(lambda: deque(maxlen=100))
        self._running = False

    def _relevant_texts_for_symbol(
        self, symbol: str, company_name: Optional[str], news_items: list
    ) -> list:
        """Filter news items that mention the symbol or company name."""
        symbol_lower = symbol.lower()
        company_words = set()
        if company_name:
            company_words = {
                w for w in company_name.lower().split() if len(w) > 4
            }

        relevant = []
        for item in news_items:
            text = (
                item.get("headline", "") + " " + item.get("body", "")
            ).lower()
            if symbol_lower in text or any(w in text for w in company_words):
                relevant.append(
                    item.get("headline", "") + " " + item.get("body", "")
                )
        return relevant

    async def _run_cycle(self, window_hours: int = 4):
        """One sentiment analysis cycle over all active watchlist symbols."""
        symbols = self.db.get_active_symbols()
        news_items = self.db.get_recent_news(hours=window_hours, limit=500)

        if not news_items:
            return

        for symbol in symbols:
            try:
                entry = self.db.get_watchlist_entry(symbol)
                company_name = (entry or {}).get("name")
                relevant = self._relevant_texts_for_symbol(
                    symbol, company_name, news_items
                )
                if not relevant:
                    continue

                score = compute_hype_score(relevant)
                count = len(relevant)

                self.db.insert_sentiment(
                    symbol=symbol,
                    keyword=symbol,
                    platform="news_aggregate",
                    count=count,
                    score=score,
                )
                self._window[symbol].append(
                    (datetime.now(timezone.utc), score)
                )

                if score > 0.3:
                    logger.info(
                        "HYPE SIGNAL: %s | score=%.2f mentions=%d",
                        symbol, score, count,
                    )
            except Exception as exc:
                logger.debug("Sentiment error for %s: %s", symbol, exc)

        logger.debug("Sentiment cycle complete for %d symbols", len(symbols))

    def get_hype_adjusted_outlook_discount(self, symbol: str) -> float:
        """
        Return a discount factor [0, hype_discount_factor] to apply to
        an AI outlook when hype is detected.  0 means no adjustment.
        """
        sentiment = self.db.get_latest_sentiment(symbol)
        if not sentiment:
            return 0.0
        hype_score = float(sentiment.get("hype_score", 0))
        if hype_score < 0.2:
            return 0.0
        return hype_score * config.sentiment.hype_discount_factor

    async def run_forever(self, interval: int = 300):
        """Run sentiment cycles every `interval` seconds."""
        self._running = True
        while self._running:
            try:
                await self._run_cycle()
            except Exception as exc:
                logger.error("Sentiment monitor error: %s", exc)
            await asyncio.sleep(interval)

    def stop(self):
        self._running = False
