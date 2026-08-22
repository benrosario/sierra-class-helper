"""
Concrete Path objects for every persistent-data file.

Config owns the filenames and the data-directory root; this module just glues
them into pathlib.Path objects so existing callers can keep writing
`from src.utils.paths import COURSES_INDEX` without change.

On Railway the DATA_DIR is a mounted Volume (SIERRA_DATA_DIR=/data) so the
files here survive container restarts. Locally it defaults to the repo root.
"""
from __future__ import annotations

from pathlib import Path

from src.config import Config

DATA_DIR: Path = Config.DATA_DIR

COURSES_INDEX = DATA_DIR / Config.INDEX_FILE
ID_TO_COURSE_JSON = DATA_DIR / Config.METADATA_FILE
COURSE_DATA_DIR = DATA_DIR / Config.COURSE_DATA_DIRNAME
DEBUG_DIR = COURSE_DATA_DIR / Config.DEBUG_DIRNAME
PROFESSOR_RATINGS_JSON = DATA_DIR / Config.PROFESSOR_RATINGS_FILE
COURSE_HASHES_JSON = DATA_DIR / Config.COURSE_HASHES_FILE
COURSE_HASHES_NO_ENROLLMENT_JSON = DATA_DIR / Config.COURSE_HASHES_NO_ENROLLMENT_FILE
ANALYTICS_DB = DATA_DIR / Config.ANALYTICS_DB_FILE


def ensure_dirs() -> None:
    """Create the directories the app writes to. Called once at startup."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COURSE_DATA_DIR.mkdir(parents=True, exist_ok=True)
