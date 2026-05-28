#!/usr/bin/env python3
"""
Offline Daily Stock Quant Screener
===================================
No API keys, no internet, no broker account required.

Scores each stock on the Alpha Picks 5-factor framework using real
institutional formulas, then prints a ranked buy/sell/hold report.

USAGE
-----
  # Generate an empty template to fill in:
  python daily_check.py --template

  # Run analysis on a filled-in CSV:
  python daily_check.py --input my_stocks.csv

  # Save report to file:
  python daily_check.py --input my_stocks.csv --output report.txt

  # Interactive wizard (enter one stock at a time):
  python daily_check.py --wizard

WEBULL IMPORT
-------------
Webull gives you positions (symbol, price, cost basis, shares) but NOT
fundamentals (P/E, ROE, EPS revisions). So the flow is two steps: import
your holdings, then fill in fundamentals from Yahoo Finance.

  # From a Webull positions JSON dump (unofficial API or OpenAPI shape):
  python daily_check.py --webull-json positions.json --export my_stocks.csv

  # From a Webull order-history CSV export (Account -> History -> Export):
  python daily_check.py --webull-csv webull_orders.csv --export my_stocks.csv

Then open my_stocks.csv, fill in the fundamental columns from Yahoo Finance,
and score it:
  python daily_check.py --input my_stocks.csv

INPUT CSV COLUMNS
-----------------
Required: symbol, price

Valuation (Value score):
  pe_ratio          — Trailing P/E ratio
  forward_pe        — Forward P/E ratio
  pb_ratio          — Price-to-Book ratio
  ev_ebitda         — EV/EBITDA multiple

Growth (Growth score):
  revenue_growth_yoy — Revenue growth year-over-year (decimal, e.g. 0.12 = 12%)
  eps_growth_yoy     — EPS growth year-over-year (decimal)
  eps_growth_3y      — 3-year annualized EPS growth (decimal)

Profitability (Profitability score):
  roe               — Return on Equity (decimal)
  roic              — Return on Invested Capital (decimal)
  gross_margin      — Gross profit margin (decimal)
  net_margin        — Net profit margin (decimal)

EPS Revisions:
  eps_rev_30d       — % change in consensus EPS estimate, last 30 days (decimal)
  eps_rev_60d       — % change in consensus EPS estimate, last 60 days (decimal)
  eps_rev_90d       — % change in consensus EPS estimate, last 90 days (decimal)

Momentum:
  price_12m_ago     — Stock price 12 months ago
  price_1m_ago      — Stock price 1 month ago
  rsi_14            — 14-day RSI (0-100)

Context (optional, used for grouping/display):
  sector            — e.g. Technology, Healthcare
  market_cap_b      — Market cap in billions
  dividend_yield    — Annual dividend yield (decimal)
  cost_basis        — Your purchase price (shows P&L column)
  shares_held       — Shares you hold (shows position value)
  notes             — Free text notes
"""

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class StockData:
    symbol: str
    price: float

    # Valuation
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    pb_ratio: Optional[float] = None
    ev_ebitda: Optional[float] = None

    # Growth
    revenue_growth_yoy: Optional[float] = None
    eps_growth_yoy: Optional[float] = None
    eps_growth_3y: Optional[float] = None

    # Profitability
    roe: Optional[float] = None
    roic: Optional[float] = None
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None

    # EPS Revisions
    eps_rev_30d: Optional[float] = None
    eps_rev_60d: Optional[float] = None
    eps_rev_90d: Optional[float] = None

    # Momentum
    price_12m_ago: Optional[float] = None
    price_1m_ago: Optional[float] = None
    rsi_14: Optional[float] = None

    # Context
    sector: Optional[str] = None
    market_cap_b: Optional[float] = None
    dividend_yield: Optional[float] = None
    cost_basis: Optional[float] = None
    shares_held: Optional[float] = None
    notes: Optional[str] = None


@dataclass
class ScoreResult:
    symbol: str
    price: float
    sector: Optional[str]

    score_value: Optional[float] = None
    score_growth: Optional[float] = None
    score_profitability: Optional[float] = None
    score_eps_revisions: Optional[float] = None
    score_momentum: Optional[float] = None
    composite: Optional[float] = None

    # Sub-metrics stored for report detail
    momentum_12_1: Optional[float] = None
    rsi_14: Optional[float] = None
    roe: Optional[float] = None
    net_margin: Optional[float] = None
    eps_rev_30d: Optional[float] = None
    revenue_growth_yoy: Optional[float] = None

    cost_basis: Optional[float] = None
    shares_held: Optional[float] = None
    notes: Optional[str] = None

    verdict: str = "HOLD"
    confidence: str = "LOW"


# ---------------------------------------------------------------------------
# Quant scorer
# ---------------------------------------------------------------------------

