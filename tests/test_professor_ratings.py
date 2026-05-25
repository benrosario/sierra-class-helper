"""
Unit tests for professor ratings lookup and the RMP scraper's transform layer.

Network calls to RMP are not exercised here — those are spot-checked manually
during verification per the project plan.
"""
import json
from pathlib import Path

import pytest

from src.utils.professor_ratings import (
    _split_sierra_name,
    format_rating,
    get_rating,
    reset_cache,
)
from src.scraper.ratemyprofessors import _make_key, _node_to_record


@pytest.fixture(autouse=True)
def _reset():
    """Make sure the module-level cache doesn't leak between tests."""
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def ratings_file(tmp_path):
    """Write a small professor_ratings.json into a temp dir and return the path."""
    data = {
        "groff, dan t.": {
            "first": "Dan",
            "last": "Groff",
            "rating": 3.8,
            "difficulty": 2.4,
            "num_ratings": 27,
            "would_take_again_pct": 75.0,
            "department": "Administration of Justice",
            "rmp_id": "1234567",
            "url": "https://www.ratemyprofessors.com/professor/1234567",
        },
        "mcgill, ralph": {
            "first": "Ralph",
            "last": "McGill",
            "rating": 4.2,
            "difficulty": 2.1,
            "num_ratings": 15,
            "would_take_again_pct": 90.0,
            "department": "Administration of Justice",
            "rmp_id": "2345678",
            "url": "https://www.ratemyprofessors.com/professor/2345678",
        },
        "doe, jane": {
            "first": "Jane",
            "last": "Doe",
            "rating": None,
            "difficulty": None,
            "num_ratings": 0,
            "would_take_again_pct": None,
            "department": "Test",
            "rmp_id": "9999999",
            "url": "https://www.ratemyprofessors.com/professor/9999999",
        },
    }
    p = tmp_path / "professor_ratings.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


class TestSplitSierraName:
    def test_with_middle_initial(self):
        assert _split_sierra_name("Groff, Dan T.") == ("groff", "dan", "t")

    def test_without_middle_initial(self):
        assert _split_sierra_name("Doe, Jane") == ("doe", "jane", "")

    def test_multi_word_last_name(self):
        # Some last names contain spaces, e.g. "Van Buren, Martin"
        assert _split_sierra_name("Van Buren, Martin") == ("van buren", "martin", "")

    def test_missing_comma_returns_none(self):
        assert _split_sierra_name("John Smith") is None

    def test_empty_first_returns_none(self):
        assert _split_sierra_name("Smith,") is None


class TestGetRating:
    def test_exact_match_with_middle_initial(self, ratings_file):
        result = get_rating("Groff, Dan T.", path=ratings_file)
        assert result is not None
        assert result["rmp_id"] == "1234567"

    def test_match_drops_middle_initial(self, ratings_file):
        # Sierra has middle initial; RMP record does not. Should still match.
        result = get_rating("McGill, Ralph L.", path=ratings_file)
        assert result is not None
        assert result["rmp_id"] == "2345678"

    def test_no_match_returns_none(self, ratings_file):
        assert get_rating("Nobody, Someone", path=ratings_file) is None

    def test_malformed_name_returns_none(self, ratings_file):
        assert get_rating("Just A String", path=ratings_file) is None

    def test_missing_file_returns_none(self, tmp_path):
        # File does not exist — should not raise, should just return None.
        missing = str(tmp_path / "does_not_exist.json")
        assert get_rating("Groff, Dan T.", path=missing) is None

    def test_case_insensitive(self, ratings_file):
        assert get_rating("GROFF, dan t.", path=ratings_file) is not None


