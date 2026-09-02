"""
Guards on the bot module's import-time behavior.

Regression: bot.py raised ValueError at module scope when DISCORD_BOT_TOKEN was
unset. tests/test_bot_format.py and tests/test_bot_history.py import that module
to reach pure helpers that never touch Discord, so the raise broke pytest
collection anywhere without a .env — CI, or a fresh clone — before a single test
ran. The token is checked in main() now, so importing must stay possible while
starting up must still fail fast.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.bot.bot import _require_discord_token

REPO_ROOT = Path(__file__).resolve().parents[1]


def _import_bot_with(**env_overrides) -> subprocess.CompletedProcess:
    """Import src.bot.bot in a fresh interpreter under the given environment."""
    return subprocess.run(
        [sys.executable, "-c", "import src.bot.bot"],
        cwd=REPO_ROOT,
        env={**os.environ, **env_overrides},
        capture_output=True,
        text=True,
    )


def test_module_imports_without_a_discord_token():
    """A fresh interpreter with no token must still be able to import the bot."""
    # Blank rather than unset: python-dotenv won't override a variable that is
    # already present, so this holds even with a local .env sitting there.
    result = _import_bot_with(DISCORD_BOT_TOKEN="")
    assert result.returncode == 0, result.stderr


def test_startup_still_fails_fast_without_a_token(monkeypatch):
    monkeypatch.setattr("src.bot.bot.Config.DISCORD_BOT_TOKEN", "")
    with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
        _require_discord_token()


def test_startup_returns_the_configured_token(monkeypatch):
    monkeypatch.setattr("src.bot.bot.Config.DISCORD_BOT_TOKEN", "token-123")
    assert _require_discord_token() == "token-123"