class QuantScorer:
    """
    Implements the Alpha Picks / Seeking Alpha Quant style 5-factor framework.

    Each factor is scored 0-10 using z-score normalization across the
    provided universe, then clipped to [0, 10].

    Formula reference:
      - Value:         lower P/E, P/B, EV/EBITDA relative to peers = higher score
      - Growth:        higher revenue/EPS growth = higher score
      - Profitability: higher ROE, ROIC, gross/net margin = higher score
      - EPS Revisions: positive estimate revisions over 30/60/90d = higher score
      - Momentum:      12-1 month price return + RSI = higher score
    """

    def _safe(self, values: list) -> list:
        """Filter out None values."""
        return [v for v in values if v is not None]

    def _mean(self, values: list) -> float:
        v = self._safe(values)
        return sum(v) / len(v) if v else 0.0

    def _std(self, values: list) -> float:
        v = self._safe(values)
        if len(v) < 2:
            return 1.0
        m = self._mean(v)
        variance = sum((x - m) ** 2 for x in v) / len(v)
        return math.sqrt(variance) or 1.0

    def _zscore_to_score(self, value: float, universe: list, invert: bool = False) -> float:
        """
        Convert a raw metric to a 0-10 score via z-score normalization.
        invert=True for metrics where lower = better (e.g. P/E ratio).
        """
        u = self._safe(universe)
        if not u:
            return 5.0
        mu = self._mean(u)
        sigma = self._std(u)
        z = (value - mu) / sigma
        if invert:
            z = -z
        # Map z-score to 0-10: z=-3 → 0, z=0 → 5, z=+3 → 10
        score = 5.0 + (z * 5.0 / 3.0)
        return max(0.0, min(10.0, score))

    def _score_value(self, stock: StockData, universe: list[StockData]) -> Optional[float]:
        """Lower valuation multiples relative to peers → higher score."""
        parts = []

        if stock.pe_ratio is not None and stock.pe_ratio > 0:
            all_pe = [s.pe_ratio for s in universe if s.pe_ratio and s.pe_ratio > 0]
            parts.append(self._zscore_to_score(stock.pe_ratio, all_pe, invert=True))

        if stock.forward_pe is not None and stock.forward_pe > 0:
            all_fpe = [s.forward_pe for s in universe if s.forward_pe and s.forward_pe > 0]
            parts.append(self._zscore_to_score(stock.forward_pe, all_fpe, invert=True))

        if stock.pb_ratio is not None and stock.pb_ratio > 0:
            all_pb = [s.pb_ratio for s in universe if s.pb_ratio and s.pb_ratio > 0]
            parts.append(self._zscore_to_score(stock.pb_ratio, all_pb, invert=True))

        if stock.ev_ebitda is not None and stock.ev_ebitda > 0:
            all_ev = [s.ev_ebitda for s in universe if s.ev_ebitda and s.ev_ebitda > 0]
            parts.append(self._zscore_to_score(stock.ev_ebitda, all_ev, invert=True))

        if not parts:
            return None
        return round(sum(parts) / len(parts), 2)

    def _score_growth(self, stock: StockData, universe: list[StockData]) -> Optional[float]:
        """Higher revenue and EPS growth → higher score."""
        parts = []

        if stock.revenue_growth_yoy is not None:
            all_rev = [s.revenue_growth_yoy for s in universe if s.revenue_growth_yoy is not None]
            parts.append(self._zscore_to_score(stock.revenue_growth_yoy, all_rev))

        if stock.eps_growth_yoy is not None:
            all_eps = [s.eps_growth_yoy for s in universe if s.eps_growth_yoy is not None]
            parts.append(self._zscore_to_score(stock.eps_growth_yoy, all_eps))

        if stock.eps_growth_3y is not None:
            all_3y = [s.eps_growth_3y for s in universe if s.eps_growth_3y is not None]
            parts.append(self._zscore_to_score(stock.eps_growth_3y, all_3y))

        if not parts:
            return None
        return round(sum(parts) / len(parts), 2)

    def _score_profitability(self, stock: StockData, universe: list[StockData]) -> Optional[float]:
        """Higher ROE, ROIC, margins vs peers → higher score."""
        parts = []

        if stock.roe is not None:
            all_roe = [s.roe for s in universe if s.roe is not None]
            parts.append(self._zscore_to_score(stock.roe, all_roe))

        if stock.roic is not None:
            all_roic = [s.roic for s in universe if s.roic is not None]
            parts.append(self._zscore_to_score(stock.roic, all_roic))

        if stock.gross_margin is not None:
            all_gm = [s.gross_margin for s in universe if s.gross_margin is not None]
            parts.append(self._zscore_to_score(stock.gross_margin, all_gm))

        if stock.net_margin is not None:
            all_nm = [s.net_margin for s in universe if s.net_margin is not None]
            parts.append(self._zscore_to_score(stock.net_margin, all_nm))

        if not parts:
            return None
        return round(sum(parts) / len(parts), 2)

    def _score_eps_revisions(self, stock: StockData, universe: list[StockData]) -> Optional[float]:
        """
        Positive analyst estimate revisions → higher score.
        30-day revisions get highest weight (most predictive).
        60d and 90d get lower weight.
        """
        parts = []
        weights = []

        if stock.eps_rev_30d is not None:
            all_30 = [s.eps_rev_30d for s in universe if s.eps_rev_30d is not None]
            parts.append(self._zscore_to_score(stock.eps_rev_30d, all_30))
            weights.append(0.5)

        if stock.eps_rev_60d is not None:
            all_60 = [s.eps_rev_60d for s in universe if s.eps_rev_60d is not None]
            parts.append(self._zscore_to_score(stock.eps_rev_60d, all_60))
            weights.append(0.3)

        if stock.eps_rev_90d is not None:
            all_90 = [s.eps_rev_90d for s in universe if s.eps_rev_90d is not None]
            parts.append(self._zscore_to_score(stock.eps_rev_90d, all_90))
            weights.append(0.2)

        if not parts:
            return None

        total_weight = sum(weights)
        weighted = sum(p * w for p, w in zip(parts, weights)) / total_weight
        return round(weighted, 2)

    def _score_momentum(self, stock: StockData, universe: list[StockData]) -> Optional[float]:
        """
        12-1 month momentum: return from 12 months ago to 1 month ago.
        Excludes last month (short-term reversal bias).
        RSI secondary component.
        """
        parts = []
        weights = []

        if stock.price_12m_ago and stock.price_1m_ago and stock.price_12m_ago > 0:
            # 12-1 month momentum = (price_1m_ago / price_12m_ago) - 1
            momentum_12_1 = (stock.price_1m_ago / stock.price_12m_ago) - 1.0
            all_mom = []
            for s in universe:
                if s.price_12m_ago and s.price_1m_ago and s.price_12m_ago > 0:
                    all_mom.append((s.price_1m_ago / s.price_12m_ago) - 1.0)
            parts.append(self._zscore_to_score(momentum_12_1, all_mom))
            weights.append(0.7)

        if stock.rsi_14 is not None:
            # RSI: oversold (<30) is bullish opportunity, overbought (>70) is bearish
            # Map RSI to 0-10 score: 50=neutral(5), 30=high(8), 70=low(2)
            # Institutions buy momentum INTO strength but not extreme overbought
            # Optimal RSI range is 50-65 (score ~6-7)
            rsi = stock.rsi_14
            if rsi <= 30:
                rsi_score = 7.5  # Oversold — potential reversal
            elif rsi <= 50:
                rsi_score = 4.0 + (rsi - 30) * 0.15
            elif rsi <= 65:
                rsi_score = 7.0 + (rsi - 50) * 0.067  # Sweet spot
            elif rsi <= 80:
                rsi_score = 8.0 - (rsi - 65) * 0.27   # Overbought caution
            else:
                rsi_score = 4.0  # Extreme overbought
            parts.append(max(0.0, min(10.0, rsi_score)))
            weights.append(0.3)

        if not parts:
            return None

        total_weight = sum(weights)
        weighted = sum(p * w for p, w in zip(parts, weights)) / total_weight
        return round(weighted, 2)

    def score(self, stocks: list[StockData]) -> list[ScoreResult]:
        results = []
        for stock in stocks:
            sv = self._score_value(stock, stocks)
            sg = self._score_growth(stock, stocks)
            sp = self._score_profitability(stock, stocks)
            se = self._score_eps_revisions(stock, stocks)
            sm = self._score_momentum(stock, stocks)

            available = [s for s in [sv, sg, sp, se, sm] if s is not None]
            composite = round(sum(available) / len(available), 2) if available else None

            # Momentum (12-1)
            mom_12_1 = None
            if stock.price_12m_ago and stock.price_1m_ago and stock.price_12m_ago > 0:
                mom_12_1 = round((stock.price_1m_ago / stock.price_12m_ago - 1) * 100, 1)

            # Verdict
            verdict, confidence = _verdict(sv, sg, sp, se, sm, composite)

            results.append(ScoreResult(
                symbol=stock.symbol,
                price=stock.price,
                sector=stock.sector,
                score_value=sv,
                score_growth=sg,
                score_profitability=sp,
                score_eps_revisions=se,
                score_momentum=sm,
                composite=composite,
                momentum_12_1=mom_12_1,
                rsi_14=stock.rsi_14,
                roe=stock.roe,
                net_margin=stock.net_margin,
                eps_rev_30d=stock.eps_rev_30d,
                revenue_growth_yoy=stock.revenue_growth_yoy,
                cost_basis=stock.cost_basis,
                shares_held=stock.shares_held,
                notes=stock.notes,
                verdict=verdict,
                confidence=confidence,
            ))

        results.sort(key=lambda r: r.composite or 0, reverse=True)
        return results


