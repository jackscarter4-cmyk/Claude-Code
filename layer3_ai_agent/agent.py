"""
Layer 3 — AI Stock Trading Agent main loop.

Runs every 15 minutes during market hours + 2 hours pre/post market:
1. Fetch all watchlist symbols.
2. Filter out symbols with oversized positions (> 8% portfolio weight).
3. Get current prices via Yahoo Finance.
4. Gather market metadata from Yahoo Finance.
5. Run Claude analysis on each symbol.
6. Execute trades that pass risk checks.
"""

import asyncio
import logging
from datetime import datetime, timezone

from config.settings import config
from layer1_data.database import Database
from layer3_ai_agent.market_analyzer import StockAnalyzer, StockAnalysis
from layer3_ai_agent.trade_executor import TradeExecutor
from utils.broker_client import BrokerClient, YahooFinanceClient

logger = logging.getLogger(__name__)


def _is_active_window() -> bool:
    """
    Return True during market hours plus 2-hour pre/post-market window.
    Market hours ET: 9:30am - 4:00pm.
    Active window ET: 7:30am - 6:00pm = 11:30 - 22:00 UTC.
    """
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    hour, minute = now.hour, now.minute
    # 11:30 UTC = 7:30am ET (EDT)
    after_premarket = (hour > 11) or (hour == 11 and minute >= 30)
    # 22:00 UTC = 6:00pm ET
    before_postmarket_close = hour < 22
    return after_premarket and before_postmarket_close


class AITradingAgent:
    """
    The core Layer 3 agent. Autonomous stock analysis and directional trader.
    """

    def __init__(self, db: Database, broker: BrokerClient, risk_guardian=None):
        self.db = db
        self.broker = broker
        self.yahoo = YahooFinanceClient()
        self.analyzer = StockAnalyzer(db)
        self.executor = TradeExecutor(db, broker, risk_guardian)
        self._running = False

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _filter_oversized(
        self,
        symbols: list,
        position_data: dict,
        portfolio_value: float,
    ) -> list:
        """
        Skip symbols where the current position already exceeds the
        max_single_stock_fraction threshold (default 8%).
        """
        result = []
        max_frac = config.risk.max_single_stock_fraction
        for sym in symbols:
            pos = position_data.get(sym, {})
            market_val = float(pos.get("market_value", 0))
            weight = market_val / portfolio_value if portfolio_value > 0 else 0.0
            if weight > max_frac:
                logger.debug(
                    "Skipping %s — position weight %.1f%% exceeds max %.1f%%",
                    sym, weight * 100, max_frac * 100,
                )
                continue
            result.append(sym)
        return result

    # ------------------------------------------------------------------
    # Analysis helpers
    # ------------------------------------------------------------------

    async def _analyze_one(
        self,
        symbol: str,
        price: float,
        market_info: dict,
        position_info: dict,
    ):
        """Run AI analysis for one symbol. Returns StockAnalysis or None."""
        if price <= 0:
            return None
        return await self.analyzer.analyze_stock(
            symbol=symbol,
            current_price=price,
            market_info=market_info,
            position_info=position_info,
        )

    async def _analyze_batch(
        self,
        symbols: list,
        prices: dict,
        market_infos: dict,
        position_data: dict,
        batch_size: int = 3,
    ) -> list:
        """Analyze symbols in small batches to respect API rate limits."""
        results = []
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i: i + batch_size]
            tasks = [
                self._analyze_one(
                    sym,
                    prices.get(sym, 0.0),
                    market_infos.get(sym, {}),
                    position_data.get(sym, {}),
                )
                for sym in batch
            ]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in batch_results:
                if isinstance(r, StockAnalysis):
                    results.append(r)
                elif isinstance(r, Exception):
                    logger.debug("Batch analysis error: %s", r)
            await asyncio.sleep(3)  # polite pause between batches
        return results

    # ------------------------------------------------------------------
    # Main cycle
    # ------------------------------------------------------------------

    async def run_once(self):
        """Execute one full analysis cycle."""
        if not _is_active_window():
            logger.debug("AI agent: outside active window, skipping cycle")
            return 0

        logger.info("Layer 3 AI agent — starting analysis cycle")

        # Fetch positions and account info
        raw_positions = await self.broker.get_positions()
        position_data: dict = {p["symbol"]: p for p in raw_positions}
        account = await self.broker.get_account()
        portfolio_value = account.get("portfolio_value", 100_000.0)

        # Watchlist symbols
        symbols = self.db.get_active_symbols()
        if not symbols:
            logger.warning("AI agent: no active symbols in watchlist")
            return 0

        # Filter oversized positions
        eligible = self._filter_oversized(symbols, position_data, portfolio_value)
        logger.info(
            "Symbols: %d total, %d eligible after size filter",
            len(symbols), len(eligible),
        )

        # Fetch current prices
        prices = await self.yahoo.get_prices_bulk(eligible)

        # Fetch market metadata for each symbol
        market_infos: dict = {}
        for sym in eligible:
            try:
                info = await self.yahoo.get_quote_summary(sym)
                market_infos[sym] = info
            except Exception as exc:
                logger.warning("Market info fetch failed for %s: %s", sym, exc)
                market_infos[sym] = {}
            await asyncio.sleep(0.3)

        # Run AI analysis
        analyses = await self._analyze_batch(eligible, prices, market_infos, position_data)

        # Sort: BULLISH HIGH first, then by upside_pct descending
        def _sort_key(a: StockAnalysis):
            conf_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(a.confidence, 0)
            outlook_rank = {"BULLISH": 2, "BEARISH": 1, "NEUTRAL": 0}.get(a.outlook, 0)
            return (conf_rank * outlook_rank, a.upside_pct)

        analyses.sort(key=_sort_key, reverse=True)

        trades_placed = 0
        for analysis in analyses:
            if analysis.outlook in ("BULLISH", "BEARISH") and analysis.confidence in ("HIGH", "MEDIUM"):
                logger.info(
                    "Opportunity: %s | %s %s | target=$%.2f upside=%.1f%% | action=%s",
                    analysis.symbol,
                    analysis.outlook,
                    analysis.confidence,
                    analysis.target_price,
                    analysis.upside_pct,
                    analysis.suggested_action,
                )
                placed = await self.executor.execute(
                    analysis,
                    portfolio_value,
                    current_positions=position_data,
                )
                if placed:
                    trades_placed += 1
                    await asyncio.sleep(1)

        logger.info(
            "AI agent cycle complete: %d analyses, %d trades placed",
            len(analyses), trades_placed,
        )
        return trades_placed

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    async def run_forever(self):
        self._running = True
        interval = config.ai_agent_interval_seconds  # 900s = 15 minutes

        while self._running:
            try:
                await self.run_once()
            except Exception as exc:
                logger.error("AI agent cycle error: %s", exc, exc_info=True)
            logger.info("AI agent sleeping %ds until next cycle", interval)
            await asyncio.sleep(interval)

    def stop(self):
        self._running = False
