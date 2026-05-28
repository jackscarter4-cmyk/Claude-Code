"""
Layer 4 — Reddit monitoring via the public JSON API.

No OAuth required for read-only access to public subreddits.
Polls configured subreddits for new posts and stores them as news items.
"""

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from config.settings import config
from layer1_data.database import Database

logger = logging.getLogger(__name__)

REDDIT_HEADERS = {
    "User-Agent": "StockTradingBot/1.0 (autonomous trading research)"
}


class RedditMonitor:
    """Poll Reddit subreddits for relevant posts."""

    def __init__(self, db: Database, poll_interval: int = 600):
        self.db = db
        self.poll_interval = poll_interval
        self._seen_ids: set[str] = set()
        self._running = False

    async def _fetch_subreddit(
        self, session: aiohttp.ClientSession, subreddit: str
    ):
        url = f"https://www.reddit.com/r/{subreddit}/new.json?limit=25"
        try:
            async with session.get(
                url,
                headers=REDDIT_HEADERS,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            posts = data.get("data", {}).get("children", [])
            for post in posts:
                post_data = post.get("data", {})
                post_id = post_data.get("id", "")
                if post_id in self._seen_ids:
                    continue
                self._seen_ids.add(post_id)

                created = post_data.get("created_utc", 0)
                published = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()

                title = post_data.get("title", "")
                selftext = post_data.get("selftext", "")[:500]
                url_post = f"https://reddit.com{post_data.get('permalink', '')}"

                self.db.insert_news(
                    source=f"reddit/r/{subreddit}",
                    headline=title,
                    body=selftext,
                    url=url_post,
                    published_at=published,
                )
        except Exception as exc:
            logger.debug("Reddit fetch failed for r/%s: %s", subreddit, exc)

    async def run_forever(self):
        self._running = True
        async with aiohttp.ClientSession() as session:
            while self._running:
                tasks = [
                    self._fetch_subreddit(session, sub)
                    for sub in config.sentiment.subreddits
                ]
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.debug("Reddit poll complete — sleeping %ds", self.poll_interval)
                await asyncio.sleep(self.poll_interval)

    def stop(self):
        self._running = False
