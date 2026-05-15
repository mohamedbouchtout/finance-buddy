"""SQLite storage. Stdlib only, single-file DB, schema migrates on import."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    stripe_customer_id TEXT,
    plan TEXT NOT NULL DEFAULT 'free',           -- 'free' | 'pro_monthly' | 'pro_yearly'
    subscription_status TEXT,                     -- 'active' | 'past_due' | 'canceled' | NULL
    current_period_end INTEGER,                   -- unix seconds
    waiver_accepted_at INTEGER,                   -- unix seconds; NULL means not yet accepted
    waiver_version TEXT,                          -- version string of the waiver they signed
    waiver_ip TEXT,                               -- IP address at the time of acceptance (evidentiary)
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS magic_tokens (
    token TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS portfolios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    shares REAL NOT NULL,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS price_cache (
    ticker TEXT PRIMARY KEY,
    price REAL NOT NULL,
    fetched_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    payload TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_licenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    key TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',         -- 'active' | 'revoked'
    machine_id TEXT,
    last_seen_at INTEGER,
    last_seen_ip TEXT,
    issued_at INTEGER NOT NULL,
    revoked_at INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bot_licenses_user ON bot_licenses(user_id);
CREATE INDEX IF NOT EXISTS idx_bot_licenses_key ON bot_licenses(key);
"""


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        # Lightweight in-place migration for older DBs created before the waiver columns existed.
        cols = {row["name"] for row in c.execute("PRAGMA table_info(users)").fetchall()}
        for col, ddl in (
            ("waiver_accepted_at", "ALTER TABLE users ADD COLUMN waiver_accepted_at INTEGER"),
            ("waiver_version",     "ALTER TABLE users ADD COLUMN waiver_version TEXT"),
            ("waiver_ip",          "ALTER TABLE users ADD COLUMN waiver_ip TEXT"),
        ):
            if col not in cols:
                c.execute(ddl)


# ---- liability waiver ----
WAIVER_VERSION = "1.0-2026-05"


def has_accepted_waiver(user: dict | None) -> bool:
    if not user:
        return False
    return bool(user.get("waiver_accepted_at"))


def record_waiver_acceptance(user_id: int, ip: str = "", version: str = WAIVER_VERSION) -> None:
    with conn() as c:
        c.execute(
            "UPDATE users SET waiver_accepted_at = ?, waiver_version = ?, waiver_ip = ? WHERE id = ?",
            (int(time.time()), version, ip[:64], user_id),
        )


# ---- price cache ----
def get_cached_price(ticker: str) -> Optional[dict]:
    with conn() as c:
        row = c.execute(
            "SELECT price, fetched_at FROM price_cache WHERE ticker = ?",
            (ticker.upper(),),
        ).fetchone()
        return dict(row) if row else None


def set_cached_price(ticker: str, price: float) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO price_cache (ticker, price, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET price = excluded.price, fetched_at = excluded.fetched_at",
            (ticker.upper(), price, int(time.time())),
        )


# ---- users ----
def upsert_user(email: str) -> dict:
    email = email.strip().lower()
    with conn() as c:
        c.execute(
            "INSERT INTO users (email, created_at) VALUES (?, ?) "
            "ON CONFLICT(email) DO NOTHING",
            (email, int(time.time())),
        )
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row)


def get_user(email: str) -> Optional[dict]:
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        return dict(row) if row else None


def get_user_by_id(uid: int) -> Optional[dict]:
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return dict(row) if row else None


def get_user_by_customer(stripe_customer_id: str) -> Optional[dict]:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM users WHERE stripe_customer_id = ?", (stripe_customer_id,)
        ).fetchone()
        return dict(row) if row else None


def update_subscription(
    email: str,
    *,
    stripe_customer_id: str | None,
    plan: str,
    status: str,
    period_end: int | None,
) -> None:
    with conn() as c:
        c.execute(
            "UPDATE users SET stripe_customer_id = COALESCE(?, stripe_customer_id), "
            "plan = ?, subscription_status = ?, current_period_end = ? WHERE email = ?",
            (stripe_customer_id, plan, status, period_end, email.strip().lower()),
        )


def user_is_pro(user: dict | None) -> bool:
    if not user:
        return False
    if user.get("subscription_status") != "active":
        return False
    return user.get("plan", "free") in ("pro_monthly", "pro_yearly")


# ---- magic tokens ----
def create_magic_token(email: str, token: str, ttl_seconds: int = 900) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO magic_tokens (token, email, expires_at, used) VALUES (?, ?, ?, 0)",
            (token, email.strip().lower(), int(time.time()) + ttl_seconds),
        )


def consume_magic_token(token: str) -> Optional[str]:
    """Returns email if token valid; else None. Marks token used."""
    with conn() as c:
        row = c.execute(
            "SELECT email, expires_at, used FROM magic_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return None
        if row["used"] or row["expires_at"] < int(time.time()):
            return None
        c.execute("UPDATE magic_tokens SET used = 1 WHERE token = ?", (token,))
        return row["email"]


# ---- portfolios ----
def save_portfolio(user_id: int, name: str, holdings: list[tuple[str, float]]) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO portfolios (user_id, name, created_at) VALUES (?, ?, ?)",
            (user_id, name, int(time.time())),
        )
        pid = cur.lastrowid
        for ticker, shares in holdings:
            c.execute(
                "INSERT INTO holdings (portfolio_id, ticker, shares) VALUES (?, ?, ?)",
                (pid, ticker.upper(), float(shares)),
            )
        return pid


def list_portfolios(user_id: int) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM portfolios WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_portfolio(portfolio_id: int, user_id: int) -> Optional[dict]:
    with conn() as c:
        p = c.execute(
            "SELECT * FROM portfolios WHERE id = ? AND user_id = ?",
            (portfolio_id, user_id),
        ).fetchone()
        if not p:
            return None
        holdings = c.execute(
            "SELECT ticker, shares FROM holdings WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchall()
        return {**dict(p), "holdings": [dict(h) for h in holdings]}


def delete_portfolio(portfolio_id: int, user_id: int) -> None:
    with conn() as c:
        c.execute(
            "DELETE FROM portfolios WHERE id = ? AND user_id = ?",
            (portfolio_id, user_id),
        )


# ---- analytics events ----
def log_event(name: str, payload: str = "") -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO events (name, payload, created_at) VALUES (?, ?, ?)",
            (name, payload, int(time.time())),
        )


def event_counts(since_seconds: int = 86400 * 7) -> dict:
    cutoff = int(time.time()) - since_seconds
    with conn() as c:
        rows = c.execute(
            "SELECT name, COUNT(*) as n FROM events WHERE created_at >= ? GROUP BY name",
            (cutoff,),
        ).fetchall()
        return {r["name"]: r["n"] for r in rows}


# Initialize on import
init_db()
