"""
Tests for the bot's /search course formatting and outgoing-message helpers.

These are pure functions — no Discord connection or API key required.
"""
from src.bot.bot import format_course_block, format_outgoing, DISCLAIMER


SAMPLE_COURSE = {
    "subject": "MATH",
    "courseNumber": "0033",
    "courseTitle": "Diff Equations/Linear Alg",
    "CRN": "40310",
    "faculty": [{"name": "Balaguy, Daniel J."}],
    "instructorRating": "3.5/5, 282 ratings, 43% would take again",
    "meetings": [{
        "days": "MW", "begin": "0900", "end": "1050",
        "building": "V", "room": "324",
        "startDate": "01/26/2026", "endDate": "05/23/2026",
    }],
    "enrollment": {"enrolled": 31, "max": 35, "waitCount": 0, "waitCapacity": 20},
}


class TestFormatCourseBlock:
    def test_lines_in_order(self):
        lines = format_course_block(SAMPLE_COURSE).strip().split("\n")
        assert len(lines) == 7
        assert lines[0] == "📚 **MATH0033** | Diff Equations/Linear Alg | CRN: 40310"
        assert lines[1] == "👤 Daniel J. Balaguy — RateMyProfessors: 3.5/5, 282 ratings, 43% would take again"
        assert lines[2] == "📅 01/26/2026 → 05/23/2026"
        assert lines[3] == "📍 Rocklin Campus"
        assert lines[4].startswith("🕒 Mon/Wed 9:00am–10:50am in V 324")
        assert lines[5] == "💺 Seats: 31/35"
        assert lines[6] == "📋 Waitlist: 0/20"

    def test_no_rmp_abbreviation(self):
        block = format_course_block(SAMPLE_COURSE)
        assert "RateMyProfessors:" in block
        assert "RMP" not in block

    def test_missing_instructor_and_rating(self):
        course = dict(SAMPLE_COURSE, faculty=[], instructorRating=None)
        block = format_course_block(course)
        assert "👤 TBA" in block


class TestFormatOutgoing:
    def test_disclaimer_link_is_clickable(self):
        # A full https:// URL is what Discord auto-links.
        assert "https://sierracollege.edu" in DISCLAIMER
        assert format_outgoing("hi")[-1].endswith(DISCLAIMER)
