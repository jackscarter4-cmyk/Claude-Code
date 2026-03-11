"""
Layer 2 — Market-making engine.

Strategy:
  1. Continuously scan all high-volume binary markets for arb opportunities.
  2. When combined ask(YES) + ask(NO) < $1.00, place limit orders on BOTH sides
     at prices that guarantee a profit if both fill.
  3. Rebalance existing limit orders every `refresh_interval_seconds`.
  4. Respect Risk layer hard limits (max positions, daily loss cap).

This is pure math — no AI required.
"""

import asyncio
import logging
from typing import Optional

from config.settings import config
from layer1_data.database import Database
from layer2_market_making.arbitrage_detector import ArbitrageDetector, ArbOpportunity
from utils.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)


class MarketMaker:
    """
    Layer 2 market-making engine.

    Runs continuously, placing and rebalancing limit orders to capture
    sub-$1.00 binary-market spreads.
    """

    def __init__(self, db: Database, client: PolymarketClient, risk_guardian=None):
        self.db = db
        self.client = client
        self.risk_guardian = risk_guardian  # Layer 5 hook (optional at init)
        self.detector = ArbitrageDetector(client)

        # Track active MM order pairs: market_id -> {"yes_order_id", "no_order_id"}
        self._active_pairs: dict[str, dict] = {}
        self._running = False

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    async def _place_arb_pair(self, opp: ArbOpportunity) -> bool:
        """
        Place matching limit orders on both sides of an arb opportunity.

        We place:
          - BUY YES at yes_ask (or slightly below to be maker)
          - BUY NO  at no_ask  (or slightly below to be maker)

        Since the combined cost < $1.00, one side will always pay out $1.00,
        so the net result is guaranteed profit.
        """
        if self.risk_guardian:
            allowed = await self.risk_guardian.check_layer2_trade(opp)
            if not allowed:
                logger.debug("Risk guardian blocked MM trade for %s", opp.market_id[:8])
                return False

        size = config.market_making.position_size_usdc
        # Place slightly inside the ask to act as maker (save on taker fees)
        yes_price = round(opp.yes_ask - 0.001, 4)
        no_price = round(opp.no_ask - 0.001, 4)

        yes_order, no_order = await asyncio.gather(
            self.client.place_limit_order(opp.yes_token_id, "BUY", yes_price, size),
            self.client.place_limit_order(opp.no_token_id, "BUY", no_price, size),
        )

        if yes_order and no_order:
            self._active_pairs[opp.market_id] = {
                "yes_order_id": yes_order.get("id", ""),
                "no_order_id": no_order.get("id", ""),
                "opp": opp,
            }
            # Log both sides
            for side, order, token, price in [
                ("YES", yes_order, opp.yes_token_id, yes_price),
                ("NO",  no_order,  opp.no_token_id,  no_price),
            ]:
                self.db.log_trade({
                    "layer": "layer2",
                    "market_id": opp.market_id,
                    "token_id": token,
                    "side": f"BUY_{side}",
                    "price": price,
                    "size_usdc": size,
                    "order_id": order.get("id", ""),
                    "reasoning": f"arb: combined_cost={opp.combined_cost:.4f} edge={opp.edge_cents:.2f}c",
                    "outcome": "OPEN",
                })
            logger.info(
                "MM pair placed for %s | edge=%.2f¢",
                opp.question[:50],
                opp.edge_cents,
            )
            return True

        # Clean up partial fills
        if yes_order:
            await self.client.cancel_order(yes_order.get("id", ""))
        if no_order:
            await self.client.cancel_order(no_order.get("id", ""))
        return False

    # ------------------------------------------------------------------
    # Rebalancing
    # ------------------------------------------------------------------

    async def _rebalance(self):
        """
        Cancel stale limit orders that are no longer profitable and
        replace them with fresh prices.
        """
        stale_markets = []
        for market_id, pair in list(self._active_pairs.items()):
            opp: ArbOpportunity = pair["opp"]
            # Re-fetch current best prices
            yes_book, no_book = await asyncio.gather(
                self.client.get_order_book(opp.yes_token_id),
                self.client.get_order_book(opp.no_token_id),
            )
            if not yes_book or not no_book:
                continue
            from layer2_market_making.arbitrage_detector import extract_best_bid_ask
            _, new_yes_ask = extract_best_bid_ask(yes_book)
            _, new_no_ask = extract_best_bid_ask(no_book)
            new_combined = new_yes_ask + new_no_ask

            if new_combined >= 1.0:
                # Opportunity is gone — cancel orders
                stale_markets.append(market_id)
                await asyncio.gather(
                    self.client.cancel_order(pair["yes_order_id"]),
                    self.client.cancel_order(pair["no_order_id"]),
                )
                logger.info("MM orders cancelled (no longer profitable): %s", market_id[:8])

        for mid in stale_markets:
            self._active_pairs.pop(mid, None)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run_forever(self):
        self._running = True
        refresh = config.market_making.refresh_interval_seconds
        max_pairs = config.market_making.max_mm_positions

        while self._running:
            try:
                markets = self.db.get_active_markets()

                # Filter to high-volume markets only
                min_vol = config.risk.min_market_volume
                eligible = [
                    m for m in markets
                    if float(m.get("volume", 0) or 0) >= min_vol
                    and m.get("yes_token_id") and m.get("no_token_id")
                ]

                logger.info(
                    "MM scan: %d eligible markets (%d active pairs)",
                    len(eligible),
                    len(self._active_pairs),
                )

                # Rebalance existing orders first
                await self._rebalance()

                # Scan for new opportunities
                new_opps = await self.detector.scan(eligible)

                for opp in new_opps:
                    if opp.market_id in self._active_pairs:
                        continue
                    if len(self._active_pairs) >= max_pairs:
                        break
                    await self._place_arb_pair(opp)
                    await asyncio.sleep(0.1)  # avoid rate limits

            except Exception as exc:
                logger.error("MM loop error: %s", exc, exc_info=True)

            await asyncio.sleep(refresh)

    def stop(self):
        self._running = False
