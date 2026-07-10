"""SQLite ledger — the authoritative record of every tool-originated trade.

The ledger is trustworthy because the tool built each order itself (PRD §2):
no polling, no inference. Trades placed directly in the Kite app never touch
these tables and are therefore never shared.
"""

import secrets
import sqlite3
import threading
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS friends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    telegram_chat_id TEXT NOT NULL,
    multiplier REAL NOT NULL DEFAULT 1.0,   -- per-friend sizing (PRD §3)
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tradingsymbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    qty INTEGER NOT NULL,
    product TEXT NOT NULL,
    order_type TEXT NOT NULL,
    price REAL,                              -- limit price when order_type=LIMIT
    -- lifecycle: PLACED -> FILLED -> CLOSING -> CLOSED (or FAILED)
    status TEXT NOT NULL DEFAULT 'PLACED',
    entry_order_id TEXT UNIQUE,
    entry_fill_price REAL,
    entry_filled_qty INTEGER,
    entry_fill_time TEXT,
    exit_order_id TEXT UNIQUE,
    exit_fill_price REAL,
    exit_fill_time TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL REFERENCES trades(id),
    friend_id INTEGER NOT NULL REFERENCES friends(id),
    leg TEXT NOT NULL CHECK (leg IN ('ENTRY','EXIT')),
    qty INTEGER NOT NULL,                    -- friend-scaled quantity
    token TEXT NOT NULL UNIQUE,              -- opaque id in the mirror link
    -- PREBUILT: exit link built at entry time, not yet pushed (NFR-1)
    -- SENT: pushed to the friend's phone; CONFIRMED: Publisher redirect landed
    status TEXT NOT NULL DEFAULT 'PREBUILT',
    sent_at TEXT,
    confirmed_at TEXT,
    nudge_count INTEGER NOT NULL DEFAULT 0,
    last_nudge_at TEXT,
    UNIQUE (trade_id, friend_id, leg)
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ---- kv (access token etc.) ----

    def kv_set(self, key: str, value: str) -> None:
        self._exec("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def kv_get(self, key: str) -> str | None:
        rows = self._query("SELECT value FROM kv WHERE key=?", (key,))
        return rows[0]["value"] if rows else None

    # ---- friends ----

    def add_friend(self, name: str, telegram_chat_id: str, multiplier: float = 1.0) -> int:
        cur = self._exec(
            "INSERT INTO friends (name, telegram_chat_id, multiplier) VALUES (?, ?, ?)",
            (name, telegram_chat_id, multiplier),
        )
        return cur.lastrowid

    def update_friend(self, friend_id: int, *, multiplier: float | None = None, active: bool | None = None) -> None:
        if multiplier is not None:
            self._exec("UPDATE friends SET multiplier=? WHERE id=?", (multiplier, friend_id))
        if active is not None:
            self._exec("UPDATE friends SET active=? WHERE id=?", (1 if active else 0, friend_id))

    def friends(self, active_only: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM friends"
        if active_only:
            sql += " WHERE active=1"
        return self._query(sql + " ORDER BY id")

    def friend(self, friend_id: int) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM friends WHERE id=?", (friend_id,))
        return rows[0] if rows else None

    # ---- trades ----

    def create_trade(self, *, tradingsymbol: str, exchange: str, side: str, qty: int,
                     product: str, order_type: str, price: float | None,
                     entry_order_id: str) -> int:
        cur = self._exec(
            """INSERT INTO trades (tradingsymbol, exchange, side, qty, product, order_type,
                                   price, status, entry_order_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'PLACED', ?, ?)""",
            (tradingsymbol, exchange, side, qty, product, order_type, price, entry_order_id, utcnow()),
        )
        return cur.lastrowid

    def trade(self, trade_id: int) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM trades WHERE id=?", (trade_id,))
        return rows[0] if rows else None

    def trades(self, limit: int = 50) -> list[sqlite3.Row]:
        return self._query("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))

    def trade_by_order_id(self, order_id: str) -> tuple[sqlite3.Row, str] | None:
        """Return (trade, leg) for a Kite order_id, or None if the order is not
        ours — i.e. it was placed directly in the Kite app and stays personal."""
        rows = self._query("SELECT * FROM trades WHERE entry_order_id=?", (order_id,))
        if rows:
            return rows[0], "ENTRY"
        rows = self._query("SELECT * FROM trades WHERE exit_order_id=?", (order_id,))
        if rows:
            return rows[0], "EXIT"
        return None

    def mark_entry_filled(self, trade_id: int, fill_price: float, filled_qty: int, fill_time: str) -> None:
        self._exec(
            "UPDATE trades SET status='FILLED', entry_fill_price=?, entry_filled_qty=?, entry_fill_time=? WHERE id=?",
            (fill_price, filled_qty, fill_time, trade_id),
        )

    def mark_closing(self, trade_id: int, exit_order_id: str) -> None:
        self._exec("UPDATE trades SET status='CLOSING', exit_order_id=? WHERE id=?", (exit_order_id, trade_id))

    def mark_closed(self, trade_id: int, fill_price: float, fill_time: str) -> None:
        self._exec(
            "UPDATE trades SET status='CLOSED', exit_fill_price=?, exit_fill_time=? WHERE id=?",
            (fill_price, fill_time, trade_id),
        )

    def mark_failed(self, trade_id: int) -> None:
        self._exec("UPDATE trades SET status='FAILED' WHERE id=?", (trade_id,))

    # ---- shares ----

    def create_share(self, trade_id: int, friend_id: int, leg: str, qty: int) -> str:
        token = secrets.token_urlsafe(16)
        self._exec(
            "INSERT INTO shares (trade_id, friend_id, leg, qty, token) VALUES (?, ?, ?, ?, ?)",
            (trade_id, friend_id, leg, qty, token),
        )
        return token

    def share_by_token(self, token: str) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM shares WHERE token=?", (token,))
        return rows[0] if rows else None

    def share(self, share_id: int) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM shares WHERE id=?", (share_id,))
        return rows[0] if rows else None

    def shares_for_trade(self, trade_id: int, leg: str | None = None) -> list[sqlite3.Row]:
        if leg:
            return self._query("SELECT * FROM shares WHERE trade_id=? AND leg=? ORDER BY friend_id", (trade_id, leg))
        return self._query("SELECT * FROM shares WHERE trade_id=? ORDER BY leg, friend_id", (trade_id,))

    def mark_share_sent(self, share_id: int) -> None:
        self._exec(
            "UPDATE shares SET status='SENT', sent_at=? WHERE id=? AND status != 'CONFIRMED'",
            (utcnow(), share_id),
        )

    def mark_share_confirmed(self, token: str) -> bool:
        cur = self._exec(
            "UPDATE shares SET status='CONFIRMED', confirmed_at=? WHERE token=? AND status != 'CONFIRMED'",
            (utcnow(), token),
        )
        return cur.rowcount > 0

    def record_nudge(self, share_id: int) -> None:
        self._exec(
            "UPDATE shares SET nudge_count=nudge_count+1, last_nudge_at=? WHERE id=?",
            (utcnow(), share_id),
        )
