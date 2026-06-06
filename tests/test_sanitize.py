"""
Unit tests for HTML-entity sanitization.
"""
from src.utils.sanitize import unescape_html


class TestUnescapeHtml:
    def test_decodes_string(self):
        assert unescape_html("Math &amp; Tech") == "Math & Tech"

    def test_decodes_apostrophe(self):
        assert unescape_html("Children&#39;s Lit") == "Children's Lit"

    def test_recurses_into_dict_and_list(self):
        course = {
            "courseTitle": "A &amp; B",
            "meetings": [{"building": "X &amp; Y", "room": "324"}],
            "credits": 3,
            "instructor": None,
        }
        out = unescape_html(course)
        assert out["courseTitle"] == "A & B"
        assert out["meetings"][0]["building"] == "X & Y"
        # Non-string scalars pass through untouched.
        assert out["credits"] == 3
        assert out["instructor"] is None

    def test_idempotent_on_clean_text(self):
        clean = {"title": "Already clean", "n": 1}
        assert unescape_html(clean) == clean
