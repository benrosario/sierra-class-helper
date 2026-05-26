"""
Single source of truth for every persistent-data file path in the project.

Why this exists: on Railway we attach a Volume to the api service mounted at
`/data`, so the app's writable state survives container restarts. Locally
during dev, the same files live at the repo root. Centralizing the paths
behind one env var (SIERRA_DATA_DIR) keeps both environments using identical
code paths — only the prefix changes.

Set SIERRA_DATA_DIR=/data in Railway env. Leave unset locally.
"""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("SIERRA_DATA_DIR", "."))

COURSES_INDEX = DATA_DIR / "courses.index"
ID_TO_COURSE_JSON = DATA_DIR / "id_to_course.json"
COURSE_DATA_DIR = DATA_DIR / "course_data"
DEBUG_DIR = COURSE_DATA_DIR / "_debug"
PROFESSOR_RATINGS_JSON = DATA_DIR / "professor_ratings.json"
COURSE_HASHES_JSON = DATA_DIR / "course_hashes.json"
COURSE_HASHES_NO_ENROLLMENT_JSON = DATA_DIR / "course_hashes_no_enrollment.json"
ANALYTICS_DB = DATA_DIR / "analytics.sqlite"


def ensure_dirs() -> None:
    """Create the directories the app writes to. Called once at startup."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COURSE_DATA_DIR.mkdir(parents=True, exist_ok=True)
