"""
Lookup professor ratings scraped from RateMyProfessors.

Sierra College stores instructor names as "Last, First M." (with optional middle
initial). RMP stores them as separate firstName / lastName. This module bridges
the two with a small normalization layer.

Matching strategy, in order:
  1. exact last + first + middle initial
  2. exact last + first (drops middle initial)
  3. last + first-name prefix, ONLY if there is a unique candidate
     (handles "Dan" -> "Daniel"; rejects when ambiguous to avoid wrong attribution)

Step 3 is bidirectional: either Sierra's first name is a prefix of RMP's, or
vice versa. We reject any non-unique match so we never attribute the wrong
professor's rating to someone else.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from src.utils.paths import PROFESSOR_RATINGS_JSON

logger = logging.getLogger(__name__)

DEFAULT_PATH = str(PROFESSOR_RATINGS_JSON)

_cache: dict | None = None
_cache_path: str | None = None
_with_middle: dict[str, dict] = {}
_no_middle: dict[str, dict] = {}
# last name -> list of (first_name, record) for the prefix-uniqueness fallback
_by_last: dict[str, list[tuple[str, dict]]] = {}


def _normalize(text: str) -> str:
    """Lowercase, strip surrounding whitespace, collapse internal whitespace, drop periods."""
    return re.sub(r"\s+", " ", text.replace(".", "").strip().lower())


def _split_sierra_name(name: str) -> Optional[tuple[str, str, str]]:
    """
    Parse Sierra's "Last, First M." format.

    Returns (last, first, middle_initial) all lowercased and stripped, or None
    if the name doesn't look like the expected format.
    """
    if "," not in name:
        return None
    last_part, first_part = name.split(",", 1)
    last = _normalize(last_part)
    first_tokens = _normalize(first_part).split()
    if not first_tokens or not last:
        return None
    first = first_tokens[0]
    middle = first_tokens[1] if len(first_tokens) > 1 else ""
    return last, first, middle


def _load(path: str) -> dict:
    global _cache, _cache_path, _with_middle, _no_middle, _by_last
    p = Path(path)
    if not p.exists():
        logger.warning(f"{path} not found — professor ratings will be unavailable")
        _cache = {}
        _cache_path = path
        _with_middle = {}
        _no_middle = {}
        _by_last = {}
        return _cache

    _cache = json.loads(p.read_text(encoding="utf-8"))
    _cache_path = path

    # Build lookup tables for the fallback chain.
    _with_middle = {}
    _no_middle = {}
    _by_last = {}
    for key, record in _cache.items():
        parsed = _split_sierra_name(key)
        if not parsed:
            # Keys we wrote should always parse, but be defensive.
            _no_middle[_normalize(key)] = record
            continue
        last, first, middle = parsed
        _no_middle[f"{last}|{first}"] = record
        if middle:
            _with_middle[f"{last}|{first}|{middle}"] = record
        _by_last.setdefault(last, []).append((first, record))

    return _cache


def _ensure_loaded(path: str) -> None:
    if _cache is None or _cache_path != path:
        _load(path)


def get_rating(faculty_name: str, path: str = DEFAULT_PATH) -> Optional[dict]:
    """
    Return the rating record for a Sierra-format faculty name, or None.

    See module docstring for the matching strategy.
    """
    _ensure_loaded(path)
    parsed = _split_sierra_name(faculty_name)
    if not parsed:
        return None
    last, first, middle = parsed

    if middle:
        hit = _with_middle.get(f"{last}|{first}|{middle}")
        if hit:
            return hit

    hit = _no_middle.get(f"{last}|{first}")
    if hit:
        return hit

    # Bidirectional first-name prefix fallback. Only commits when unique.
    candidates = [
        record
        for rmp_first, record in _by_last.get(last, [])
        if rmp_first.startswith(first) or first.startswith(rmp_first)
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def format_rating(rating: dict) -> str:
    """Render a rating record as a short inline string for LLM context."""
    parts = [f"RMP: {rating['rating']:.1f}/5"] if rating.get("rating") else []
    if rating.get("num_ratings"):
        parts.append(f"{rating['num_ratings']} rating{'s' if rating['num_ratings'] != 1 else ''}")
    if rating.get("would_take_again_pct") is not None:
        parts.append(f"{int(round(rating['would_take_again_pct']))}% would take again")
    return ", ".join(parts) if parts else ""


def reset_cache() -> None:
    """Test helper: drop the in-memory cache so the next call reloads."""
    global _cache, _cache_path, _with_middle, _no_middle, _by_last
    _cache = None
    _cache_path = None
    _with_middle = {}
    _no_middle = {}
    _by_last = {}
