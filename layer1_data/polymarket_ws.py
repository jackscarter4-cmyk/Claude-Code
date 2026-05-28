"""
Layer 1 — Alpaca IEX WebSocket market data feed.

Connects to the Alpaca IEX streaming endpoint and streams real-time
trade and quote updates for all subscribed symbols, persisting each
tick to the database.  Falls back to polling Yahoo Finance every 30s
if the WebSocket connection fails persistently.

Alpaca WS docs: https://docs.alpaca.markets/reference/stocklatestquotes
"""

import asyncio
import json
import logging
from typing import Callable, Optional

import websockets

from config.settings import config
from layer1_data.database import Database
from utils.broker_client import YahooFinanceClient

logger = logging.getLogger(__name__)


class MarketDataFeed:
    """
    Subscribes to trade and quote streams for a list of equity symbols
    via the Alpaca IEX WebSocket and fires a callback on every tick.

    Falls back to polling Yahoo Finance every 30 s if the WebSocket
    cannot be established or repeatedly disconnects.
    """

    WS_URL = config.alpaca.ws_url

    def __init__(self, db: Database, on_tick: Optional[Callable] = None):
        self.db = db
        self.on_tick = on_tick
        self._symbols: list[str] = []
        self._running = False
        self._yahoo = YahooFinanceClient()

    # ------------------------------------------------------------------
    # Symbol registration
    # ------------------------------------------------------------------

    def set_symbols(self, symbols: list[str]):
        """Replace the full list of symbols to subscribe to."""
        self._symbols = list(symbols)
        logger.info("MarketDataFeed: registered %d symbols", len(self._symbols))

    def add_symbol(self, symbol: str):
        """Add a single symbol without replacing the full list."""
        if symbol not in self._symbols:
            self._symbols.append(symbol)

    # ------------------------------------------------------------------
    # WebSocket helpers
    # ------------------------------------------------------------------

    def _auth_message(self) -> str:
        return json.dumps({
            "action": "auth",
            "key": config.alpaca.api_key,
            "secret": config.alpaca.api_secret,
        })

    def _subscribe_message(self) -> str:
        return json.dumps({
            "action": "subscribe",
            "trades": self._symbols,
            "quotes": self._symbols,
        })

    async def _handle_message(self, raw: str):
        """Parse an Alpaca WS message and persist relevant ticks."""
        try:
            messages = json.loads(raw)
        except json.JSONDecodeError:
            return

        if not isinstance(messages, list):
            messages = [messages]

        for msg in messages:
            msg_type = msg.get("T", "")

            if msg_type == "t":
                # Trade tick
                symbol = msg.get("S", "")
                price_raw = msg.get("p")
                size = msg.get("s", 0)
                if symbol and price_raw is not None:
                    try:
                        price = float(price_raw)
                    except (TypeError, ValueError):
                        continue
                    self.db.insert_price_tick(symbol, symbol, price, "TRADE")
                    if self.on_tick:
                        self.on_tick(symbol=symbol, price=price, size=size, tick_type="TRADE")
                    logger.debug("Trade tick %s = $%.4f (size=%s)", symbol, price, size)

            elif msg_type == "q":
                # Quote tick — store mid-price
                symbol = msg.get("S", "")
                ask = msg.get("ap")
                bid = msg.get("bp")
                if symbol and ask is not None and bid is not None:
                    try:
                        mid = (float(ask) + float(bid)) / 2.0
                    except (TypeError, ValueError):
                        continue
                    self.db.insert_price_tick(symbol, symbol, mid, "QUOTE")
                    if self.on_tick:
                        self.on_tick(symbol=symbol, price=mid, ask=float(ask), bid=float(bid), tick_type="QUOTE")
                    logger.debug("Quote tick %s ask=$%.4f bid=$%.4f", symbol, float(ask), float(bid))

            elif msg_type == "error":
                logger.warning("Alpaca WS error message: %s", msg)

    # ------------------------------------------------------------------
    # Yahoo Finance polling fallback
    # ------------------------------------------------------------------

    async def _poll_yahoo_forever(self):
        """Poll Yahoo Finance for prices every 30 s as a fallback."""
        interval = config.yahoo.poll_interval_seconds
        logger.info("MarketDataFeed: falling back to Yahoo Finance polling (every %ds)", interval)
        while self._running:
            if self._symbols:
                prices = await self._yahoo.get_prices_bulk(self._symbols)
                for symbol, price in prices.items():
                    if price is not None:
                        self.db.insert_price_tick(symbol, symbol, price, "TRADE")
                        if self.on_tick:
                            self.on_tick(symbol=symbol, price=price, tick_type="POLL")
                        logger.debug("Yahoo poll %s = $%.4f", symbol, price)
            await asyncio.sleep(interval)

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run_forever(self):
        """Connect to Alpaca IEX WebSocket and stream ticks indefinitely.

        Reconnects with exponential back-off on any error.
        Switches to Yahoo Finance polling if 5 consecutive failures occur.
        """
        self._running = True
        backoff = 1
        consecutive_failures = 0

        while self._running:
            if consecutive_failures >= 5:
                logger.warning(
                    "MarketDataFeed: 5 consecutive WS failures — switching to Yahoo polling"
                )
                await self._poll_yahoo_forever()
                break

            try:
                logger.info("MarketDataFeed: connecting to Alpaca IEX WebSocket …")
                async with websockets.connect(
                    self.WS_URL,
                    ping_interval=20,
                    ping_timeout=30,
                ) as ws:
                    backoff = 1
                    consecutive_failures = 0

                    # Step 1: Authenticate
                    await ws.send(self._auth_message())
                    auth_raw = await ws.recv()
                    logger.debug("Alpaca WS auth response: %s", auth_raw)

                    # Step 2: Subscribe to symbols
                    if self._symbols:
                        await ws.send(self._subscribe_message())
                        sub_raw = await ws.recv()
                        logger.info(
                            "MarketDataFeed: subscribed to %d symbols — %s",
                            len(self._symbols),
                            sub_raw[:200],
                        )

                    # Step 3: Consume messages
                    async for raw in ws:
                        if not self._running:
                            break
                        await self._handle_message(raw)

            except Exception as exc:
                if not self._running:
                    break
                consecutive_failures += 1
                logger.warning(
                    "MarketDataFeed WS error (%d): %s — reconnecting in %ds",
                    consecutive_failures, exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def stop(self):
        self._running = False
        logger.info("MarketDataFeed stopped")
