"""
Unit tests for the hybrid search building blocks in src/embeddings/core.py:
exact course-code resolution, the lexical (keyword) channel, and RRF fusion.

Importing core is side-effect free — none of these tests need an OpenAI key or
a built FAISS index.
"""
import pytest

from src.embeddings import core as _core


@pytest.fixture
def core():
    """
    Yield the core module with a small, deterministic VALID_SUBJECTS set so
    exact-code resolution works without initialize() (which would need a real
    OpenAI key + FAISS index). Restored to whatever was there before after each
    test — usually the empty default, but be explicit.
    """
    original = _core.VALID_SUBJECTS
    _core.VALID_SUBJECTS = {"MATH", "PHYS", "CHEM", "CSCI", "BIOL", "BUS", "ENGL"}
    try:
        yield _core
    finally:
        _core.VALID_SUBJECTS = original


def _entry(subject, number, title, desc="Mathematics"):
    return {"course": {
        "subject": subject, "courseNumber": number,
        "courseTitle": title, "subjectDescription": desc,
    }}


class TestResolveCourseCode:
    def test_subject_word_plus_number(self, core):
        # The headline fix: the *word* "physics" + a number resolves to PHYS0205.
        assert core.resolve_course_code("physics 205") == ("PHYS", "0205")

    def test_real_code_still_works(self, core):
        assert core.resolve_course_code("MATH 31") == ("MATH", "0031")

    def test_abbreviated_code(self, core):
        assert core.resolve_course_code("phys 205") == ("PHYS", "0205")

    def test_no_number_is_not_a_code(self, core):
        assert core.resolve_course_code("easy math class") == (None, None)

    def test_non_adjacent_number_is_not_a_code(self, core):
        # "biology" and "20" aren't adjacent, so this is a topic search, not BIOL0020.
        assert core.resolve_course_code("20 best biology classes") == (None, None)


class TestLexicalSearch:
    def test_linear_algebra_beats_college_algebra(self, core):
        entries = [
            _entry("MATH", "0012", "College Algebra"),
            _entry("MATH", "0033", "Diff Equations/Linear Alg"),
            _entry("MATH", "0008", "Elementary Algebra"),
        ]
        index = core.build_lexical_index(entries)
        ranked = core.lexical_search(index, "linear algebra", 5)
        top_pos = ranked[0][0]
        assert entries[top_pos]["course"]["courseTitle"] == "Diff Equations/Linear Alg"

    def test_number_token_matches_padded_and_bare(self, core):
        entries = [_entry("PHYS", "0205", "General Physics"), _entry("PHYS", "0210", "Modern Physics")]
        index = core.build_lexical_index(entries)
        ranked = core.lexical_search(index, "physics 205", 5)
        assert entries[ranked[0][0]]["course"]["courseNumber"] == "0205"

    def test_year_does_not_prefix_match_course_number(self, core):
        # The "linear algebra fall 2026" bug: the year "2026" must NOT match
        # course number "0202" (BUS0202) by prefix — numbers are exact-only.
        entries = [_entry("BUS", "0202", "Financial Accounting II", desc="Business")]
        index = core.build_lexical_index(entries)
        assert core.lexical_search(index, "2026", 5) == []
        # But the exact course number still matches.
        assert core.lexical_search(index, "202", 5)[0][0] == 0


