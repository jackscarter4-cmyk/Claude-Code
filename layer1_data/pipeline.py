"""
Layer 1 — Top-level data pipeline orchestrator.

Starts all ingestors concurrently and keeps them running 24/7.
"""

import asyncio
import logging

from config.settings import config
from layer1_data.database import Database
from layer1_data.news_feeds import RSSIngestor, TwitterIngestor, StructuredEventFeed
from layer1_data.polymarket_ws import PolymarketWebSocket
from utils.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)


class DataPipeline:
    """Orchestrates all Layer 1 data-collection components."""

    def __init__(self, db: Database):
        self.db = db
        self.polymarket_client = PolymarketClient()
        self.ws_feed = PolymarketWebSocket(db)
        self.rss = RSSIngestor(db)
        self.twitter = TwitterIngestor(db)
        self.events = StructuredEventFeed(db)

    async def _bootstrap_markets(self):
        """Fetch all active markets on startup and store / register them."""
        logger.info("Bootstrapping active markets …")
        markets = await self.polymarket_client.get_all_active_markets()
        for m in markets:
            self.db.upsert_market(m)
        self.ws_feed.register_markets_from_db()
        logger.info("Bootstrapped %d markets", len(markets))

    async def _market_refresh_loop(self, interval: int = 3600):
        """Periodically refresh the market list (new markets, closures)."""
        while True:
            await asyncio.sleep(interval)
            try:
                logger.info("Refreshing market list …")
                markets = await self.polymarket_client.get_all_active_markets()
                for m in markets:
                    self.db.upsert_market(m)
                self.ws_feed.register_markets_from_db()
                logger.info("Market refresh: %d active", len(markets))
            except Exception as exc:
                logger.error("Market refresh failed: %s", exc)

    async def run_forever(self):
        """Start all pipeline components."""
        await self._bootstrap_markets()

        tasks = [
            asyncio.create_task(self.ws_feed.run_forever(), name="ws_feed"),
            asyncio.create_task(self.rss.run_forever(), name="rss"),
            asyncio.create_task(self.twitter.run_forever(), name="twitter"),
            asyncio.create_task(self.events.run_forever(), name="events"),
            asyncio.create_task(self._market_refresh_loop(), name="market_refresh"),
        ]
        logger.info("Layer 1 data pipeline running (%d tasks)", len(tasks))
        try:
            await asyncio.gather(*tasks)
        finally:
            await self.polymarket_client.close()

    def stop(self):
        self.ws_feed.stop()
        self.rss.stop()
        self.twitter.stop()
