"""
Layer 1 — Polymarket WebSocket price feed.

Connects to the Polymarket CLOB WebSocket and streams real-time
price updates for all subscribed markets, persisting each tick
to the database.

Polymarket WS docs: https://docs.polymarket.com/#websocket
"""

import asyncio
import json
import logging
from typing import Callable, Optional

import websockets

from config.settings import config
from layer1_data.database import Database

logger = logging.getLogger(__name__)


class PolymarketWebSocket:
    """
    Subscribes to one or more token IDs on the Polymarket price feed
    and fires callbacks on every price update.
    """

    WS_URL = config.polymarket.ws_url

    def __init__(self, db: Database, on_tick: Optional[Callable] = None):
        self.db = db
        self.on_tick = on_tick  # optional external callback
        self._token_to_market: dict[str, str] = {}  # token_id -> market_id
        self._running = False

    def register_market(self, market_id: str, yes_token_id: str, no_token_id: str):
        """Register a market so incoming ticks can be attributed correctly."""
        if yes_token_id:
            self._token_to_market[yes_token_id] = (market_id, "YES")
        if no_token_id:
            self._token_to_market[no_token_id] = (market_id, "NO")

    def register_markets_from_db(self):
        """Bulk-register all active markets stored in the database."""
        markets = self.db.get_active_markets()
        for m in markets:
            self.register_market(m["id"], m.get("yes_token_id", ""), m.get("no_token_id", ""))
        logger.info("Registered %d markets for WebSocket", len(markets))

    async def _subscribe_message(self) -> str:
        """Build the subscription payload for all registered token IDs."""
        token_ids = list(self._token_to_market.keys())
        # Polymarket WS expects a subscription per asset
        payload = {
            "type": "subscribe",
            "channel": "market",
            "assets_ids": token_ids,
        }
        return json.dumps(payload)

    async def _handle_message(self, raw: str):
        """Parse a WebSocket message and persist / broadcast the tick."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = data.get("event_type") or data.get("type", "")

        # Price update events
        if msg_type in ("price_change", "book", "last_trade_price"):
            token_id = data.get("asset_id") or data.get("token_id", "")
            price_raw = data.get("price") or data.get("last_trade_price")
            if not token_id or price_raw is None:
                return

            try:
                price = float(price_raw)
            except (TypeError, ValueError):
                return

            meta = self._token_to_market.get(token_id)
            if meta:
                market_id, side = meta
                self.db.insert_price_tick(market_id, token_id, price, side)
                if self.on_tick:
                    self.on_tick(market_id=market_id, token_id=token_id, price=price, side=side)
                logger.debug("Tick %s %s = %.4f", market_id[:8], side, price)

    async def run_forever(self):
        """Connect and listen, reconnecting on any error."""
        self._running = True
        backoff = 1
        while self._running:
            try:
                logger.info("Connecting to Polymarket WebSocket …")
                async with websockets.connect(self.WS_URL, ping_interval=20) as ws:
                    backoff = 1  # reset after successful connect
                    sub_msg = await self._subscribe_message()
                    await ws.send(sub_msg)
                    logger.info(
                        "Subscribed to %d tokens", len(self._token_to_market)
                    )
                    async for raw in ws:
                        if not self._running:
                            break
                        await self._handle_message(raw)
            except Exception as exc:
                if not self._running:
                    break
                logger.warning("WebSocket error: %s — reconnecting in %ds", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def stop(self):
        self._running = False