@pytest.fixture
def prefix_ratings_file(tmp_path):
    """Fixture with nickname/fullname cases to exercise the prefix-uniqueness fallback."""
    data = {
        # Sierra says "Dan", RMP says "Daniel" — unique within last name "groff"
        "groff, daniel": {"first": "Daniel", "last": "Groff", "rating": 4.0, "num_ratings": 17, "would_take_again_pct": 80, "rmp_id": "1", "url": "u1"},
        # Two different "Smith" professors — first names overlap with no unique winner
        "smith, robert": {"first": "Robert", "last": "Smith", "rating": 3.0, "num_ratings": 5, "would_take_again_pct": None, "rmp_id": "2", "url": "u2"},
        "smith, ron":    {"first": "Ron",    "last": "Smith", "rating": 4.0, "num_ratings": 8, "would_take_again_pct": None, "rmp_id": "3", "url": "u3"},
        # Single "Jones" by full name only — Sierra might write "Jo"
        "jones, jonathan": {"first": "Jonathan", "last": "Jones", "rating": 4.5, "num_ratings": 12, "would_take_again_pct": 95, "rmp_id": "4", "url": "u4"},
    }
    p = tmp_path / "professor_ratings.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


class TestPrefixFallback:
    def test_nickname_matches_unique_full_name(self, prefix_ratings_file):
        # Sierra "Dan" -> RMP "Daniel", unique under "Groff" — should match
        result = get_rating("Groff, Dan T.", path=prefix_ratings_file)
        assert result is not None
        assert result["rmp_id"] == "1"

    def test_full_name_matches_unique_short_name(self, prefix_ratings_file):
        # Sierra "Jonathan" matches a unique "Jonathan" already, but verify the reverse:
        # Sierra "Jo" -> RMP "Jonathan", still unique.
        result = get_rating("Jones, Jo", path=prefix_ratings_file)
        assert result is not None
        assert result["rmp_id"] == "4"

    def test_ambiguous_prefix_returns_none(self, prefix_ratings_file):
        # Sierra "R" could be Robert or Ron — refuse to guess
        result = get_rating("Smith, R", path=prefix_ratings_file)
        assert result is None


class TestFormatRating:
    def test_full_rating(self):
        rec = {"rating": 3.8, "num_ratings": 27, "would_take_again_pct": 75.0}
        out = format_rating(rec)
        assert "3.8/5" in out
        assert "27 ratings" in out
        assert "75% would take again" in out

    def test_singular_rating_count(self):
        rec = {"rating": 4.0, "num_ratings": 1, "would_take_again_pct": None}
        out = format_rating(rec)
        assert "1 rating," not in out  # singular form, no trailing comma issue
        assert "1 rating" in out

    def test_empty_record_returns_empty_string(self):
        # If RMP returned a stub with no ratings, format should yield nothing usable.
        rec = {"rating": None, "num_ratings": 0, "would_take_again_pct": None}
        assert format_rating(rec) == ""


class TestScraperNodeToRecord:
    def test_typical_node(self):
        node = {
            "id": "VGVhY2hlci0xMjM=",
            "legacyId": 1234567,
            "firstName": "Dan",
            "lastName": "Groff",
            "avgRating": 3.8,
            "avgDifficulty": 2.4,
            "numRatings": 27,
            "wouldTakeAgainPercent": 75.0,
            "department": "Administration of Justice",
        }
        rec = _node_to_record(node)
        assert rec["first"] == "Dan"
        assert rec["last"] == "Groff"
        assert rec["rating"] == 3.8
        assert rec["num_ratings"] == 27
        assert rec["rmp_id"] == 1234567
        assert rec["url"] == "https://www.ratemyprofessors.com/professor/1234567"

    def test_missing_legacy_id_yields_no_url(self):
        node = {"firstName": "X", "lastName": "Y", "legacyId": None}
        rec = _node_to_record(node)
        assert rec["url"] is None

    def test_null_num_ratings_coerces_to_zero(self):
        node = {"firstName": "X", "lastName": "Y", "legacyId": 1, "numRatings": None}
        rec = _node_to_record(node)
        assert rec["num_ratings"] == 0


class TestMakeKey:
    def test_basic(self):
        assert _make_key("Dan", "Groff") == "groff, dan"

    def test_strips_whitespace(self):
        assert _make_key("  Dan  ", " Groff ") == "groff, dan"
