"""
Unit tests for course formatting utilities.
Run with: pytest tests/
"""
import pytest
from src.utils.course_formatting import (
    informalName,
    meetingDays,
    format_time,
    summarize_meetings,
)
from src.utils.campus import get_campus


class TestInformalName:
    """Test the informalName function"""

    def test_basic_name(self):
        assert informalName("Doe, John") == "John Doe"

    def test_name_with_middle(self):
        assert informalName("Smith, Jane Marie") == "Jane Marie Smith"


class TestMeetingDays:
    """Test the meetingDays function"""

    def test_monday_wednesday(self):
        data = [{"days": "MW"}]
        result = meetingDays(data)
        assert "Monday" in result
        assert "Wednesday" in result

    def test_tuesday_thursday(self):
        data = [{"days": "TTh"}]
        result = meetingDays(data)
        assert "Tuesday" in result
        assert "Thursday" in result
        assert "Monday" not in result

    def test_online(self):
        data = [{"days": "ONLINE"}]
        result = meetingDays(data)
        assert "Online" in result

    def test_no_duplicates(self):
        data = [{"days": "MW"}, {"days": "MW"}]
        result = meetingDays(data)
        assert result.count("Monday") == 1
        assert result.count("Wednesday") == 1


class TestGetCampus:
    """Test the get_campus function"""

    def test_rocklin_campus(self):
        assert get_campus("D-101") == "Rocklin Campus"
        assert get_campus("E-200") == "Rocklin Campus"
        assert get_campus("F") == "Rocklin Campus"

    def test_nevada_county_campus(self):
        assert get_campus("N100") == "Nevada County Campus (Grass Valley/Tahoe-Truckee)"
        assert get_campus("N234") == "Nevada County Campus (Grass Valley/Tahoe-Truckee)"

    def test_online(self):
        assert get_campus("ONLINE") == "Online"

    def test_off_campus(self):
        assert get_campus("HIGH SCHOOL") == "Off-Campus Location"
        assert get_campus("FIRE STATION") == "Off-Campus Location"

    def test_unknown(self):
        assert get_campus("") == "Unknown Campus"
        assert get_campus("XYZ123") == "Unknown Campus"


class TestFormatTime:
    """Test the format_time function"""

    def test_morning(self):
        assert format_time("0900") == "9:00am"

    def test_afternoon(self):
        assert format_time("1350") == "1:50pm"

    def test_noon(self):
        assert format_time("1200") == "12:00pm"

    def test_midnight(self):
        assert format_time("0000") == "12:00am"

    def test_three_digit(self):
        assert format_time("930") == "9:30am"

    def test_missing_returns_tba(self):
        assert format_time(None) == "TBA"
        assert format_time("") == "TBA"
        assert format_time("TBA") == "TBA"

    def test_out_of_range_returns_tba(self):
        assert format_time("2500") == "TBA"


class TestSummarizeMeetings:
    """Test the summarize_meetings function"""

    def test_full_meeting(self):
        result = summarize_meetings([
            {"days": "MW", "begin": "0900", "end": "1050", "building": "V", "room": "303"}
        ])
        assert result == "Mon/Wed 9:00am–10:50am in V 303"

    def test_no_location(self):
        result = summarize_meetings([
            {"days": "TTh", "begin": "1100", "end": "1250", "building": "", "room": ""}
        ])
        assert result == "Tue/Thu 11:00am–12:50pm"

    def test_empty_returns_tba(self):
        assert summarize_meetings([]) == "Meeting time TBA"

    def test_online(self):
        result = summarize_meetings([{"days": "ONLINE", "begin": None, "end": None}])
        assert "Online" in result
