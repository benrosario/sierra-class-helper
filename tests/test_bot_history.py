"""
Tests for Discord bot conversation history management.

These are unit tests that don't require external APIs.
"""
import pytest
from unittest.mock import MagicMock


class TestConversationHistory:
    """Test conversation history management in Discord bot"""

    @pytest.fixture
    def cog(self):
        """Create a SierraClassHelper cog with mocked bot"""
        from src.bot.bot import SierraClassHelper
        bot = MagicMock()
        cog = SierraClassHelper(bot)
        return cog

    def test_get_user_history_creates_empty(self, cog):
        """Should create empty list for new users"""
        history = cog.get_user_history(12345)
        assert history == []

    def test_get_user_history_returns_same_list(self, cog):
        """Should return the same (stable) list object for an active user"""
        # An empty user is forgotten between calls, so seed one entry first.
        cog.add_to_history(12345, "user", "hello")
        history1 = cog.get_user_history(12345)
        history2 = cog.get_user_history(12345)
        assert history1 is history2

    def test_add_to_history(self, cog):
        """Should add messages to history"""
        cog.add_to_history(12345, "user", "Hello")
        cog.add_to_history(12345, "assistant", "Hi there!")

        history = cog.get_user_history(12345)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "Hi there!"

    def test_history_trimming(self, cog):
        """Should trim history when exceeding max_history_per_user"""
        # Default is 10, add more than that
        for i in range(25):
            cog.add_to_history(12345, "user", f"Message {i}")

        history = cog.get_user_history(12345)
        assert len(history) == 10
        # Should keep the most recent messages
        assert history[-1]["content"] == "Message 24"
        assert history[0]["content"] == "Message 15"

    def test_clear_user_history(self, cog):
        """Should remove user's history"""
        cog.add_to_history(12345, "user", "Hello")
        cog.add_to_history(12345, "assistant", "Hi!")

        cog.clear_user_history(12345)

        assert 12345 not in cog.conversation_history

    def test_clear_nonexistent_user(self, cog):
        """Should handle clearing non-existent user gracefully"""
        # Should not raise
        cog.clear_user_history(99999)

    def test_user_isolation(self, cog):
        """Different users should have separate histories"""
        cog.add_to_history(11111, "user", "User 1 message")
        cog.add_to_history(22222, "user", "User 2 message")

        history1 = cog.get_user_history(11111)
        history2 = cog.get_user_history(22222)

        assert len(history1) == 1
        assert len(history2) == 1
        assert history1[0]["content"] == "User 1 message"
        assert history2[0]["content"] == "User 2 message"

    def test_history_structure(self, cog):
        """History entries should have correct structure"""
        cog.add_to_history(12345, "user", "Test message")

        history = cog.get_user_history(12345)
        entry = history[0]

        assert "role" in entry
        assert "content" in entry
        assert entry["role"] == "user"
        assert entry["content"] == "Test message"

    def test_alternating_roles(self, cog):
        """Should correctly store alternating user/assistant messages"""
        cog.add_to_history(12345, "user", "Question 1")
        cog.add_to_history(12345, "assistant", "Answer 1")
        cog.add_to_history(12345, "user", "Question 2")
        cog.add_to_history(12345, "assistant", "Answer 2")

        history = cog.get_user_history(12345)
        roles = [msg["role"] for msg in history]

        assert roles == ["user", "assistant", "user", "assistant"]

    def test_custom_max_history(self, cog):
        """Should respect custom max_history_per_user"""
        cog.max_history_per_user = 5

        for i in range(10):
            cog.add_to_history(12345, "user", f"Message {i}")

        history = cog.get_user_history(12345)
        assert len(history) == 5

    def test_multiple_users_trimming(self, cog):
        """Trimming one user shouldn't affect others"""
        cog.max_history_per_user = 3

        # Add messages for user 1
        for i in range(5):
            cog.add_to_history(11111, "user", f"User1 Message {i}")

        # Add messages for user 2
        for i in range(2):
            cog.add_to_history(22222, "user", f"User2 Message {i}")

        history1 = cog.get_user_history(11111)
        history2 = cog.get_user_history(22222)

        assert len(history1) == 3  # Trimmed
        assert len(history2) == 2  # Not trimmed


class TestCogInitialization:
    """Test SierraClassHelper cog initialization"""

    def test_cog_initializes_with_bot(self):
        """Should initialize with bot reference"""
        from src.bot.bot import SierraClassHelper
        bot = MagicMock()
        cog = SierraClassHelper(bot)

        assert cog.bot is bot
        assert cog.session is None
        assert cog.conversation_history == {}
        assert cog.max_history_per_user == 10


class TestHistoryExpiry:
    """Test the time-based expiry of conversation history."""

    @pytest.fixture
    def cog(self):
        from src.bot.bot import SierraClassHelper
        return SierraClassHelper(MagicMock())

    def test_expired_entries_are_pruned_on_read(self, cog):
        """Entries older than HISTORY_TTL should be dropped, fresh ones kept."""
        import time
        from src.bot.bot import HISTORY_TTL
        cog.conversation_history[1] = [
            {"role": "user", "content": "old", "ts": time.time() - HISTORY_TTL - 1},
            {"role": "user", "content": "new", "ts": time.time()},
        ]
        history = cog.get_user_history(1)
        assert [e["content"] for e in history] == ["new"]

    def test_fully_expired_user_is_forgotten(self, cog):
        """When every entry has expired, the user is removed from the store."""
        import time
        from src.bot.bot import HISTORY_TTL
        cog.conversation_history[2] = [
            {"role": "user", "content": "old", "ts": time.time() - HISTORY_TTL - 1},
        ]
        assert cog.get_user_history(2) == []
        assert 2 not in cog.conversation_history

    def test_entries_are_timestamped(self, cog):
        """add_to_history should stamp each entry with a 'ts'."""
        cog.add_to_history(3, "user", "hi")
        assert "ts" in cog.conversation_history[3][0]
