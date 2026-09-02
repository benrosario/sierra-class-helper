"""
Regression tests for the /admin/stats aggregates.

The bug these exist for: `ts` is a TEXT column holding SQLite's
CURRENT_TIMESTAMP ('YYYY-MM-DD HH:MM:SS'), so the active-user windows are
lexicographic string comparisons. get_stats() used to build its cutoffs with
datetime.isoformat(), which separates date from time with 'T' (0x54) where
SQLite uses a space (0x20). Since ' ' < 'T', any row sharing a calendar date
with the cutoff sorted as *older* than it and vanished from the count.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.api import analytics
from src.api.analytics import _cutoff, get_stats, init_schema, record_message


@pytest.fixture
def analytics_db(tmp_path, monkeypatch):
    """Point the analytics module at a throwaway database."""
    monkeypatch.setattr(analytics, "ANALYTICS_DB", tmp_path / "analytics.sqlite")
    init_schema()
    return tmp_path


def _insert_at(when: datetime, user: str) -> None:
    """Insert one message with an explicit SQLite-formatted timestamp."""
    with analytics._connect() as conn:
        conn.execute(
            "INSERT INTO messages (discord_user_id, ts, message_length) VALUES (?, ?, ?)",
            (user, when.strftime("%Y-%m-%d %H:%M:%S"), 10),
        )


class TestCutoffFormat:
    def test_cutoff_matches_sqlite_timestamp_format(self):
        now = datetime(2026, 9, 2, 1, 33, 0, tzinfo=timezone.utc)
        assert _cutoff(now, days=1) == "2026-09-01 01:33:00"

    def test_row_on_same_date_as_cutoff_still_sorts_inside_window(self):
        """The exact comparison that used to fail: a message from two hours ago
        landing on the same calendar date as a 24h-old cutoff."""
        now = datetime(2026, 9, 2, 1, 33, 0, tzinfo=timezone.utc)
        cutoff = _cutoff(now, days=1)              # '2026-09-01 01:33:00'
        two_hours_ago = "2026-09-01 23:33:00"      # inside the window
        assert two_hours_ago >= cutoff


class TestStatsWindows:
    def test_recent_message_counts_toward_dau(self, analytics_db):
        _insert_at(datetime.now(timezone.utc) - timedelta(hours=2), "user-1")
        assert get_stats()["active_users_24h"] == 1

    def test_windows_widen_correctly(self, analytics_db):
        now = datetime.now(timezone.utc)
        _insert_at(now - timedelta(hours=2), "recent")
        _insert_at(now - timedelta(days=3), "this-week")
        _insert_at(now - timedelta(days=40), "ancient")

        stats = get_stats()
        assert stats["active_users_24h"] == 1
        assert stats["active_users_7d"] == 2
        assert stats["active_users_30d"] == 2
        assert stats["unique_users"] == 3
        assert stats["total_messages"] == 3

    def test_empty_database_reports_zeros(self, analytics_db):
        stats = get_stats()
        assert stats["total_messages"] == 0
        assert stats["active_users_24h"] == 0
        assert stats["first_message_at"] is None

    def test_record_message_ignores_missing_user_id(self, analytics_db):
        record_message(None, 42)
        assert get_stats()["total_messages"] == 0