def _verdict(sv, sg, sp, se, sm, composite) -> tuple[str, str]:
    """
    Derive a BUY/SELL/HOLD/AVOID verdict and HIGH/MEDIUM/LOW confidence.

    Buy threshold:  composite >= 6.5
    Sell threshold: composite <= 3.5
    Red flags: growth <= 3 OR momentum <= 3 caps at HOLD (Seeking Alpha rule)
    """
    if composite is None:
        return "HOLD", "LOW"

    # Red-flag caps (mirrors SA: D+ or worse on key factors → no buy)
    has_red_flag = (sg is not None and sg <= 3.0) or (sm is not None and sm <= 3.0)

    if composite >= 7.5 and not has_red_flag:
        return "BUY", "HIGH"
    elif composite >= 6.5 and not has_red_flag:
        return "BUY", "MEDIUM"
    elif composite <= 2.5:
        return "AVOID", "HIGH"
    elif composite <= 3.5:
        return "SELL", "MEDIUM"
    elif composite <= 4.5:
        return "HOLD", "LOW"
    else:
        return "HOLD", "MEDIUM"


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

_FLOAT_FIELDS = [
    "price", "pe_ratio", "forward_pe", "pb_ratio", "ev_ebitda",
    "revenue_growth_yoy", "eps_growth_yoy", "eps_growth_3y",
    "roe", "roic", "gross_margin", "net_margin",
    "eps_rev_30d", "eps_rev_60d", "eps_rev_90d",
    "price_12m_ago", "price_1m_ago", "rsi_14",
    "market_cap_b", "dividend_yield", "cost_basis", "shares_held",
]


