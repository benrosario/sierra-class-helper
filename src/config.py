"""
Central configuration for Sierra Class Helper.

This module is the definitive source of truth for:
  - model names / embedding dimension
  - filenames for persistent data (composed with DATA_DIR in src.utils.paths)
  - values sourced from environment variables (API keys, data dir, feature flags)

Any hardcoded model name, filename, or env-var lookup elsewhere in the codebase
is a bug — read from Config instead.
"""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

# Load .env before reading any environment variables so the values below reflect
# what the developer set locally. On Railway, load_dotenv() is a harmless no-op —
# environment variables set by the service dashboard are already in os.environ
# and load_dotenv() does not override them.
env_path = Path(".") / ".env"
if env_path.exists():
    load_dotenv(env_path)


class Config:
    """Application configuration - the definitive source for tunables."""

    # --- Environment / secrets ---
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
    API_URL = os.environ.get("API_URL", "http://localhost:8000")
    PORT = int(os.environ.get("PORT", 8000))
    ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")
    ADMIN_TOKEN = os.environ.get("SIERRA_ADMIN_TOKEN")
    BOT_API_TOKEN = os.environ.get("SIERRA_BOT_API_TOKEN", "")
    GOOGLE_CLIENT_ID = os.environ.get("SIERRA_GOOGLE_CLIENT_ID", "").strip()
    DEMO_ALLOWED_ORIGINS = [
        origin.strip() for origin in os.environ.get("SIERRA_DEMO_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]
    GOOGLE_TOKEN_MAX_BYTES = 8192
    GOOGLE_CERT_TIMEOUT_SECONDS = 5
    DEMO_AUTH_IP_RATE = "30/minute"
    DEMO_AUTH_GLOBAL_RATE = "300/minute"
    DEMO_MESSAGE_LIMIT = 3  # Lifetime allowance; never resets with a session.
    DEMO_DAILY_LIMIT = int(os.environ.get("SIERRA_DEMO_DAILY_LIMIT", "100"))
    MAX_REQUEST_BYTES = 64 * 1024
    MAX_MESSAGE_CHARS = 2000
    MAX_HISTORY_CHARS = 12000
    MAX_HISTORY_ENTRY_CHARS = 8000
    MAX_CONTEXT_CHARS = 24000
    MAX_COMPLETION_TOKENS = 2000
    ENABLE_SCHEDULER = os.environ.get("SIERRA_ENABLE_SCHEDULER") == "1"
    BOT_CHANNEL_IDS: set[int] = {
        int(cid) for cid in os.environ.get("SIERRA_BOT_CHANNEL_IDS", "").split(",")
        if cid.strip()
    }

    # --- OpenAI models ---
    EMBEDDING_MODEL = "text-embedding-3-small"
    EMBEDDING_DIMENSION = 1536
    CHAT_MODEL = "gpt-5.6-luna"

    # --- Persistent data ---
    # Root directory for writable state. '.' (repo root) locally; on Railway
    # this points at a mounted Volume so data survives container restarts.
    # src.utils.paths composes the filenames below onto this directory.
    DATA_DIR = Path(os.environ.get("SIERRA_DATA_DIR", "."))

    INDEX_FILE = "courses.index"
    METADATA_FILE = "id_to_course.json"
    COURSE_DATA_DIRNAME = "course_data"
    DEBUG_DIRNAME = "_debug"
    PROFESSOR_RATINGS_FILE = "professor_ratings.json"
    COURSE_HASHES_FILE = "course_hashes.json"
    COURSE_HASHES_NO_ENROLLMENT_FILE = "course_hashes_no_enrollment.json"
    ANALYTICS_DB_FILE = "analytics.sqlite"
    DEMO_DB_FILE = "demo.sqlite"

    @classmethod
    def require_bot_api_token(cls) -> str:
        if len(cls.BOT_API_TOKEN) < 32:
            raise ValueError("SIERRA_BOT_API_TOKEN must contain at least 32 characters")
        return cls.BOT_API_TOKEN

    @classmethod
    def validate_api_security(cls) -> None:
        """Optional endpoints stay disabled when unconfigured; weak keys fail startup."""
        if cls.BOT_API_TOKEN and len(cls.BOT_API_TOKEN) < 32:
            raise ValueError("SIERRA_BOT_API_TOKEN must contain at least 32 characters")
        if cls.GOOGLE_CLIENT_ID and not re.fullmatch(
            r"[a-zA-Z0-9_-]+\.apps\.googleusercontent\.com", cls.GOOGLE_CLIENT_ID
        ):
            raise ValueError("SIERRA_GOOGLE_CLIENT_ID must be a Google OAuth web client ID")
        for origin in cls.DEMO_ALLOWED_ORIGINS:
            parsed = urlsplit(origin)
            local_http = (not cls.is_production() and parsed.scheme == "http"
                          and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
            if (not parsed.hostname or (parsed.scheme != "https" and not local_http)
                    or parsed.path or parsed.query or parsed.fragment or parsed.username
                    or parsed.password or "*" in origin or any(c.isspace() for c in origin)):
                raise ValueError("SIERRA_DEMO_ALLOWED_ORIGINS must contain exact HTTPS origins (no paths or wildcards)")
            # Accessing port also rejects invalid or out-of-range port values.
            _ = parsed.port
        if cls.DEMO_DAILY_LIMIT < 1:
            raise ValueError("SIERRA_DEMO_DAILY_LIMIT must be positive")

    @classmethod
    def require_api_url(cls) -> str:
        """
        Return API_URL, refusing the localhost default in production so a
        misconfigured bot service can't silently point at nothing.
        """
        api_url = os.environ.get("API_URL")
        if cls.is_production() and not api_url:
            raise ValueError(
                "API_URL environment variable is required in production. "
                "On Railway this should be set automatically to the api service's private domain."
            )
        return api_url or "http://localhost:8000"

    @classmethod
    def validate(cls) -> None:
        """Fail fast on missing required config."""
        errors = []
        if not cls.OPENAI_API_KEY:
            errors.append("OPENAI_API_KEY is required")
        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")

    @classmethod
    def is_production(cls) -> bool:
        return cls.ENVIRONMENT == "production"
