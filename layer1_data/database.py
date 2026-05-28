"""
Layer 1 — Persistent storage for the stock trading engine.

Uses SQLite for structured watchlist/price/trade data and a CSV
fallback for raw tick data.
"""

import csv
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config.settings import config

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thread-safe SQLite wrapper for stock trading data."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or config.database.path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_tables(self):
        cur = self._conn.cursor()

        cur.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist (
            symbol      TEXT PRIMARY KEY,
            name        TEXT,
            sector      TEXT,
            industry    TEXT,
            market_cap  REAL,
            beta        REAL,
            active      INTEGER DEFAULT 1,
            added_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS price_ticks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id   TEXT NOT NULL,
            token_id    TEXT NOT NULL,
            price       REAL NOT NULL,
            side        TEXT,
            ts          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_price_ticks_market ON price_ticks(market_id, ts);

        CREATE TABLE IF NOT EXISTS news_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT,
            headline    TEXT NOT NULL,
            body        TEXT,
            url         TEXT,
            published_at TEXT,
            fetched_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_news_ts ON news_items(published_at);

        CREATE TABLE IF NOT EXISTS trade_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            layer       TEXT,
            symbol      TEXT,
            side        TEXT,
            price       REAL,
            size_usd    REAL,
            order_id    TEXT,
            reasoning   TEXT,
            target_price REAL,
            stop_loss   REAL,
            strategy    TEXT,
            placed_at   TEXT,
            closed_at   TEXT,
            pnl_usd     REAL,
            outcome     TEXT
        );

        CREATE TABLE IF NOT EXISTS sentiment_signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT,
            keyword     TEXT,
            platform    TEXT,
            mention_count INTEGER,
            hype_score  REAL,
            ts          TEXT
        );

        CREATE TABLE IF NOT EXISTS stock_analyses (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol              TEXT NOT NULL,
            company_name        TEXT,
            ai_outlook          TEXT,
            confidence          TEXT,
            price_at_analysis   REAL,
            target_price        REAL,
            upside_pct          REAL,
            reasoning           TEXT,
            key_risks           TEXT,
            model               TEXT,
            ts                  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_stock_analyses_symbol ON stock_analyses(symbol, ts);

        CREATE TABLE IF NOT EXISTS performance_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT,
            total_pnl   REAL,
            win_rate    REAL,
            avg_edge    REAL,
            category_stats TEXT,
            notes       TEXT
        );

        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT,
            total_value     REAL,
            cash            REAL,
            positions_json  TEXT,
            daily_pnl       REAL,
            total_pnl       REAL
        );
        CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_ts ON portfolio_snapshots(ts);
        """)
        self._conn.commit()
        logger.info("Database ready at %s", self.db_path)

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------

    def upsert_watchlist(self, entry: dict):
        """Insert or update a symbol in the watchlist."""
        cur = self._conn.cursor()
        cur.execute("""
        INSERT INTO watchlist (symbol, name, sector, industry, market_cap, beta, active, added_at)
        VALUES (:symbol, :name, :sector, :industry, :market_cap, :beta, :active, :added_at)
        ON CONFLICT(symbol) DO UPDATE SET
            name       = excluded.name,
            sector     = excluded.sector,
            industry   = excluded.industry,
            market_cap = excluded.market_cap,
            beta       = excluded.beta,
            active     = excluded.active
        """, {
            "symbol": entry.get("symbol", ""),
            "name": entry.get("name"),
            "sector": entry.get("sector"),
            "industry": entry.get("industry"),
            "market_cap": entry.get("market_cap"),
            "beta": entry.get("beta"),
            "active": 1 if entry.get("active", True) else 0,
            "added_at": entry.get("added_at", _utcnow()),
        })
        self._conn.commit()

    def get_active_symbols(self) -> list[str]:
        """Return all active watchlist symbols."""
        cur = self._conn.cursor()
        rows = cur.execute(
            "SELECT symbol FROM watchlist WHERE active=1 ORDER BY symbol"
        ).fetchall()
        return [r["symbol"] for r in rows]

    def get_watchlist(self) -> list[dict]:
        """Return full watchlist metadata."""
        cur = self._conn.cursor()
        rows = cur.execute(
            "SELECT * FROM watchlist WHERE active=1 ORDER BY symbol"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_watchlist_entry(self, symbol: str) -> Optional[dict]:
        """Return a single watchlist entry."""
        cur = self._conn.cursor()
        row = cur.execute(
            "SELECT * FROM watchlist WHERE symbol=?", (symbol,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Price ticks
    # ------------------------------------------------------------------

    def insert_price_tick(self, market_id: str, token_id: str, price: float, side: str = "TRADE"):
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO price_ticks (market_id, token_id, price, side, ts) VALUES (?,?,?,?,?)",
            (market_id, token_id, price, side, _utcnow()),
        )
        self._conn.commit()

    def get_recent_prices(self, symbol: str, limit: int = 100) -> list[dict]:
        cur = self._conn.cursor()
        rows = cur.execute(
            "SELECT * FROM price_ticks WHERE market_id=? ORDER BY ts DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # News
    # ------------------------------------------------------------------

    def insert_news(self, source: str, headline: str, body: str = "", url: str = "", published_at: str = ""):
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO news_items (source, headline, body, url, published_at, fetched_at) VALUES (?,?,?,?,?,?)",
            (source, headline, body, url, published_at or _utcnow(), _utcnow()),
        )
        self._conn.commit()

    def get_recent_news(self, hours: int = 24, limit: int = 200) -> list[dict]:
        cur = self._conn.cursor()
        rows = cur.execute("""
            SELECT * FROM news_items
            WHERE published_at >= datetime('now', ? || ' hours')
            ORDER BY published_at DESC
            LIMIT ?
        """, (f"-{hours}", limit)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Trade log
    # ------------------------------------------------------------------

    def log_trade(self, trade: dict) -> int:
        """Insert a trade record. Returns the new row ID."""
        cur = self._conn.cursor()
        cur.execute("""
        INSERT INTO trade_log
            (layer, symbol, side, price, size_usd, order_id,
             reasoning, target_price, stop_loss, strategy, placed_at, outcome)
        VALUES
            (:layer, :symbol, :side, :price, :size_usd, :order_id,
             :reasoning, :target_price, :stop_loss, :strategy, :placed_at, :outcome)
        """, {
            "layer": trade.get("layer", ""),
            "symbol": trade.get("symbol", ""),
            "side": trade.get("side", ""),
            "price": trade.get("price", 0.0),
            "size_usd": trade.get("size_usd", 0.0),
            "order_id": trade.get("order_id", ""),
            "reasoning": trade.get("reasoning", ""),
            "target_price": trade.get("target_price"),
            "stop_loss": trade.get("stop_loss"),
            "strategy": trade.get("strategy", ""),
            "placed_at": _utcnow(),
            "outcome": trade.get("outcome", "OPEN"),
        })
        self._conn.commit()
        return cur.lastrowid

    def update_trade_outcome(self, trade_id: int, pnl_usd: float, outcome: str):
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE trade_log SET pnl_usd=?, outcome=?, closed_at=? WHERE id=?",
            (pnl_usd, outcome, _utcnow(), trade_id),
        )
        self._conn.commit()

    def get_open_trades(self) -> list[dict]:
        cur = self._conn.cursor()
        rows = cur.execute(
            "SELECT * FROM trade_log WHERE outcome='OPEN' ORDER BY placed_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_trade_history(self, limit: int = 500) -> list[dict]:
        cur = self._conn.cursor()
        rows = cur.execute(
            "SELECT * FROM trade_log ORDER BY placed_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Sentiment
    # ------------------------------------------------------------------

    def insert_sentiment(self, symbol: str, keyword: str, platform: str, count: int, score: float):
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO sentiment_signals (symbol, keyword, platform, mention_count, hype_score, ts) VALUES (?,?,?,?,?,?)",
            (symbol, keyword, platform, count, score, _utcnow()),
        )
        self._conn.commit()

    def get_latest_sentiment(self, symbol: str) -> Optional[dict]:
        cur = self._conn.cursor()
        row = cur.execute(
            "SELECT * FROM sentiment_signals WHERE symbol=? ORDER BY ts DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Stock analyses (replaces ai_predictions)
    # ------------------------------------------------------------------

    def log_stock_analysis(self, analysis: dict) -> int:
        """Insert a stock analysis record. Returns new row ID."""
        cur = self._conn.cursor()
        cur.execute("""
        INSERT INTO stock_analyses
            (symbol, company_name, ai_outlook, confidence, price_at_analysis,
             target_price, upside_pct, reasoning, key_risks, model, ts)
        VALUES
            (:symbol, :company_name, :ai_outlook, :confidence, :price_at_analysis,
             :target_price, :upside_pct, :reasoning, :key_risks, :model, :ts)
        """, {
            "symbol": analysis.get("symbol", ""),
            "company_name": analysis.get("company_name"),
            "ai_outlook": analysis.get("ai_outlook", "NEUTRAL"),
            "confidence": analysis.get("confidence", "LOW"),
            "price_at_analysis": analysis.get("price_at_analysis"),
            "target_price": analysis.get("target_price"),
            "upside_pct": analysis.get("upside_pct"),
            "reasoning": analysis.get("reasoning", ""),
            "key_risks": json.dumps(analysis.get("key_risks", [])),
            "model": analysis.get("model", ""),
            "ts": _utcnow(),
        })
        self._conn.commit()
        return cur.lastrowid

    def get_recent_analyses(self, symbol: str, limit: int = 10) -> list[dict]:
        cur = self._conn.cursor()
        rows = cur.execute(
            "SELECT * FROM stock_analyses WHERE symbol=? ORDER BY ts DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["key_risks"] = json.loads(d.get("key_risks") or "[]")
            except (json.JSONDecodeError, TypeError):
                d["key_risks"] = []
            result.append(d)
        return result

    def get_analyses_for_review(self, days: int = 30) -> list[dict]:
        """Return analyses joined with subsequent trade outcomes for calibration."""
        cur = self._conn.cursor()
        rows = cur.execute("""
            SELECT a.*, t.outcome, t.pnl_usd
            FROM stock_analyses a
            LEFT JOIN trade_log t ON t.symbol = a.symbol AND t.layer = 'layer3'
            WHERE a.ts >= datetime('now', ? || ' days')
            ORDER BY a.ts DESC
        """, (f"-{days}",)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["key_risks"] = json.loads(d.get("key_risks") or "[]")
            except (json.JSONDecodeError, TypeError):
                d["key_risks"] = []
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Performance snapshots
    # ------------------------------------------------------------------

    def log_performance_snapshot(
        self,
        total_pnl: float,
        win_rate: float,
        avg_edge: float,
        category_stats: dict,
        notes: str = "",
    ):
        cur = self._conn.cursor()
        cur.execute("""
        INSERT INTO performance_snapshots (ts, total_pnl, win_rate, avg_edge, category_stats, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (
            _utcnow(),
            total_pnl,
            win_rate,
            avg_edge,
            json.dumps(category_stats),
            notes,
        ))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Portfolio snapshots
    # ------------------------------------------------------------------

    def log_portfolio_snapshot(
        self,
        total_value: float,
        cash: float,
        positions: list[dict],
        daily_pnl: float = 0.0,
        total_pnl: float = 0.0,
    ):
        cur = self._conn.cursor()
        cur.execute("""
        INSERT INTO portfolio_snapshots (ts, total_value, cash, positions_json, daily_pnl, total_pnl)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (
            _utcnow(),
            total_value,
            cash,
            json.dumps(positions),
            daily_pnl,
            total_pnl,
        ))
        self._conn.commit()

    def get_latest_portfolio_snapshot(self) -> Optional[dict]:
        cur = self._conn.cursor()
        row = cur.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["positions"] = json.loads(d.get("positions_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["positions"] = []
        return d

    # ------------------------------------------------------------------
    # CSV export helper
    # ------------------------------------------------------------------

    def export_price_ticks_to_csv(self, symbol: str):
        csv_dir = Path(config.database.csv_dir)
        csv_dir.mkdir(parents=True, exist_ok=True)
        ticks = self.get_recent_prices(symbol, limit=100_000)
        if not ticks:
            return
        safe_name = symbol.replace(".", "_")
        path = csv_dir / f"{safe_name}_ticks.csv"
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=ticks[0].keys())
            writer.writeheader()
            writer.writerows(ticks)
        logger.debug("Exported %d ticks for %s to %s", len(ticks), symbol, path)

    def close(self):
        self._conn.close()