def _parse_float(val: str) -> Optional[float]:
    if val is None or val.strip() in ("", "N/A", "n/a", "-", "None"):
        return None
    try:
        return float(val.strip().replace(",", "").replace("%", "").replace("$", ""))
    except ValueError:
        return None


def load_csv(path: str) -> list[StockData]:
    stocks = []
    with open(path, newline="", encoding="utf-8") as f:
        filtered = (line for line in f if not line.startswith("#") and line.strip())
        reader = csv.DictReader(filtered)
        for row in reader:
            kwargs = {"symbol": row["symbol"].strip().upper(), "price": float(row["price"])}
            for fld in _FLOAT_FIELDS:
                if fld in row:
                    kwargs[fld] = _parse_float(row[fld])
            for fld in ("sector", "notes"):
                if fld in row:
                    kwargs[fld] = row[fld].strip() or None
            stocks.append(StockData(**kwargs))
    return stocks


def load_json(path: str) -> list[StockData]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    stocks = []
    for item in data:
        symbol = item.pop("symbol").upper()
        price = float(item.pop("price"))
        kwargs = {"symbol": symbol, "price": price}
        for fld in _FLOAT_FIELDS:
            if fld in item and item[fld] not in (None, "", "N/A"):
                try:
                    kwargs[fld] = float(item[fld])
                except (ValueError, TypeError):
                    pass
        for fld in ("sector", "notes"):
            if fld in item:
                kwargs[fld] = item[fld]
        stocks.append(StockData(**kwargs))
    return stocks


# ---------------------------------------------------------------------------
# Webull import
# ---------------------------------------------------------------------------
# Webull does not export a clean holdings CSV and does not expose fundamentals
# (P/E, ROE, EPS revisions, etc.). These loaders pull what Webull DOES provide
# — symbol, current price, cost basis, share count — so you only have to fill
# in fundamentals (from Yahoo Finance) before scoring.

