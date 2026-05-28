"""
Layer 1 — Top-level data pipeline orchestrator.

Starts all ingestors concurrently and keeps them running 24/7:
  - Alpaca IEX WebSocket for real-time price ticks
  - RSS / Twitter / Reddit news ingestors
  - Daily portfolio snapshot task (4:30pm ET on weekdays)
  - Hourly watchlist metadata refresh from Yahoo Finance
"""

import asyncio
import logging
from datetime import datetime, timezone, time as dt_time

from config.settings import config
from layer1_data.database import Database
from layer1_data.news_feeds import RSSIngestor, TwitterIngestor, StructuredEventFeed
from layer1_data.polymarket_ws import MarketDataFeed
from utils.broker_client import BrokerClient, YahooFinanceClient

logger = logging.getLogger(__name__)

# 4:30 PM ET in UTC offset.  ET = UTC-5 (EST) or UTC-4 (EDT).
# We use 21:30 UTC as a conservative post-close time that works year-round.
_SNAPSHOT_HOUR_UTC = 21
_SNAPSHOT_MINUTE_UTC = 30


class DataPipeline:
    """Orchestrates all Layer 1 data-collection components."""

    def __init__(self, db: Database):
        self.db = db
        self.broker = BrokerClient()
        self.yahoo = YahooFinanceClient()
        self.ws_feed = MarketDataFeed(db)
        self.rss = RSSIngestor(db)
        self.twitter = TwitterIngestor(db)
        self.events = StructuredEventFeed(db)

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    async def _bootstrap_watchlist(self):
        """
        Seed the watchlist with:
        1. Currently held positions from Alpaca.
        2. Default watchlist from config.
        3. Fetch metadata (name, sector) from Yahoo Finance for each symbol.
        """
        logger.info("Bootstrapping watchlist …")

        # Collect all symbols
        symbols_seen: set[str] = set()

        # From live positions
        positions = await self.broker.get_positions()
        for pos in positions:
            sym = pos.get("symbol", "").upper()
            if sym:
                symbols_seen.add(sym)
                logger.info("Position detected: %s (qty=%.4f)", sym, pos.get("qty", 0))

        # From config watchlist
        for sym in config.watchlist:
            symbols_seen.add(sym.upper())

        # Fetch metadata and upsert
        logger.info("Fetching Yahoo Finance metadata for %d symbols …", len(symbols_seen))
        for symbol in sorted(symbols_seen):
            try:
                info = await self.yahoo.get_quote_summary(symbol)
                self.db.upsert_watchlist({
                    "symbol": symbol,
                    "name": info.get("name"),
                    "sector": info.get("sector"),
                    "industry": info.get("industry"),
                    "market_cap": info.get("market_cap"),
                    "beta": info.get("beta"),
                    "active": 1,
                })
            except Exception as exc:
                logger.warning("Metadata fetch failed for %s: %s", symbol, exc)
                self.db.upsert_watchlist({"symbol": symbol, "active": 1})
            await asyncio.sleep(0.3)  # gentle rate limiting

        # Subscribe WebSocket to all symbols
        all_symbols = self.db.get_active_symbols()
        self.ws_feed.set_symbols(all_symbols)
        logger.info("Watchlist bootstrapped: %d symbols", len(all_symbols))

    # ------------------------------------------------------------------
    # Periodic tasks
    # ------------------------------------------------------------------

    async def _watchlist_refresh_loop(self, interval_seconds: int = 3600):
        """Refresh Yahoo Finance metadata for all watchlist symbols hourly."""
        while True:
            await asyncio.sleep(interval_seconds)
            symbols = self.db.get_active_symbols()
            logger.info("Watchlist metadata refresh: %d symbols", len(symbols))
            for symbol in symbols:
                try:
                    info = await self.yahoo.get_quote_summary(symbol)
                    self.db.upsert_watchlist({
                        "symbol": symbol,
                        "name": info.get("name"),
                        "sector": info.get("sector"),
                        "industry": info.get("industry"),
                        "market_cap": info.get("market_cap"),
                        "beta": info.get("beta"),
                        "active": 1,
                    })
                except Exception as exc:
                    logger.warning("Metadata refresh failed for %s: %s", symbol, exc)
                await asyncio.sleep(0.3)

            # Update WebSocket subscription if new symbols were added
            updated = self.db.get_active_symbols()
            self.ws_feed.set_symbols(updated)

    async def _daily_snapshot_loop(self):
        """
        Every weekday at ~4:30pm ET (21:30 UTC), fetch all positions
        and store a portfolio snapshot in the database.
        """
        while True:
            now = datetime.now(timezone.utc)
            # Calculate seconds until next 21:30 UTC
            target = now.replace(
                hour=_SNAPSHOT_HOUR_UTC,
                minute=_SNAPSHOT_MINUTE_UTC,
                second=0,
                microsecond=0,
            )
            import datetime as _dt
            if now >= target:
                # Already past today's snapshot time — schedule for tomorrow
                target = target + _dt.timedelta(days=1)

            wait_seconds = (target - now).total_seconds()
            logger.debug("Next portfolio snapshot in %.0f seconds", wait_seconds)
            await asyncio.sleep(wait_seconds)

            # Only snapshot on weekdays (Monday=0 … Friday=4)
            today = datetime.now(timezone.utc)
            if today.weekday() >= 5:
                logger.debug("Skipping portfolio snapshot — weekend")
                continue

            try:
                await self._take_portfolio_snapshot()
            except Exception as exc:
                logger.error("Portfolio snapshot failed: %s", exc)

    async def _take_portfolio_snapshot(self):
        """Fetch current account state and persist it."""
        account = await self.broker.get_account()
        positions = await self.broker.get_positions()

        total_value = account.get("portfolio_value", 0.0)
        cash = account.get("cash", 0.0)

        # Compute total unrealised PnL
        total_pnl = sum(float(p.get("unrealized_pl", 0)) for p in positions)

        # Daily PnL: compare to last snapshot if available
        last = self.db.get_latest_portfolio_snapshot()
        if last:
            daily_pnl = total_value - float(last.get("total_value", total_value))
        else:
            daily_pnl = 0.0

        self.db.log_portfolio_snapshot(
            total_value=total_value,
            cash=cash,
            positions=positions,
            daily_pnl=daily_pnl,
            total_pnl=total_pnl,
        )
        logger.info(
            "Portfolio snapshot saved: value=$%.2f cash=$%.2f daily_pnl=$%+.2f total_pnl=$%+.2f",
            total_value, cash, daily_pnl, total_pnl,
        )

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    async def run_forever(self):
        """Start all pipeline components."""
        await self._bootstrap_watchlist()

        tasks = [
            asyncio.create_task(self.ws_feed.run_forever(), name="ws_feed"),
            asyncio.create_task(self.rss.run_forever(), name="rss"),
            asyncio.create_task(self.twitter.run_forever(), name="twitter"),
            asyncio.create_task(self.events.run_forever(), name="events"),
            asyncio.create_task(self._watchlist_refresh_loop(), name="watchlist_refresh"),
            asyncio.create_task(self._daily_snapshot_loop(), name="daily_snapshot"),
        ]
        logger.info("Layer 1 data pipeline running (%d tasks)", len(tasks))
        try:
            await asyncio.gather(*tasks)
        finally:
            await self.broker.close()
            await self.yahoo.close()

    def stop(self):
        self.ws_feed.stop()
        self.rss.stop()
        self.twitter.stop()
