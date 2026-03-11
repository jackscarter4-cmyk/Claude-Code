"""
Polymarket REST API client wrapper.

Covers:
- Fetching all active markets (CLOB + Gamma API)
- Placing / cancelling limit orders
- Querying open positions and order book
- Account balance checks

Docs: https://docs.polymarket.com/
"""

import asyncio
import hashlib
import hmac
import logging
import time
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp

from config.settings import config

logger = logging.getLogger(__name__)


class PolymarketClient:
    """Async wrapper around the Polymarket CLOB REST API."""

    def __init__(self):
        self.rest_host = config.polymarket.rest_host
        self.gamma_api = config.polymarket.gamma_api
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Authentication helpers
    # ------------------------------------------------------------------

    def _build_headers(self, method: str, path: str, body: str = "") -> dict:
        """Build HMAC-signed request headers for authenticated endpoints."""
        timestamp = str(int(time.time() * 1000))
        message = timestamp + method.upper() + path + body
        signature = hmac.new(
            config.polymarket.api_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            "POLY-ADDRESS": config.polymarket.wallet_address,
            "POLY-SIGNATURE": signature,
            "POLY-TIMESTAMP": timestamp,
            "POLY-API-KEY": config.polymarket.api_key,
            "POLY-PASSPHRASE": config.polymarket.api_passphrase,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------

    async def get_markets(
        self,
        active: bool = True,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """Fetch a page of markets from the Gamma API."""
        session = await self._get_session()
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if active:
            params["active"] = "true"
        url = f"{self.gamma_api}/markets?" + urlencode(params)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as exc:
            logger.error("get_markets failed: %s", exc)
            return []

    async def get_all_active_markets(self) -> list[dict]:
        """Page through all active markets and return the full list."""
        all_markets: list[dict] = []
        offset = 0
        limit = 500
        while True:
            page = await self.get_markets(active=True, limit=limit, offset=offset)
            if not page:
                break
            all_markets.extend(page)
            if len(page) < limit:
                break
            offset += limit
            await asyncio.sleep(0.2)  # gentle rate limiting
        return all_markets

    async def get_market(self, market_id: str) -> Optional[dict]:
        """Fetch a single market by its condition ID."""
        session = await self._get_session()
        url = f"{self.gamma_api}/markets/{market_id}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as exc:
            logger.error("get_market(%s) failed: %s", market_id, exc)
            return None

    async def get_order_book(self, token_id: str) -> Optional[dict]:
        """Fetch the live order book for a token."""
        session = await self._get_session()
        url = f"{self.rest_host}/book?token_id={token_id}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as exc:
            logger.error("get_order_book(%s) failed: %s", token_id, exc)
            return None

    async def get_midpoint_price(self, token_id: str) -> Optional[float]:
        """Return the midpoint price for a token (0–1 scale)."""
        book = await self.get_order_book(token_id)
        if not book:
            return None
        try:
            best_bid = float(book["bids"][0]["price"]) if book.get("bids") else 0.0
            best_ask = float(book["asks"][0]["price"]) if book.get("asks") else 1.0
            return (best_bid + best_ask) / 2.0
        except (KeyError, IndexError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Authenticated endpoints
    # ------------------------------------------------------------------

    async def get_balance(self) -> float:
        """Return the USDC balance of the connected wallet."""
        session = await self._get_session()
        path = "/balance"
        url = self.rest_host + path
        headers = self._build_headers("GET", path)
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return float(data.get("balance", 0))
        except Exception as exc:
            logger.error("get_balance failed: %s", exc)
            return 0.0

    async def get_open_orders(self) -> list[dict]:
        """Return all open (unfilled) orders."""
        session = await self._get_session()
        path = "/orders"
        url = self.rest_host + path
        headers = self._build_headers("GET", path)
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as exc:
            logger.error("get_open_orders failed: %s", exc)
            return []

    async def get_positions(self) -> list[dict]:
        """Return all open positions."""
        session = await self._get_session()
        path = "/positions"
        url = self.rest_host + path
        headers = self._build_headers("GET", path)
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as exc:
            logger.error("get_positions failed: %s", exc)
            return []

    async def place_limit_order(
        self,
        token_id: str,
        side: str,       # "BUY" or "SELL"
        price: float,    # 0–1
        size: float,     # USDC amount
    ) -> Optional[dict]:
        """Place a limit order. Returns the order response or None."""
        import json

        session = await self._get_session()
        path = "/order"
        body_dict = {
            "token_id": token_id,
            "side": side,
            "price": str(round(price, 4)),
            "size": str(round(size, 2)),
            "type": "GTC",  # Good-Till-Cancelled
        }
        body = json.dumps(body_dict)
        headers = self._build_headers("POST", path, body)
        url = self.rest_host + path
        try:
            async with session.post(
                url, data=body, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                resp.raise_for_status()
                result = await resp.json()
                logger.info("Order placed: %s %s @ %.4f × %.2f", side, token_id[:8], price, size)
                return result
        except Exception as exc:
            logger.error("place_limit_order failed: %s", exc)
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID."""
        import json

        session = await self._get_session()
        path = f"/order/{order_id}"
        headers = self._build_headers("DELETE", path)
        url = self.rest_host + path
        try:
            async with session.delete(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.error("cancel_order(%s) failed: %s", order_id, exc)
            return False

    async def cancel_all_orders(self) -> int:
        """Cancel all open orders. Returns count cancelled."""
        orders = await self.get_open_orders()
        cancelled = 0
        for order in orders:
            if await self.cancel_order(order["id"]):
                cancelled += 1
        return cancelled
