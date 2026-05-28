"""
Layer 1 — News and information ingestion.

Sources:
1. RSS feeds (Reuters, NYT, BBC, Politico, CoinDesk, etc.)
2. Twitter/X streaming via the v2 filtered-stream API
3. Scheduled structured events (Fed calendar, earnings, elections)

All items are normalised and stored via the Database class.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import feedparser  # pip install feedparser

from config.settings import config
from layer1_data.database import Database

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RSS ingestion
# ---------------------------------------------------------------------------

class RSSIngestor:
    """Poll RSS feeds and store new headlines."""

    def __init__(self, db: Database, poll_interval: int = 300):
        self.db = db
        self.poll_interval = poll_interval
        self._seen_urls: set[str] = set()
        self._running = False

    async def _fetch_feed(self, session: aiohttp.ClientSession, url: str):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                content = await resp.read()
            feed = feedparser.parse(content)
            for entry in feed.entries:
                item_url = entry.get("link", "")
                if item_url in self._seen_urls:
                    continue
                self._seen_urls.add(item_url)

                published = ""
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    ).isoformat()

                summary = entry.get("summary", "") or entry.get("content", [{}])[0].get("value", "")

                self.db.insert_news(
                    source=feed.feed.get("title", url),
                    headline=entry.get("title", ""),
                    body=summary[:2000],
                    url=item_url,
                    published_at=published,
                )
        except Exception as exc:
            logger.warning("RSS fetch failed for %s: %s", url, exc)

    async def run_forever(self):
        self._running = True
        async with aiohttp.ClientSession() as session:
            while self._running:
                tasks = [self._fetch_feed(session, url) for url in config.rss_feeds]
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.debug("RSS cycle complete — sleeping %ds", self.poll_interval)
                await asyncio.sleep(self.poll_interval)

    def stop(self):
        self._running = False


# ---------------------------------------------------------------------------
# Twitter/X filtered stream ingestion
# ---------------------------------------------------------------------------

MARKET_KEYWORDS = [
    "stock market", "earnings", "fed rate", "inflation", "interest rate",
    "GDP", "recession", "bull market", "bear market", "S&P 500",
    "NASDAQ", "bitcoin", "ethereum", "crypto", "IPO", "merger",
    "acquisition", "dividend", "buyback", "quarterly results",
]


class TwitterIngestor:
    """
    Stream tweets matching stock market keywords via the v2
    filtered-stream endpoint.

    Requires TWITTER_BEARER_TOKEN in the environment.
    """

    STREAM_URL = "https://api.twitter.com/2/tweets/search/stream"
    RULES_URL = "https://api.twitter.com/2/tweets/search/stream/rules"

    def __init__(self, db: Database):
        self.db = db
        self._running = False

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {config.twitter.bearer_token}"}

    async def _set_rules(self, session: aiohttp.ClientSession):
        """Delete existing rules and set fresh stock market rules."""
        # Fetch existing rules
        async with session.get(self.RULES_URL, headers=self._auth_headers()) as resp:
            data = await resp.json()
            existing = data.get("data", [])

        if existing:
            ids = [r["id"] for r in existing]
            await session.post(
                self.RULES_URL,
                json={"delete": {"ids": ids}},
                headers=self._auth_headers(),
            )

        # Add new rules (each rule is up to 512 chars)
        rules = [
            {"value": " OR ".join(MARKET_KEYWORDS[:8]), "tag": "pm_terms_1"},
            {"value": " OR ".join(MARKET_KEYWORDS[8:]), "tag": "pm_terms_2"},
        ]
        await session.post(
            self.RULES_URL,
            json={"add": rules},
            headers=self._auth_headers(),
        )
        logger.info("Twitter stream rules set")

    async def run_forever(self):
        if not config.twitter.bearer_token:
            logger.warning("TWITTER_BEARER_TOKEN not set — skipping Twitter ingestor")
            return

        self._running = True
        backoff = 5
        async with aiohttp.ClientSession() as session:
            await self._set_rules(session)
            while self._running:
                try:
                    params = {"tweet.fields": "created_at,author_id,entities"}
                    async with session.get(
                        self.STREAM_URL,
                        headers=self._auth_headers(),
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=None),  # streaming
                    ) as resp:
                        backoff = 5
                        async for line in resp.content:
                            if not self._running:
                                break
                            line = line.strip()
                            if not line:
                                continue
                            import json
                            try:
                                tweet = json.loads(line)
                                text = tweet.get("data", {}).get("text", "")
                                created = tweet.get("data", {}).get("created_at", "")
                                if text:
                                    self.db.insert_news(
                                        source="twitter",
                                        headline=text[:280],
                                        published_at=created,
                                    )
                            except Exception:
                                pass
                except Exception as exc:
                    if not self._running:
                        break
                    logger.warning("Twitter stream error: %s — retry in %ds", exc, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 120)

    def stop(self):
        self._running = False


# ---------------------------------------------------------------------------
# Structured event calendar
# ---------------------------------------------------------------------------

class StructuredEventFeed:
    """
    Polls structured data sources for scheduled events:
    - Fed FOMC meeting dates (hardcoded upcoming + annual refresh)
    - Earnings calendar (placeholder — integrate Polygon.io or similar)
    - Major political event dates
    """

    # FOMC 2025 meeting dates (update annually)
    FOMC_DATES_2025 = [
        "2025-01-29", "2025-03-19", "2025-05-07",
        "2025-06-18", "2025-07-30", "2025-09-17",
        "2025-10-29", "2025-12-10",
    ]

    def __init__(self, db: Database):
        self.db = db

    def seed_fomc_events(self):
        """Insert FOMC meeting dates as structured news items."""
        for date_str in self.FOMC_DATES_2025:
            self.db.insert_news(
                source="fed_calendar",
                headline=f"FOMC Meeting — {date_str}",
                body="Federal Open Market Committee scheduled meeting. Rate decision expected.",
                published_at=date_str + "T14:00:00+00:00",
            )
        logger.info("Seeded %d FOMC events", len(self.FOMC_DATES_2025))

    async def run_forever(self):
        """Run once at startup, then sleep (events are mostly static)."""
        self.seed_fomc_events()
        # Future: poll an earnings calendar API every morning
        while True:
            await asyncio.sleep(86_400)  # refresh daily
