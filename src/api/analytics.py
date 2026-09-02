"""
Lightweight engagement analytics for the Discord bot.

Records every /chat call into a SQLite table at paths.ANALYTICS_DB. The
schema is one row per message, which gives us flexibility later to compute
DAU/WAU/MAU or break down by hour without changing the recording code.

Only the api process writes here (single writer, no need for WAL juggling).
Reads happen on demand from the /admin/stats endpoint.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from src.utils.paths import ANALYTICS_DB, ensure_dirs

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id TEXT NOT NULL,
    ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    message_length INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(discord_user_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(ANALYTICS_DB, timeout=10)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# SQLite writes CURRENT_TIMESTAMP as UTC text in 'YYYY-MM-DD HH:MM:SS' form, and
# `ts` is a TEXT column, so every window comparison below is lexicographic.
_SQLITE_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _cutoff(now: datetime, days: int) -> str:
    """
    Render a "N days ago" cutoff in SQLite's own timestamp format.

    This must not use isoformat(). Python separates date and time with 'T'
    (0x54), SQLite with a space (0x20), and ' ' sorts *before* 'T' — so any row
    sharing a calendar date with the cutoff compared as older than it and
    dropped out of the count. That silently under-reported every active-user
    number, DAU worst of all: a message from two hours ago was excluded.
    """
    return (now - timedelta(days=days)).strftime(_SQLITE_TS_FORMAT)


def init_schema() -> None:
    """Create the table + indexes if they don't exist yet."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)
    logger.info(f"Analytics schema initialized at {ANALYTICS_DB}")


def record_message(discord_user_id: str | None, message_length: int) -> None:
    """Insert one row per /chat call. No-op if the bot didn't send a user ID."""
    if not discord_user_id:
        return
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO messages (discord_user_id, message_length) VALUES (?, ?)",
                (discord_user_id, message_length),
            )
    except Exception:
        # Analytics must never break a real request.
        logger.exception("Failed to record analytics event")


def get_stats() -> dict:
    """Return engagement aggregates suitable for the /admin/stats endpoint."""
    with _connect() as conn:
        cur = conn.cursor()
        total_messages = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        unique_users = cur.execute(
            "SELECT COUNT(DISTINCT discord_user_id) FROM messages"
        ).fetchone()[0]

        now = datetime.now(timezone.utc)
        dau = cur.execute(
            "SELECT COUNT(DISTINCT discord_user_id) FROM messages WHERE ts >= ?",
            (_cutoff(now, days=1),),
        ).fetchone()[0]
        wau = cur.execute(
            "SELECT COUNT(DISTINCT discord_user_id) FROM messages WHERE ts >= ?",
            (_cutoff(now, days=7),),
        ).fetchone()[0]
        mau = cur.execute(
            "SELECT COUNT(DISTINCT discord_user_id) FROM messages WHERE ts >= ?",
            (_cutoff(now, days=30),),
        ).fetchone()[0]

        first_seen_row = cur.execute("SELECT MIN(ts) FROM messages").fetchone()
        last_seen_row = cur.execute("SELECT MAX(ts) FROM messages").fetchone()

    return {
        "total_messages": total_messages,
        "unique_users": unique_users,
        "active_users_24h": dau,
        "active_users_7d": wau,
        "active_users_30d": mau,
        "first_message_at": first_seen_row[0] if first_seen_row else None,
        "last_message_at": last_seen_row[0] if last_seen_row else None,
    }
