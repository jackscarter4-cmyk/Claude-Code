"""
Layer 1 — Persistent storage.

Uses SQLite for structured market/price data and a CSV fallback for
raw tick data (matching Emil's approach from the reference video).
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
    """Thread-safe SQLite wrapper for prediction-market data."""

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
        CREATE TABLE IF NOT EXISTS markets (
            id              TEXT PRIMARY KEY,
            question        TEXT NOT NULL,
            description     TEXT,
            category        TEXT,
            end_date        TEXT,
            volume          REAL DEFAULT 0,
            liquidity       REAL DEFAULT 0,
            yes_token_id    TEXT,
            no_token_id     TEXT,
            active          INTEGER DEFAULT 1,
            fetched_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS price_ticks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id       TEXT NOT NULL,
            token_id        TEXT NOT NULL,
            price           REAL NOT NULL,
            side            TEXT,            -- 'YES' or 'NO'
            ts              TEXT NOT NULL,
            FOREIGN KEY(market_id) REFERENCES markets(id)
        );
        CREATE INDEX IF NOT EXISTS idx_price_ticks_market ON price_ticks(market_id, ts);

        CREATE TABLE IF NOT EXISTS news_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source          TEXT,
            headline        TEXT NOT NULL,
            body            TEXT,
            url             TEXT,
            published_at    TEXT,
            fetched_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_news_ts ON news_items(published_at);

        CREATE TABLE IF NOT EXISTS trade_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            layer           TEXT,            -- 'layer2' or 'layer3'
            market_id       TEXT,
            token_id        TEXT,
            side            TEXT,
            price           REAL,
            size_usdc       REAL,
            order_id        TEXT,
            reasoning       TEXT,            -- JSON / free text from AI
            ai_probability  REAL,
            market_price    REAL,
            edge            REAL,
            placed_at       TEXT,
            closed_at       TEXT,
            pnl_usdc        REAL,
            outcome         TEXT             -- 'WIN', 'LOSS', 'OPEN', 'CANCELLED'
        );

        CREATE TABLE IF NOT EXISTS sentiment_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id       TEXT,
            keyword         TEXT,
            platform        TEXT,            -- 'twitter', 'reddit', 'polymarket_comments'
            mention_count   INTEGER,
            hype_score      REAL,
            ts              TEXT
        );

        CREATE TABLE IF NOT EXISTS ai_predictions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id       TEXT NOT NULL,
            question        TEXT,
            ai_probability  REAL,
            market_price    REAL,
            edge            REAL,
            reasoning       TEXT,
            model           TEXT,
            ts              TEXT
        );

        CREATE TABLE IF NOT EXISTS performance_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT,
            total_pnl       REAL,
            win_rate        REAL,
            avg_edge        REAL,
            category_stats  TEXT,           -- JSON
            notes           TEXT
        );
        """)
        self._conn.commit()
        logger.info("Database ready at %s", self.db_path)

    # ------------------------------------------------------------------
    # Markets
    # ------------------------------------------------------------------

    def upsert_market(self, market: dict):
        cur = self._conn.cursor()
        cur.execute("""
        INSERT INTO markets (id, question, description, category, end_date,
                             volume, liquidity, yes_token_id, no_token_id, active, fetched_at)
        VALUES (:id, :question, :description, :category, :end_date,
                :volume, :liquidity, :yes_token_id, :no_token_id, :active, :fetched_at)
        ON CONFLICT(id) DO UPDATE SET
            question = excluded.question,
            description = excluded.description,
            category = excluded.category,
            end_date = excluded.end_date,
            volume = excluded.volume,
            liquidity = excluded.liquidity,
            yes_token_id = excluded.yes_token_id,
            no_token_id = excluded.no_token_id,
            active = excluded.active,
            fetched_at = excluded.fetched_at
        """, {
            "id": market.get("conditionId", market.get("id", "")),
            "question": market.get("question", ""),
            "description": market.get("description", ""),
            "category": market.get("groupItemTitle", market.get("category", "")),
            "end_date": market.get("endDate", market.get("end_date", "")),
            "volume": float(market.get("volume", 0) or 0),
            "liquidity": float(market.get("liquidity", 0) or 0),
            "yes_token_id": market.get("clobTokenIds", ["", ""])[0] if market.get("clobTokenIds") else "",
            "no_token_id": market.get("clobTokenIds", ["", ""])[1] if market.get("clobTokenIds") else "",
            "active": 1 if market.get("active", True) else 0,
            "fetched_at": _utcnow(),
        })
        self._conn.commit()

    def get_active_markets(self) -> list[dict]:
        cur = self._conn.cursor()
        rows = cur.execute(
            "SELECT * FROM markets WHERE active=1 ORDER BY volume DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_market(self, market_id: str) -> Optional[dict]:
        cur = self._conn.cursor()
        row = cur.execute("SELECT * FROM markets WHERE id=?", (market_id,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Price ticks
    # ------------------------------------------------------------------

    def insert_price_tick(self, market_id: str, token_id: str, price: float, side: str = "YES"):
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO price_ticks (market_id, token_id, price, side, ts) VALUES (?,?,?,?,?)",
            (market_id, token_id, price, side, _utcnow()),
        )
        self._conn.commit()

    def get_recent_prices(self, market_id: str, limit: int = 100) -> list[dict]:
        cur = self._conn.cursor()
        rows = cur.execute(
            "SELECT * FROM price_ticks WHERE market_id=? ORDER BY ts DESC LIMIT ?",
            (market_id, limit),
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
        cur = self._conn.cursor()
        cur.execute("""
        INSERT INTO trade_log
            (layer, market_id, token_id, side, price, size_usdc, order_id,
             reasoning, ai_probability, market_price, edge, placed_at, outcome)
        VALUES
            (:layer, :market_id, :token_id, :side, :price, :size_usdc, :order_id,
             :reasoning, :ai_probability, :market_price, :edge, :placed_at, :outcome)
        """, {
            "layer": trade.get("layer", ""),
            "market_id": trade.get("market_id", ""),
            "token_id": trade.get("token_id", ""),
            "side": trade.get("side", ""),
            "price": trade.get("price", 0.0),
            "size_usdc": trade.get("size_usdc", 0.0),
            "order_id": trade.get("order_id", ""),
            "reasoning": trade.get("reasoning", ""),
            "ai_probability": trade.get("ai_probability"),
            "market_price": trade.get("market_price"),
            "edge": trade.get("edge"),
            "placed_at": _utcnow(),
            "outcome": trade.get("outcome", "OPEN"),
        })
        self._conn.commit()
        return cur.lastrowid

    def update_trade_outcome(self, trade_id: int, pnl_usdc: float, outcome: str):
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE trade_log SET pnl_usdc=?, outcome=?, closed_at=? WHERE id=?",
            (pnl_usdc, outcome, _utcnow(), trade_id),
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

    def insert_sentiment(self, market_id: str, keyword: str, platform: str, count: int, score: float):
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO sentiment_signals (market_id, keyword, platform, mention_count, hype_score, ts) VALUES (?,?,?,?,?,?)",
            (market_id, keyword, platform, count, score, _utcnow()),
        )
        self._conn.commit()

    def get_latest_sentiment(self, market_id: str) -> Optional[dict]:
        cur = self._conn.cursor()
        row = cur.execute(
            "SELECT * FROM sentiment_signals WHERE market_id=? ORDER BY ts DESC LIMIT 1",
            (market_id,),
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # AI predictions
    # ------------------------------------------------------------------

    def log_prediction(self, market_id: str, question: str, ai_prob: float,
                       market_price: float, reasoning: str, model: str):
        cur = self._conn.cursor()
        cur.execute("""
        INSERT INTO ai_predictions (market_id, question, ai_probability, market_price, edge, reasoning, model, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (market_id, question, ai_prob, market_price, ai_prob - market_price, reasoning, model, _utcnow()))
        self._conn.commit()

    def get_predictions_for_review(self, days: int = 30) -> list[dict]:
        cur = self._conn.cursor()
        rows = cur.execute("""
            SELECT p.*, t.outcome, t.pnl_usdc
            FROM ai_predictions p
            LEFT JOIN trade_log t ON t.market_id = p.market_id
            WHERE p.ts >= datetime('now', ? || ' days')
            ORDER BY p.ts DESC
        """, (f"-{days}",)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # CSV export helper (Layer 1 raw data)
    # ------------------------------------------------------------------

    def export_price_ticks_to_csv(self, market_id: str):
        csv_dir = Path(config.database.csv_dir)
        csv_dir.mkdir(parents=True, exist_ok=True)
        ticks = self.get_recent_prices(market_id, limit=100_000)
        if not ticks:
            return
        path = csv_dir / f"{market_id[:16]}_ticks.csv"
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=ticks[0].keys())
            writer.writeheader()
            writer.writerows(ticks)
        logger.debug("Exported %d ticks for %s to %s", len(ticks), market_id[:8], path)

    def close(self):
        self._conn.close()
