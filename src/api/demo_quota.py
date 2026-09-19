"""Persistent, atomic demo admission. No paid work runs before a slot is committed."""
import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from fastapi import HTTPException

from src.config import Config


def _connect():
    return sqlite3.connect(Config.DATA_DIR / Config.DEMO_DB_FILE, timeout=10)


def init_demo_schema() -> None:
    Config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with closing(_connect()) as conn, conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS demo_users (
                user_hash TEXT PRIMARY KEY,
                used INTEGER NOT NULL CHECK (used >= 0 AND used <= 3)
            );
            CREATE TABLE IF NOT EXISTS demo_days (
                day TEXT PRIMARY KEY,
                used INTEGER NOT NULL CHECK (used >= 0)
            );
        """)


def reserve_message(user_id: str) -> int:
    """Consume one lifetime attempt and return remaining slots.

    Attempts remain charged on model errors or disconnects: paid work may have
    already occurred. BEGIN IMMEDIATE serializes admission across processes on
    the same volume; SQLite failure is fatal, never a free/unmetered request.
    """
    user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        day = datetime.now(timezone.utc).date().isoformat()
        row = conn.execute("SELECT used FROM demo_users WHERE user_hash = ?", (user_hash,)).fetchone()
        used = row[0] if row else 0
        if used >= Config.DEMO_MESSAGE_LIMIT:
            raise HTTPException(429, detail={
                "code": "demo_limit_reached",
                "message": "You have used all 3 demo messages.",
                "messages_remaining": 0,
            })
        daily = conn.execute("SELECT used FROM demo_days WHERE day = ?", (day,)).fetchone()
        if daily and daily[0] >= Config.DEMO_DAILY_LIMIT:
            raise HTTPException(429, detail={
                "code": "demo_daily_limit_reached",
                "message": "The demo is at capacity. Please try again tomorrow (UTC).",
            })
        conn.execute("""
            INSERT INTO demo_users (user_hash, used) VALUES (?, 1)
            ON CONFLICT(user_hash) DO UPDATE SET used = used + 1
        """, (user_hash,))
        conn.execute("""
            INSERT INTO demo_days (day, used) VALUES (?, 1)
            ON CONFLICT(day) DO UPDATE SET used = used + 1
        """, (day,))
        conn.execute("DELETE FROM demo_days WHERE day < ?", (day,))
    return Config.DEMO_MESSAGE_LIMIT - used - 1