class TestAllSectionsFor:
    def test_expands_to_all_sections(self, core):
        entries = [
            {"course": {"subject": "MATH", "courseNumber": "0033", "CRN": "81908", "courseTitle": "Lin Alg"}},
            {"course": {"subject": "MATH", "courseNumber": "0033", "CRN": "82458", "courseTitle": "Lin Alg"}},
            {"course": {"subject": "MATH", "courseNumber": "0012", "CRN": "1", "courseTitle": "College Alg"}},
        ]
        # A single representative of MATH0033 expands to both of its sections.
        rep = [entries[0]["course"]]
        out = core.all_sections_for(rep, entries)
        assert [c["CRN"] for c in out] == ["81908", "82458"]

    def test_preserves_course_order_and_dedups_input(self, core):
        entries = [
            {"course": {"subject": "MATH", "courseNumber": "0033", "CRN": "a", "courseTitle": "x"}},
            {"course": {"subject": "MATH", "courseNumber": "0033", "CRN": "b", "courseTitle": "x"}},
            {"course": {"subject": "BUS", "courseNumber": "0202", "CRN": "c", "courseTitle": "y"}},
        ]
        # Duplicate input codes collapse; output is grouped per course in input order.
        courses = [entries[0]["course"], entries[1]["course"], entries[2]["course"]]
        out = core.all_sections_for(courses, entries)
        assert [c["CRN"] for c in out] == ["a", "b", "c"]

    def test_top_course_full_others_capped(self, core):
        # Top match expands fully; a popular secondary course is capped to 1 section
        # so it doesn't flood (the "linear algebra" vs College Algebra case).
        entries = [
            {"course": {"subject": "MATH", "courseNumber": "0033", "CRN": "m1", "courseTitle": "Lin"}},
            {"course": {"subject": "MATH", "courseNumber": "0033", "CRN": "m2", "courseTitle": "Lin"}},
            {"course": {"subject": "MATH", "courseNumber": "0012", "CRN": "c1", "courseTitle": "Coll"}},
            {"course": {"subject": "MATH", "courseNumber": "0012", "CRN": "c2", "courseTitle": "Coll"}},
            {"course": {"subject": "MATH", "courseNumber": "0012", "CRN": "c3", "courseTitle": "Coll"}},
        ]
        ranked = [entries[0]["course"], entries[2]["course"]]  # MATH0033 then MATH0012
        out = core.all_sections_for(ranked, entries)  # others_limit=1 default
        assert [c["CRN"] for c in out] == ["m1", "m2", "c1"]
        # others_limit=None expands everything.
        out_full = core.all_sections_for(ranked, entries, others_limit=None)
        assert [c["CRN"] for c in out_full] == ["m1", "m2", "c1", "c2", "c3"]

    def test_empty_query_returns_nothing(self, core):
        entries = [_entry("MATH", "0012", "College Algebra")]
        index = core.build_lexical_index(entries)
        assert core.lexical_search(index, "the of and", 5) == []


class TestFuseCandidates:
    def test_strong_lexical_beats_top_vector(self, core):
        # The "linear algebra" shape: pos 1 has a much higher lexical score but a
        # poor vector rank; pos 0 tops the vector list with a weak lexical score.
        # Magnitude-aware fusion must let the strong keyword match win.
        entries = [_entry("MATH", "0012", "College Algebra"),
                   _entry("MATH", "0033", "Diff Equations/Linear Alg")]
        vector_positions = [0, 1]            # vector prefers pos 0
        lexical_scored = [(1, 43.5), (0, 14.3)]  # lexical strongly prefers pos 1
        out = core.fuse_candidates(vector_positions, lexical_scored, None, entries,
                                   k=2, seen_codes=set())
        assert out[0]["courseTitle"] == "Diff Equations/Linear Alg"

    def test_dedupes_by_course_code(self, core):
        # Two sections of the same course collapse to one distinct result.
        entries = [_entry("MATH", "0012", "College Algebra"),
                   _entry("MATH", "0012", "College Algebra"),
                   _entry("MATH", "0018", "Nature of Math")]
        out = core.fuse_candidates([0, 1, 2], [], None, entries, k=3, seen_codes=set())
        codes = [(c["subject"], c["courseNumber"]) for c in out]
        assert codes == [("MATH", "0012"), ("MATH", "0018")]

    def test_skips_already_seen_codes(self, core):
        entries = [_entry("PHYS", "0205", "Mechanics"), _entry("PHYS", "0210", "Modern")]
        # PHYS0205 already pinned as an exact match — fusion shouldn't repeat it.
        out = core.fuse_candidates([0, 1], [], None, entries, k=2,
                                   seen_codes={("PHYS", "0205")})
        assert [c["courseNumber"] for c in out] == ["0210"]

    def test_preferred_subject_boost(self, core):
        entries = [_entry("A", "1", "a"), _entry("B", "2", "b")]
        # Equal lexical score, no vector signal — the subject bonus is the tiebreaker.
        out = core.fuse_candidates([], [(0, 10.0), (1, 10.0)], "B", entries, k=2, seen_codes=set())
        assert out[0]["subject"] == "B"
