"""
Layer 2 — Arbitrage opportunity detector.

On Polymarket binary markets:
  - YES token + NO token = $1.00 at resolution
  - If combined best-ask(YES) + best-ask(NO) < $1.00, a risk-free profit exists

This module scans the live order-book for those discounts.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from config.settings import config

logger = logging.getLogger(__name__)


@dataclass
class ArbOpportunity:
    market_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    yes_ask: float       # best ask for YES side
    no_ask: float        # best ask for NO side
    combined_cost: float # yes_ask + no_ask — must be < 1.0 for profit
    edge_cents: float    # (1.0 - combined_cost) * 100
    yes_bid: float = 0.0
    no_bid: float = 0.0


def extract_best_bid_ask(order_book: dict) -> tuple[float, float]:
    """Return (best_bid, best_ask) from a Polymarket order-book response."""
    try:
        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])
        best_bid = float(bids[0]["price"]) if bids else 0.0
        best_ask = float(asks[0]["price"]) if asks else 1.0
        return best_bid, best_ask
    except (KeyError, IndexError, ValueError, TypeError):
        return 0.0, 1.0


class ArbitrageDetector:
    """
    Scans a set of binary markets for sub-$1.00 pair opportunities.

    Usage:
        detector = ArbitrageDetector(polymarket_client)
        opps = await detector.scan(markets)
    """

    def __init__(self, polymarket_client):
        self.client = polymarket_client
        self.max_combined_cost = config.market_making.max_combined_cost

    async def _check_market(self, market: dict) -> Optional[ArbOpportunity]:
        """
        Fetch the order books for both sides of a binary market and check
        for an arbitrage opportunity.
        """
        yes_token = market.get("yes_token_id", "")
        no_token = market.get("no_token_id", "")
        if not yes_token or not no_token:
            return None

        # Fetch both books concurrently
        import asyncio
        yes_book, no_book = await asyncio.gather(
            self.client.get_order_book(yes_token),
            self.client.get_order_book(no_token),
        )
        if yes_book is None or no_book is None:
            return None

        yes_bid, yes_ask = extract_best_bid_ask(yes_book)
        no_bid, no_ask = extract_best_bid_ask(no_book)

        combined = yes_ask + no_ask
        if combined < self.max_combined_cost:
            edge = (1.0 - combined) * 100  # in cents
            logger.info(
                "ARB FOUND: %s | combined=%.4f | edge=%.2f¢",
                market.get("question", market["id"])[:60],
                combined,
                edge,
            )
            return ArbOpportunity(
                market_id=market["id"],
                question=market.get("question", ""),
                yes_token_id=yes_token,
                no_token_id=no_token,
                yes_ask=yes_ask,
                no_ask=no_ask,
                combined_cost=combined,
                edge_cents=edge,
                yes_bid=yes_bid,
                no_bid=no_bid,
            )
        return None

    async def scan(self, markets: list[dict]) -> list[ArbOpportunity]:
        """
        Scan all provided markets for arbitrage opportunities.
        Returns a list of opportunities sorted by edge (largest first).
        """
        import asyncio
        tasks = [self._check_market(m) for m in markets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        opportunities = []
        for r in results:
            if isinstance(r, ArbOpportunity):
                opportunities.append(r)
            elif isinstance(r, Exception):
                logger.debug("Scan error: %s", r)

        opportunities.sort(key=lambda o: o.edge_cents, reverse=True)
        logger.info("Scan complete: %d opportunities found", len(opportunities))
        return opportunities
