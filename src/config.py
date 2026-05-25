"""
Configuration management for Sierra Class Helper
Loads settings from environment variables
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if it exists
env_path = Path(".") / ".env"
if env_path.exists():
    load_dotenv(env_path)

class Config:
    """Application configuration"""

    # OpenAI
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

    # Discord
    DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

    # API Server
    API_URL = os.environ.get("API_URL", "http://localhost:8000")
    PORT = int(os.environ.get("PORT", 8000))

    # Environment
    ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")

    @classmethod
    def require_api_url(cls):
        """Return API_URL, but refuse the localhost default in production."""
        api_url = os.environ.get("API_URL")
        if cls.is_production() and not api_url:
            raise ValueError(
                "API_URL environment variable is required in production. "
                "On Railway this should be set automatically to the api service's private domain."
            )
        return api_url or "http://localhost:8000"

    # Data files
    INDEX_FILE = "courses.index"
    METADATA_FILE = "id_to_course.json"
    COURSE_DATA_DIR = "course_data"

    # Embedding model
    EMBEDDING_MODEL = "text-embedding-3-small"
    EMBEDDING_DIMENSION = 1536

    # LLM model
    CHAT_MODEL = "gpt-4o-mini"

    @classmethod
    def validate(cls):
        """Validate required configuration"""
        errors = []

        if not cls.OPENAI_API_KEY:
            errors.append("OPENAI_API_KEY is required")

        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")

    @classmethod
    def is_production(cls):
        """Check if running in production"""
        return cls.ENVIRONMENT == "production"
