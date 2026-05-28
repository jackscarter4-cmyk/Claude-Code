"""
Alpaca REST API broker client + Yahoo Finance fallback.

BrokerClient — authenticated Alpaca REST/data wrapper:
  - Account info, positions, orders
  - Place and cancel orders
  - Historical bars and latest quotes

YahooFinanceClient — unauthenticated Yahoo Finance price fetcher
  used as fallback when Alpaca data is unavailable.

Alpaca API docs: https://docs.alpaca.markets/
"""

import asyncio
import json
import logging
from typing import Any, Optional

import aiohttp

from config.settings import config

logger = logging.getLogger(__name__)


class BrokerClient:
    """Async wrapper around the Alpaca REST API."""

    def __init__(self):
        self.base_url = config.alpaca.base_url.rstrip("/")
        self.data_url = "https://data.alpaca.markets"
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "APCA-API-KEY-ID": config.alpaca.api_key,
                    "APCA-API-SECRET-KEY": config.alpaca.api_secret,
                    "Content-Type": "application/json",
                }
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def get_account(self) -> dict:
        """
        Return account details.
        Keys: buying_power, portfolio_value, cash.
        """
        session = await self._get_session()
        url = f"{self.base_url}/v2/account"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return {
                    "buying_power": float(data.get("buying_power", 0)),
                    "portfolio_value": float(data.get("portfolio_value", 0)),
                    "cash": float(data.get("cash", 0)),
                    "raw": data,
                }
        except Exception as exc:
            logger.error("get_account failed: %s", exc)
            return {"buying_power": 0.0, "portfolio_value": 0.0, "cash": 0.0}

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    async def get_positions(self) -> list[dict]:
        """
        Return all open positions.
        Each dict has: symbol, qty, avg_entry_price, current_price,
        unrealized_pl, unrealized_plpc, market_value.
        """
        session = await self._get_session()
        url = f"{self.base_url}/v2/positions"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                raw_positions = await resp.json()
                positions = []
                for p in raw_positions:
                    positions.append({
                        "symbol": p.get("symbol", ""),
                        "qty": float(p.get("qty", 0)),
                        "avg_entry_price": float(p.get("avg_entry_price", 0)),
                        "current_price": float(p.get("current_price", 0)),
                        "unrealized_pl": float(p.get("unrealized_pl", 0)),
                        "unrealized_plpc": float(p.get("unrealized_plpc", 0)),
                        "market_value": float(p.get("market_value", 0)),
                        "side": p.get("side", "long"),
                    })
                return positions
        except Exception as exc:
            logger.error("get_positions failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def get_orders(self, status: str = "open") -> list[dict]:
        """
        Return orders filtered by status ('open', 'closed', 'all').
        """
        session = await self._get_session()
        url = f"{self.base_url}/v2/orders"
        params = {"status": status, "limit": 500}
        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as exc:
            logger.error("get_orders(status=%s) failed: %s", status, exc)
            return []

    async def place_market_order(self, symbol: str, qty: float, side: str) -> Optional[dict]:
        """
        Place a market order.
        side: "buy" or "sell"
        Returns the order response dict or None on failure.
        """
        session = await self._get_session()
        url = f"{self.base_url}/v2/orders"
        payload = {
            "symbol": symbol,
            "qty": str(round(qty, 6)),
            "side": side.lower(),
            "type": "market",
            "time_in_force": "day",
        }
        try:
            async with session.post(
                url,
                data=json.dumps(payload),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                result = await resp.json()
                logger.info(
                    "Market order placed: %s %s qty=%.4f order_id=%s",
                    side.upper(), symbol, qty, result.get("id", "")[:8],
                )
                return result
        except Exception as exc:
            logger.error("place_market_order(%s %s) failed: %s", side, symbol, exc)
            return None

    async def place_limit_order(
        self, symbol: str, qty: float, side: str, limit_price: float
    ) -> Optional[dict]:
        """
        Place a limit order (GTC).
        side: "buy" or "sell"
        Returns the order response dict or None on failure.
        """
        session = await self._get_session()
        url = f"{self.base_url}/v2/orders"
        payload = {
            "symbol": symbol,
            "qty": str(round(qty, 6)),
            "side": side.lower(),
            "type": "limit",
            "limit_price": str(round(limit_price, 2)),
            "time_in_force": "gtc",
        }
        try:
            async with session.post(
                url,
                data=json.dumps(payload),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                result = await resp.json()
                logger.info(
                    "Limit order placed: %s %s qty=%.4f @ $%.2f order_id=%s",
                    side.upper(), symbol, qty, limit_price, result.get("id", "")[:8],
                )
                return result
        except Exception as exc:
            logger.error(
                "place_limit_order(%s %s @ %.2f) failed: %s", side, symbol, limit_price, exc
            )
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID. Returns True if cancelled."""
        session = await self._get_session()
        url = f"{self.base_url}/v2/orders/{order_id}"
        try:
            async with session.delete(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                # 204 No Content on success
                if resp.status in (200, 204):
                    return True
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.error("cancel_order(%s) failed: %s", order_id[:8], exc)
            return False

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_bars(
        self, symbol: str, timeframe: str = "1Day", limit: int = 50
    ) -> list[dict]:
        """
        Fetch historical OHLCV bars from Alpaca's data API.
        Returns list of dicts with: t, o, h, l, c, v, vw (volume-weighted).
        """
        session = await self._get_session()
        url = f"{self.data_url}/v2/stocks/{symbol}/bars"
        params = {
            "timeframe": timeframe,
            "limit": limit,
            "feed": "iex",
            "adjustment": "raw",
        }
        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                bars = data.get("bars", [])
                result = []
                for b in bars:
                    result.append({
                        "t": b.get("t", ""),
                        "o": float(b.get("o", 0)),
                        "h": float(b.get("h", 0)),
                        "l": float(b.get("l", 0)),
                        "c": float(b.get("c", 0)),
                        "v": float(b.get("v", 0)),
                        "vw": float(b.get("vw", 0)),
                    })
                return result
        except Exception as exc:
            logger.error("get_bars(%s) failed: %s", symbol, exc)
            return []

    async def get_latest_quote(self, symbol: str) -> dict:
        """
        Return the latest quote for a symbol.
        Keys: ask_price, bid_price, last_price.
        """
        session = await self._get_session()
        url = f"{self.data_url}/v2/stocks/{symbol}/quotes/latest"
        params = {"feed": "iex"}
        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                q = data.get("quote", {})
                return {
                    "ask_price": float(q.get("ap", 0)),
                    "bid_price": float(q.get("bp", 0)),
                    "last_price": float(q.get("ap", q.get("bp", 0))),
                }
        except Exception as exc:
            logger.error("get_latest_quote(%s) failed: %s", symbol, exc)
            return {"ask_price": 0.0, "bid_price": 0.0, "last_price": 0.0}


class YahooFinanceClient:
    """
    Unauthenticated Yahoo Finance price client used as fallback.

    Hits: https://query1.finance.yahoo.com/v8/finance/chart/{symbol}
    """

    CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.HEADERS)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_price(self, symbol: str) -> Optional[float]:
        """Return the latest price for a symbol, or None on failure."""
        data = await self.get_chart(symbol)
        if not data:
            return None
        try:
            meta = data["chart"]["result"][0]["meta"]
            price = float(
                meta.get("regularMarketPrice")
                or meta.get("previousClose")
                or 0
            )
            return price if price > 0 else None
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    async def get_chart(self, symbol: str, interval: str = "1d", range_: str = "1mo") -> Optional[dict]:
        """
        Fetch chart data for a symbol.
        interval: '1m', '5m', '1h', '1d', etc.
        range_: '1d', '5d', '1mo', '3mo', '6mo', '1y', etc.
        """
        session = await self._get_session()
        url = f"{self.CHART_URL}/{symbol}"
        params = {"interval": interval, "range": range_}
        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as exc:
            logger.warning("YahooFinanceClient.get_chart(%s) failed: %s", symbol, exc)
            return None

    async def get_quote_summary(self, symbol: str) -> dict:
        """
        Fetch a rich quote summary including sector, industry, beta,
        52W range, dividend yield, EPS estimates, and earnings date.
        Returns a flat dict with available fields; missing fields are None.
        """
        session = await self._get_session()
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
        modules = ",".join([
            "price",
            "summaryDetail",
            "defaultKeyStatistics",
            "assetProfile",
            "financialData",
            "calendarEvents",
            "earningsTrend",
        ])
        params = {"modules": modules}
        result: dict[str, Any] = {
            "symbol": symbol,
            "name": None,
            "sector": None,
            "industry": None,
            "market_cap": None,
            "beta": None,
            "fifty_two_week_high": None,
            "fifty_two_week_low": None,
            "dividend_yield": None,
            "dividend_rate": None,
            "eps_forward": None,
            "earnings_date": None,
            "current_price": None,
            "avg_volume": None,
        }
        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=25)
            ) as resp:
                if resp.status != 200:
                    return result
                data = await resp.json()
                qs = data.get("quoteSummary", {}).get("result", [{}])[0]

                price_mod = qs.get("price", {})
                summary = qs.get("summaryDetail", {})
                key_stats = qs.get("defaultKeyStatistics", {})
                profile = qs.get("assetProfile", {})
                financial = qs.get("financialData", {})
                calendar = qs.get("calendarEvents", {})

                def _val(d: dict, key: str) -> Any:
                    v = d.get(key)
                    if isinstance(v, dict):
                        return v.get("raw")
                    return v

                result["name"] = price_mod.get("longName") or price_mod.get("shortName")
                result["current_price"] = _val(price_mod, "regularMarketPrice")
                result["market_cap"] = _val(price_mod, "marketCap")
                result["sector"] = profile.get("sector")
                result["industry"] = profile.get("industry")
                result["beta"] = _val(summary, "beta") or _val(key_stats, "beta")
                result["fifty_two_week_high"] = _val(summary, "fiftyTwoWeekHigh")
                result["fifty_two_week_low"] = _val(summary, "fiftyTwoWeekLow")
                result["dividend_yield"] = _val(summary, "dividendYield")
                result["dividend_rate"] = _val(summary, "dividendRate")
                result["eps_forward"] = _val(financial, "revenuePerShare")
                result["avg_volume"] = _val(summary, "averageVolume") or _val(
                    price_mod, "averageVolume"
                )

                # Earnings date
                earnings = calendar.get("earnings", {})
                dates = earnings.get("earningsDate", [])
                if dates:
                    first_date = dates[0]
                    if isinstance(first_date, dict):
                        result["earnings_date"] = first_date.get("fmt")
                    else:
                        result["earnings_date"] = str(first_date)

        except Exception as exc:
            logger.warning("get_quote_summary(%s) failed: %s", symbol, exc)

        return result

    async def get_prices_bulk(self, symbols: list[str]) -> dict[str, Optional[float]]:
        """Fetch latest prices for multiple symbols concurrently."""
        tasks = {symbol: self.get_price(symbol) for symbol in symbols}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        return {
            symbol: (r if not isinstance(r, Exception) else None)
            for symbol, r in zip(tasks.keys(), results)
        }
