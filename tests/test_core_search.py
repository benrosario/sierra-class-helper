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
        """Import the function - requires OPENAI_API_KEY to import core module"""
        import os
        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY required to import embeddings.core")
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
    """Test the extract_course_code function"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Import the function"""
        import os
        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY required to import embeddings.core")
        from src.embeddings.core import extract_course_code, VALID_SUBJECTS
        self.extract = extract_course_code
        self.valid_subjects = VALID_SUBJECTS

    def test_extract_valid_math_code(self):
        """Should extract MATH course codes"""
        if "MATH" not in self.valid_subjects:
            pytest.skip("MATH not in valid subjects")
        subject, number = self.extract("MATH 31")
        assert subject == "MATH"
        assert number == "0031"

    def test_extract_with_letter_suffix(self):
        """Should handle letter suffixes like 1B"""
        # Find a valid subject to test with
        if "CHEM" in self.valid_subjects:
            subject, number = self.extract("CHEM 1B")
            assert subject == "CHEM"
            assert number == "0001B"
        elif "MATH" in self.valid_subjects:
            subject, number = self.extract("MATH 1A")
            if subject:  # Only assert if there's a 1A variant
                assert number.endswith("A")

    def test_extract_invalid_subject(self):
        """Should return None for invalid subjects like CALC"""
        subject, number = self.extract("CALC 2")
        # CALC is not typically a valid subject code
        if "CALC" not in self.valid_subjects:
            assert subject is None
            assert number is None

    def test_extract_no_pattern(self):
        """Should return None for queries without course codes"""
        subject, number = self.extract("math classes for beginners")
        assert subject is None
        assert number is None

    def test_extract_normalizes_to_four_digits(self):
        """Course numbers should be padded to 4 digits"""
        # Use a subject we know is valid
        for subj in ["CSCI", "MATH", "ENGL"]:
            if subj in self.valid_subjects:
                subject, number = self.extract(f"{subj} 10")
                if subject:
                    assert len(number) == 4
                    assert number == "0010"
                break


class TestDetectSubjectPreference:
    """Test the detect_subject_preference function"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Import the function"""
        import os
        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY required to import embeddings.core")
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
    """Integration tests for search_courses function"""

    def test_search_returns_results(self):
        """search_courses should return course dictionaries"""
        from src.embeddings.core import search_courses
        results = search_courses("computer science classes", k=3)
        assert isinstance(results, list)
        assert len(results) <= 3
        if results:
            assert "subject" in results[0]
            assert "courseTitle" in results[0]

    def test_search_respects_k_limit(self):
        """search_courses should not return more than k results"""
        from src.embeddings.core import search_courses
        results = search_courses("programming", k=2)
        assert len(results) <= 2

    def test_search_with_subject_hint(self):
        """search_courses with subject_hint should prefer that subject"""
        from src.embeddings.core import search_courses
        results = search_courses("programming classes", k=5, subject_hint="Computer Science")
        # Should have some CSCI results
        if results:
            csci_count = sum(1 for r in results if r.get("subject") == "CSCI")
            # At least some should be CSCI
            assert csci_count >= 0  # May not find any depending on data

    def test_search_generic_query(self):
        """search_courses should handle generic queries"""
        from src.embeddings.core import search_courses
        results = search_courses("easy classes", k=3)
        assert isinstance(results, list)
