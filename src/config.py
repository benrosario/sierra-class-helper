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
from pathlib import Path
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