def _wb_num(val) -> Optional[float]:
    """Coerce a Webull field (often a string) to float."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    return _parse_float(str(val))


def _extract_positions(blob) -> list:
    """Pull the list of position dicts out of various Webull payload shapes."""
    if isinstance(blob, list):
        return blob
    if isinstance(blob, dict):
        for key in ("positions", "holdings", "items", "data"):
            inner = blob.get(key)
            if isinstance(inner, list):
                return inner
            if isinstance(inner, dict):
                # OpenAPI sometimes nests one more level: data -> positions
                for k2 in ("positions", "holdings", "items"):
                    if isinstance(inner.get(k2), list):
                        return inner[k2]
    return []


def load_webull_positions_json(path: str) -> list[StockData]:
    """
    Load current holdings from a Webull positions JSON dump.

    Handles both the unofficial `webull` package shape (ticker is a nested
    object, share count under "position") and the official OpenAPI shape
    (flat "symbol"/"quantity"). Accepts a bare list, a full account blob,
    or a {"data": {...}} wrapper.
    """
    with open(path, encoding="utf-8") as f:
        blob = json.load(f)

    positions = _extract_positions(blob)
    stocks = []
    for pos in positions:
        if not isinstance(pos, dict):
            continue

        # Symbol: flat field, or nested ticker object
        symbol = pos.get("symbol") or pos.get("disSymbol")
        ticker = pos.get("ticker")
        if not symbol and isinstance(ticker, dict):
            symbol = ticker.get("symbol") or ticker.get("disSymbol")
        if not symbol and isinstance(ticker, str):
            symbol = ticker
        if not symbol:
            continue
        symbol = str(symbol).strip().upper()

        shares = _wb_num(pos.get("position")) or _wb_num(pos.get("quantity"))
        cost_basis = (
            _wb_num(pos.get("costPrice"))
            or _wb_num(pos.get("cost"))
            or _wb_num(pos.get("unitCostPrice"))
        )
        price = _wb_num(pos.get("lastPrice"))
        market_value = _wb_num(pos.get("marketValue"))
        if price is None and market_value and shares:
            price = round(market_value / shares, 4)
        # Last resort: fall back to cost basis so the row is at least scorable
        if price is None:
            price = cost_basis or 0.0

        sector = pos.get("sector")
        if not sector and isinstance(ticker, dict):
            sector = ticker.get("sector")

        pnl = pos.get("unrealizedProfitLossRate")
        note = None
        if pnl is not None:
            rate = _wb_num(pnl)
            if rate is not None:
                # Webull gives this as a decimal (0.123 = +12.3%)
                note = f"Webull unrealized P&L: {rate * 100:+.1f}%"

        stocks.append(StockData(
            symbol=symbol,
            price=price or 0.0,
            cost_basis=cost_basis,
            shares_held=shares,
            sector=sector,
            notes=note,
        ))
    return stocks


# Webull order-history CSV uses these column headers (varies by export source)
_WB_COL_SYMBOL = ("symbol", "ticker")
_WB_COL_SIDE = ("side", "action", "buy/sell")
_WB_COL_STATUS = ("status", "state")
_WB_COL_QTY = ("filled", "filled qty", "qty", "quantity", "total qty", "shares")
_WB_COL_PRICE = ("avg price", "average fill price", "avg fill price", "filled price", "price")
_WB_COL_TIME = ("filled time", "placed time", "time", "date")


def _find_col(fieldnames: list, candidates: tuple) -> Optional[str]:
    """Case-insensitive lookup of the first matching column name."""
    lower = {fn.lower().strip(): fn for fn in fieldnames if fn}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    return None


def load_webull_orders_csv(path: str) -> list[StockData]:
    """
    Reconstruct current holdings from a Webull order/transaction-history CSV.

    Processes filled BUY/SELL orders chronologically, maintaining a running
    share count and weighted-average cost per symbol. The result is your net
    open position with its blended cost basis — fundamentals are left blank.

    Note: the export has no "current price" column, so price is set to the
    most recent fill price as a placeholder. Update it before scoring.
    """
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    col_sym = _find_col(fieldnames, _WB_COL_SYMBOL)
    col_side = _find_col(fieldnames, _WB_COL_SIDE)
    col_status = _find_col(fieldnames, _WB_COL_STATUS)
    col_qty = _find_col(fieldnames, _WB_COL_QTY)
    col_price = _find_col(fieldnames, _WB_COL_PRICE)
    col_time = _find_col(fieldnames, _WB_COL_TIME)

    if not col_sym or not col_qty or not col_price:
        raise ValueError(
            "Could not find Symbol / Qty / Price columns in the Webull CSV. "
            f"Found columns: {fieldnames}"
        )

    # Sort oldest-first so cost averaging is correct (Webull exports newest-first)
    if col_time:
        rows.sort(key=lambda r: r.get(col_time) or "")

    # symbol -> {shares, avg_cost, last_price}
    book: dict = {}
    for row in rows:
        # Only count executed fills
        if col_status:
            status = (row.get(col_status) or "").strip().lower()
            if status and "filled" not in status and "executed" not in status:
                continue

        symbol = (row.get(col_sym) or "").strip().upper()
        if not symbol:
            continue
        qty = _parse_float(row.get(col_qty))
        price = _parse_float(row.get(col_price))
        if not qty or qty <= 0 or price is None:
            continue

        side = (row.get(col_side) or "buy").strip().lower()
        is_sell = side.startswith("s")  # "sell", "sold"

        b = book.setdefault(symbol, {"shares": 0.0, "avg_cost": 0.0, "last_price": price})
        b["last_price"] = price

        if is_sell:
            b["shares"] = max(0.0, b["shares"] - qty)
            if b["shares"] == 0.0:
                b["avg_cost"] = 0.0
        else:
            prev_shares = b["shares"]
            new_shares = prev_shares + qty
            b["avg_cost"] = (
                (prev_shares * b["avg_cost"] + qty * price) / new_shares
                if new_shares > 0 else 0.0
            )
            b["shares"] = new_shares

    stocks = []
    for symbol, b in book.items():
        if b["shares"] <= 0:
            continue  # fully closed — no open position
        stocks.append(StockData(
            symbol=symbol,
            price=round(b["last_price"], 4),
            cost_basis=round(b["avg_cost"], 4) if b["avg_cost"] else None,
            shares_held=round(b["shares"], 6),
            notes="Imported from Webull orders — price is last fill, update before scoring",
        ))
    return stocks


def write_screener_csv(stocks: list[StockData], path: str) -> None:
    """Write StockData rows to a screener CSV using the standard column order."""
    columns = [
        "symbol", "price", "pe_ratio", "forward_pe", "pb_ratio", "ev_ebitda",
        "revenue_growth_yoy", "eps_growth_yoy", "eps_growth_3y",
        "roe", "roic", "gross_margin", "net_margin",
        "eps_rev_30d", "eps_rev_60d", "eps_rev_90d",
        "price_12m_ago", "price_1m_ago", "rsi_14",
        "sector", "market_cap_b", "dividend_yield", "cost_basis", "shares_held", "notes",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for s in stocks:
            d = asdict(s)
            writer.writerow(["" if d.get(c) is None else d.get(c) for c in columns])


def wizard() -> list[StockData]:
    """Interactive one-stock-at-a-time entry."""
    print("\n=== Stock Entry Wizard ===")
    print("Enter stock data. Press Enter to skip optional fields.")
    print("Enter 'done' at symbol prompt when finished.\n")
    stocks = []

    while True:
        symbol = input("Symbol (or 'done'): ").strip().upper()
        if symbol == "DONE":
            break
        if not symbol:
            continue

        price = None
        while price is None:
            try:
                price = float(input(f"  Current price of {symbol}: $").strip())
            except ValueError:
                print("  Please enter a valid number.")

        def _ask(prompt, fld=None):
            val = input(f"  {prompt}: ").strip()
            return _parse_float(val) if val else None

        def _ask_str(prompt):
            val = input(f"  {prompt}: ").strip()
            return val or None

        print("  --- Valuation ---")
        pe = _ask("P/E ratio (trailing, e.g. 25.3)")
        fpe = _ask("Forward P/E")
        pb = _ask("Price-to-Book ratio")
        ev_ebitda = _ask("EV/EBITDA")

        print("  --- Growth ---")
        rev_g = _ask("Revenue growth YoY % (e.g. 12 for 12%)")
        if rev_g is not None:
            rev_g /= 100
        eps_g = _ask("EPS growth YoY %")
        if eps_g is not None:
            eps_g /= 100
        eps_3y = _ask("3-year EPS growth % annualized")
        if eps_3y is not None:
            eps_3y /= 100

        print("  --- Profitability ---")
        roe = _ask("Return on Equity % (e.g. 28 for 28%)")
        if roe is not None:
            roe /= 100
        roic = _ask("Return on Invested Capital %")
        if roic is not None:
            roic /= 100
        gm = _ask("Gross margin %")
        if gm is not None:
            gm /= 100
        nm = _ask("Net margin %")
        if nm is not None:
            nm /= 100

        print("  --- EPS Revisions (analyst estimate changes) ---")
        rev30 = _ask("EPS estimate change last 30 days % (+ = raised, - = cut)")
        if rev30 is not None:
            rev30 /= 100
        rev60 = _ask("EPS estimate change last 60 days %")
        if rev60 is not None:
            rev60 /= 100
        rev90 = _ask("EPS estimate change last 90 days %")
        if rev90 is not None:
            rev90 /= 100

        print("  --- Momentum ---")
        p12m = _ask("Price 12 months ago ($)")
        p1m = _ask("Price 1 month ago ($)")
        rsi = _ask("14-day RSI (0-100)")

        print("  --- Context (optional) ---")
        sector = _ask_str("Sector (e.g. Technology)")
        cb = _ask("Your cost basis ($, if you own it)")
        sh = _ask("Shares held")
        notes = _ask_str("Notes")

        stocks.append(StockData(
            symbol=symbol, price=price,
            pe_ratio=pe, forward_pe=fpe, pb_ratio=pb, ev_ebitda=ev_ebitda,
            revenue_growth_yoy=rev_g, eps_growth_yoy=eps_g, eps_growth_3y=eps_3y,
            roe=roe, roic=roic, gross_margin=gm, net_margin=nm,
            eps_rev_30d=rev30, eps_rev_60d=rev60, eps_rev_90d=rev90,
            price_12m_ago=p12m, price_1m_ago=p1m, rsi_14=rsi,
            sector=sector, cost_basis=cb, shares_held=sh, notes=notes,
        ))
        print(f"  Added {symbol}.\n")

    return stocks


# ---------------------------------------------------------------------------
# Report renderer
# ---------------------------------------------------------------------------

VERDICT_EMOJI = {
    "BUY": "  BUY  ",
    "SELL": "  SELL ",
    "AVOID": " AVOID ",
    "HOLD": "  HOLD ",
}

CONF_STARS = {"HIGH": "***", "MEDIUM": "**", "LOW": "*"}


def _bar(score: Optional[float], width: int = 10) -> str:
    if score is None:
        return "?" + " " * (width - 1)
    filled = round(score / 10 * width)
    return "#" * filled + "-" * (width - filled)


def _fmt(val: Optional[float], pct: bool = False, prefix: str = "") -> str:
    if val is None:
        return "N/A"
    if pct:
        return f"{val * 100:+.1f}%"
    return f"{prefix}{val:.2f}"


def render_report(results: list[ScoreResult], output=sys.stdout) -> None:
    today = date.today().isoformat()
    sep = "=" * 80

    print(sep, file=output)
    print(f"  DAILY QUANT SCREEN — {today}", file=output)
    print(f"  {len(results)} stock(s) analyzed | 5-factor Alpha Picks framework", file=output)
    print(sep, file=output)

    for r in results:
        verdict_str = VERDICT_EMOJI.get(r.verdict, r.verdict)
        conf_str = CONF_STARS.get(r.confidence, "")
        comp_str = f"{r.composite:.1f}/10" if r.composite is not None else "N/A"

        pnl_str = ""
        if r.cost_basis and r.price:
            pnl_pct = (r.price - r.cost_basis) / r.cost_basis * 100
            pnl_str = f"  P&L: {pnl_pct:+.1f}%"
            if r.shares_held:
                pnl_usd = (r.price - r.cost_basis) * r.shares_held
                pnl_str += f" (${pnl_usd:+,.0f})"

        sector_str = f" | {r.sector}" if r.sector else ""
        print(f"\n[{verdict_str}] {r.symbol:<8} ${r.price:<10.2f} Score: {comp_str:<8} {conf_str}{sector_str}{pnl_str}", file=output)

        # Factor bars
        factors = [
            ("Value       ", r.score_value),
            ("Growth      ", r.score_growth),
            ("Profitablty ", r.score_profitability),
            ("EPS Revs    ", r.score_eps_revisions),
            ("Momentum    ", r.score_momentum),
        ]
        for label, score in factors:
            bar = _bar(score)
            val_str = f"{score:.1f}" if score is not None else "N/A"
            print(f"    {label} [{bar}] {val_str}", file=output)

        # Key metrics
        details = []
        if r.revenue_growth_yoy is not None:
            details.append(f"Rev growth: {r.revenue_growth_yoy*100:+.1f}%")
        if r.roe is not None:
            details.append(f"ROE: {r.roe*100:.1f}%")
        if r.net_margin is not None:
            details.append(f"Net margin: {r.net_margin*100:.1f}%")
        if r.eps_rev_30d is not None:
            details.append(f"EPS rev 30d: {r.eps_rev_30d*100:+.1f}%")
        if r.momentum_12_1 is not None:
            details.append(f"12-1m mom: {r.momentum_12_1:+.1f}%")
        if r.rsi_14 is not None:
            details.append(f"RSI: {r.rsi_14:.0f}")
        if details:
            print("    " + " | ".join(details), file=output)

        if r.notes:
            print(f"    Notes: {r.notes}", file=output)

    print(f"\n{sep}", file=output)

    # Summary table
    print("  RANKING SUMMARY", file=output)
    print(f"  {'Symbol':<8} {'Score':>6} {'Verdict':<8} {'Confidence'}", file=output)
    print("  " + "-" * 40, file=output)
    for r in results:
        score_str = f"{r.composite:.1f}" if r.composite is not None else " N/A"
        print(f"  {r.symbol:<8} {score_str:>6} {r.verdict:<8} {r.confidence}", file=output)

    print(sep, file=output)
    print("  Formulas: z-score normalization across input universe.", file=output)
    print("  Scores are relative — add more stocks for more accurate ranking.", file=output)
    print(sep, file=output)


# ---------------------------------------------------------------------------
# Template generator
# ---------------------------------------------------------------------------

TEMPLATE_CSV = """\
symbol,price,pe_ratio,forward_pe,pb_ratio,ev_ebitda,revenue_growth_yoy,eps_growth_yoy,eps_growth_3y,roe,roic,gross_margin,net_margin,eps_rev_30d,eps_rev_60d,eps_rev_90d,price_12m_ago,price_1m_ago,rsi_14,sector,market_cap_b,dividend_yield,cost_basis,shares_held,notes
AAPL,189.50,28.5,26.1,45.2,22.1,0.06,0.12,0.10,1.45,0.28,0.43,0.25,-0.02,-0.01,0.03,165.00,188.00,58,Technology,2900,0.005,150.00,10,Example row
NVDA,875.00,65.0,35.0,32.0,45.0,0.122,0.45,0.60,0.95,0.55,0.73,0.52,0.08,0.12,0.20,495.00,850.00,72,Technology,2150,,600.00,5,Bought during dip
AMZN,185.00,42.0,32.0,8.5,18.0,0.10,0.35,0.25,0.22,0.12,0.46,0.08,0.04,0.06,0.10,138.00,182.00,61,Consumer Cyclical,1950,,145.00,8,
JNJ,155.00,14.5,13.0,5.2,12.0,0.04,0.05,0.07,0.22,0.15,0.68,0.19,0.01,0.00,-0.01,148.00,154.00,52,Healthcare,375,0.030,160.00,15,Core holding — underwater
PG,165.00,26.0,24.0,8.5,18.5,0.03,0.07,0.08,0.28,0.18,0.50,0.18,0.00,0.01,0.02,143.00,164.00,55,Consumer Defensive,390,0.023,187.00,12,Dividend reinvesting
META,500.00,24.0,20.0,7.0,14.0,0.22,0.55,0.40,0.38,0.25,0.81,0.35,0.06,0.09,0.15,312.00,490.00,65,Technology,1270,,380.00,7,
MSFT,415.00,35.0,30.0,13.0,24.0,0.17,0.22,0.18,0.38,0.28,0.70,0.35,0.03,0.04,0.07,360.00,410.00,60,Technology,3100,0.007,,0,Watchlist
"""

TEMPLATE_JSON = """\
[
  {
    "symbol": "AAPL",
    "price": 189.50,
    "pe_ratio": 28.5,
    "forward_pe": 26.1,
    "pb_ratio": 45.2,
    "ev_ebitda": 22.1,
    "revenue_growth_yoy": 0.06,
    "eps_growth_yoy": 0.12,
    "roe": 1.45,
    "gross_margin": 0.43,
    "net_margin": 0.25,
    "eps_rev_30d": -0.02,
    "price_12m_ago": 165.00,
    "price_1m_ago": 188.00,
    "rsi_14": 58,
    "sector": "Technology",
    "cost_basis": 150.00,
    "shares_held": 10
  }
]
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Offline daily stock quant screener — no API keys required.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", "-i", help="Path to CSV or JSON input file")
    parser.add_argument("--webull-json", help="Import holdings from a Webull positions JSON dump")
    parser.add_argument("--webull-csv", help="Import holdings from a Webull order-history CSV export")
    parser.add_argument("--output", "-o", help="Save report to this file (default: stdout)")
    parser.add_argument("--export", help="Write an enriched screener CSV (fill in fundamentals) instead of scoring")
    parser.add_argument("--wizard", "-w", action="store_true", help="Interactive data entry wizard")
    parser.add_argument("--template", "-t", action="store_true", help="Print a template CSV/JSON and exit")
    parser.add_argument("--json-template", action="store_true", help="Print a JSON template and exit")
    parser.add_argument("--json-out", action="store_true", help="Output scores as JSON instead of report")

    args = parser.parse_args()

    if args.template:
        print(TEMPLATE_CSV.rstrip())
        sys.stderr.write("\n# Save the above as stocks.csv, fill in your data, then run:\n")
        sys.stderr.write("# python daily_check.py --input stocks.csv\n")
        return

    if args.json_template:
        print(TEMPLATE_JSON)
        return

    if args.wizard:
        stocks = wizard()
    elif args.webull_json:
        stocks = load_webull_positions_json(args.webull_json)
    elif args.webull_csv:
        stocks = load_webull_orders_csv(args.webull_csv)
    elif args.input:
        if args.input.endswith(".json"):
            stocks = load_json(args.input)
        else:
            stocks = load_csv(args.input)
    else:
        parser.print_help()
        print("\nError: provide --input <file>, --webull-json, --webull-csv, --wizard, or --template")
        sys.exit(1)

    if not stocks:
        print("No stocks loaded. Check your input file.")
        sys.exit(1)

    # Webull import path: write an enriched screener CSV for you to add fundamentals.
    if args.export:
        write_screener_csv(stocks, args.export)
        print(f"Wrote {len(stocks)} holdings to {args.export}")
        print("Fill in the fundamentals columns (P/E, ROE, growth, etc.) from")
        print("Yahoo Finance, then run: python daily_check.py --input " + args.export)
        return

    scorer = QuantScorer()
    results = scorer.score(stocks)

    if args.json_out:
        out = []
        for r in results:
            d = asdict(r)
            out.append(d)
        output = json.dumps(out, indent=2)
        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
        else:
            print(output)
        return

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            render_report(results, output=f)
        print(f"Report saved to {args.output}")
    else:
        render_report(results)


if __name__ == "__main__":
    main()
