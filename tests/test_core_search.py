"""
Tests for core search logic in src/embeddings/core.py

These tests cover the pure functions that don't require API keys:
- normalize_course_query
- extract_course_code
- detect_subject_preference

Integration tests that require OPENAI_API_KEY are marked with @requires_openai.
"""
import pytest
from tests.conftest import requires_openai, requires_faiss_index


class TestNormalizeCourseQuery:
    """Test the normalize_course_query function"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from src.embeddings.core import normalize_course_query
        self.normalize = normalize_course_query

    def test_normalize_chem1b(self):
        """Chem1B should become CHEM 1B"""
        result = self.normalize("Chem1B")
        assert "CHEM 1B" in result

    def test_normalize_cs50(self):
        """CS50 should become CS 50"""
        result = self.normalize("CS50")
        assert "CS 50" in result

    def test_normalize_math10(self):
        """Math10 should become MATH 10"""
        result = self.normalize("Math10")
        assert "MATH 10" in result

    def test_normalize_preserves_existing_spaces(self):
        """Already properly spaced codes should stay the same"""
        result = self.normalize("CHEM 1B")
        assert "CHEM 1B" in result

    def test_normalize_complex_query(self):
        """Should normalize within longer queries"""
        result = self.normalize("I want to take Chem1B and Math10")
        assert "CHEM 1B" in result
        assert "MATH 10" in result

    def test_normalize_lowercase_becomes_uppercase(self):
        """Subject codes should be uppercased"""
        result = self.normalize("bio12")
        assert "BIO 12" in result


class TestExtractCourseCode:
    """Test the extract_course_code function.

    Passes an explicit valid_subjects set so these tests don't need a
    SearchIndex to be constructed.
    """

    VALID = {"MATH", "CHEM", "CSCI", "ENGL", "PHYS"}

    @pytest.fixture(autouse=True)
    def setup(self):
        from src.embeddings.core import extract_course_code
        self.extract = lambda q: extract_course_code(q, valid_subjects=self.VALID)

    def test_extract_valid_math_code(self):
        assert self.extract("MATH 31") == ("MATH", "0031")

    def test_extract_with_letter_suffix(self):
        assert self.extract("CHEM 1B") == ("CHEM", "0001B")

    def test_extract_invalid_subject(self):
        # CALC not in the whitelist — must be rejected even though the pattern matches.
        assert self.extract("CALC 2") == (None, None)

    def test_extract_no_pattern(self):
        assert self.extract("math classes for beginners") == (None, None)

    def test_extract_normalizes_to_four_digits(self):
        assert self.extract("CSCI 10") == ("CSCI", "0010")


class TestDetectSubjectPreference:
    """Test the detect_subject_preference function"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from src.embeddings.core import detect_subject_preference
        self.detect = detect_subject_preference

    def test_detect_math(self):
        """Should detect 'math' keyword"""
        result = self.detect("math courses")
        assert result == "MATH"

    def test_detect_mathematics(self):
        """Should detect 'mathematics' keyword"""
        result = self.detect("I need a mathematics class")
        assert result == "MATH"

    def test_detect_computer_science(self):
        """Should detect 'computer science' keyword"""
        result = self.detect("computer science classes")
        assert result == "CSCI"

    def test_detect_cs_abbreviation(self):
        """Should detect 'cs' abbreviation"""
        result = self.detect("what cs classes are available")
        assert result == "CSCI"

    def test_detect_biology(self):
        """Should detect biology keywords"""
        result = self.detect("biology courses")
        assert result == "BIOL"

    def test_detect_longest_match_first(self):
        """Should match 'computer science' before 'science'"""
        result = self.detect("computer science")
        assert result == "CSCI"

    def test_detect_no_preference(self):
        """Should return None for generic queries"""
        result = self.detect("what classes are available?")
        assert result is None

    def test_detect_psychology(self):
        """Should detect psychology keywords"""
        result = self.detect("psych classes")
        assert result == "PSYC"


@requires_openai
@requires_faiss_index
class TestSearchCoursesIntegration:
    """Integration tests for SearchIndex.search() — need a real key + built index."""

    def test_search_returns_results(self):
        from src.embeddings.core import get_index
        results = get_index().search("computer science classes", k=3)
        assert isinstance(results, list)
        assert len(results) <= 3
        if results:
            assert "subject" in results[0]
            assert "courseTitle" in results[0]

    def test_search_respects_k_limit(self):
        from src.embeddings.core import get_index
        results = get_index().search("programming", k=2)
        assert len(results) <= 2

    def test_search_generic_query(self):
        from src.embeddings.core import get_index
        results = get_index().search("easy classes", k=3)
        assert isinstance(results, list)
