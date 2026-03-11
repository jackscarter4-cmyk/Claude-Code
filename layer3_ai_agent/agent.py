"""
Layer 3 — AI Directional Trading Agent main loop.

Runs on a configurable interval (default 10 minutes):
1. Fetch all active markets
2. Filter to tradeable candidates
3. Get current prices
4. Run Claude analysis on promising markets
5. Execute trades that pass risk checks
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from config.settings import config
from layer1_data.database import Database
from layer3_ai_agent.market_analyzer import MarketAnalyzer
from layer3_ai_agent.trade_executor import TradeExecutor
from utils.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)


class AITradingAgent:
    """
    The core Layer 3 agent. Autonomous, event-driven directional trader.
    """

    def __init__(self, db: Database, client: PolymarketClient, risk_guardian=None):
        self.db = db
        self.client = client
        self.analyzer = MarketAnalyzer(db)
        self.executor = TradeExecutor(db, client, risk_guardian)
        self._running = False

    def _is_tradeable(self, market: dict) -> bool:
        """Pre-filter markets before calling the expensive AI analysis."""
        # Minimum volume
        volume = float(market.get("volume", 0) or 0)
        if volume < config.risk.min_market_volume:
            return False

        # Must have both token IDs
        if not market.get("yes_token_id") or not market.get("no_token_id"):
            return False

        # Must not be expired
        end_date_str = market.get("end_date", "")
        if end_date_str:
            try:
                # Handle various date formats
                for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S+00:00", "%Y-%m-%d"):
                    try:
                        end_dt = datetime.strptime(end_date_str[:19], fmt[:len(end_date_str[:19])])
                        end_dt = end_dt.replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        continue
                else:
                    end_dt = None

                if end_dt:
                    min_days = timedelta(days=config.risk.min_days_to_resolution)
                    if datetime.now(timezone.utc) + min_days > end_dt:
                        return False
            except Exception:
                pass

        return True

    async def _analyze_batch(
        self, markets: list[dict], batch_size: int = 5
    ) -> list:
        """
        Analyze markets in small batches to avoid hammering the API.
        Returns list of (MarketAnalysis, market_price) tuples.
        """
        results = []
        for i in range(0, len(markets), batch_size):
            batch = markets[i: i + batch_size]
            tasks = []
            for market in batch:
                # Get the midpoint price
                tasks.append(self._analyze_one(market))
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in batch_results:
                if not isinstance(r, Exception) and r is not None:
                    results.append(r)
            # Polite pause between batches
            await asyncio.sleep(2)
        return results

    async def _analyze_one(self, market: dict):
        """Fetch price and run AI analysis for one market."""
        yes_token = market.get("yes_token_id", "")
        price = await self.client.get_midpoint_price(yes_token)
        if price is None:
            return None

        # Quick filter: skip markets where price is already 0 or 1
        # (near-resolved, little profit potential)
        if price < 0.02 or price > 0.98:
            return None

        return await self.analyzer.analyze_market(market, price)

    async def run_once(self):
        """Execute one full analysis cycle."""
        logger.info("Layer 3 AI agent — starting analysis cycle")

        markets = self.db.get_active_markets()
        tradeable = [m for m in markets if self._is_tradeable(m)]
        logger.info(
            "Markets: %d total, %d tradeable",
            len(markets),
            len(tradeable),
        )

        analyses = await self._analyze_batch(tradeable)

        # Sort by absolute edge (best opportunities first)
        analyses.sort(key=lambda a: abs(a.edge), reverse=True)

        trades_placed = 0
        for analysis in analyses:
            if abs(analysis.edge) >= config.risk.min_edge_threshold:
                logger.info(
                    "Opportunity: %s | AI=%.3f market=%.3f edge=%.3f | %s",
                    analysis.question[:60],
                    analysis.ai_probability,
                    analysis.market_price,
                    analysis.edge,
                    analysis.confidence,
                )
                placed = await self.executor.execute(analysis)
                if placed:
                    trades_placed += 1
                    await asyncio.sleep(0.5)  # brief pause between orders

        logger.info(
            "Cycle complete: %d analyses, %d trades placed",
            len(analyses),
            trades_placed,
        )
        return trades_placed

    async def run_forever(self):
        self._running = True
        interval = config.ai_agent_interval_seconds

        while self._running:
            try:
                await self.run_once()
            except Exception as exc:
                logger.error("AI agent cycle error: %s", exc, exc_info=True)
            logger.info("AI agent sleeping %ds until next cycle", interval)
            await asyncio.sleep(interval)

    def stop(self):
        self._running = False
