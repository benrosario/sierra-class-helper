"""
Unit tests for course formatting utilities.
Run with: pytest tests/
"""
import pytest
from src.utils.course_formatting import informalName, meetingDays
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
