"""
Shared pytest fixtures for Sierra Class Helper tests
"""
import pytest
import os

from src.utils.paths import COURSES_INDEX, ID_TO_COURSE_JSON


def pytest_configure(config):
    """Register custom markers"""
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests (require API keys)"
    )


# Skip markers for integration tests
requires_openai = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY environment variable not set"
)

requires_faiss_index = pytest.mark.skipif(
    not (COURSES_INDEX.exists() and ID_TO_COURSE_JSON.exists()),
    reason="FAISS index or metadata not found at SIERRA_DATA_DIR"
)


@pytest.fixture
def sample_course_data():
    """Sample course dictionary for testing"""
    return {
        "term": "Fall 2025",
        "CRN": "12345",
        "subject": "CSCI",
        "courseNumber": "0010",
        "courseTitle": "Introduction to Computer Science",
        "subjectDescription": "Computer Science",
        "credits": 3,
        "enrollment": {
            "max": 35,
            "enrolled": 28,
            "available": 7,
            "waitCapacity": 10,
            "waitCount": 0
        },
        "instructionMethod": "In Person",
        "faculty": [
            {"name": "Smith, John", "email": "jsmith@sierracollege.edu"}
        ],
        "meetings": [
            {
                "days": "MW",
                "begin": "0900",
                "end": "1050",
                "building": "D",
                "room": "101",
                "startDate": "2025-08-18",
                "endDate": "2025-12-12"
            }
        ],
        "attributes": ["Transfers to UC/CSU"]
    }


@pytest.fixture
def sample_course_minimal():
    """Minimal course data for edge case testing"""
    return {
        "CRN": "99999",
        "subject": "TEST",
        "courseNumber": "0001",
        "courseTitle": "Test Course",
        "subjectDescription": "Testing",
        "credits": 0,
        "enrollment": {"max": 0, "enrolled": 0, "available": 0, "waitCapacity": 0, "waitCount": 0},
        "faculty": [],
        "meetings": [],
        "attributes": []
    }


@pytest.fixture
def sample_conversation_history():
    """Sample conversation history for chat tests"""
    from src.api.server import ConversationMessage
    return [
        ConversationMessage(role="user", content="What CS classes are available?"),
        ConversationMessage(role="assistant", content="Here are some CS classes available...")
    ]


@pytest.fixture
def temp_course_data_dir(tmp_path):
    """Create temporary course data directory with sample JSON"""
    import json

    data_dir = tmp_path / "course_data"
    data_dir.mkdir()

    sample_data = {
        "12345": {
            "course": {
                "CRN": "12345",
                "subject": "CSCI",
                "courseNumber": "0010",
                "courseTitle": "Intro to CS",
                "subjectDescription": "Computer Science",
                "credits": 3,
                "enrollment": {"max": 35, "enrolled": 20, "available": 15, "waitCapacity": 10, "waitCount": 0},
                "faculty": [{"name": "Doe, Jane", "email": "jdoe@test.edu"}],
                "meetings": [{"days": "MW", "begin": "0900", "end": "1050", "building": "D", "room": "101", "startDate": "2025-01-01", "endDate": "2025-05-01"}],
                "attributes": []
            }
        },
        "67890": {
            "course": {
                "CRN": "67890",
                "subject": "MATH",
                "courseNumber": "0010",
                "courseTitle": "Elementary Algebra",
                "subjectDescription": "Mathematics",
                "credits": 4,
                "enrollment": {"max": 30, "enrolled": 25, "available": 5, "waitCapacity": 5, "waitCount": 2},
                "faculty": [{"name": "Johnson, Bob", "email": "bjohnson@test.edu"}],
                "meetings": [{"days": "TTh", "begin": "1100", "end": "1250", "building": "E", "room": "200", "startDate": "2025-01-01", "endDate": "2025-05-01"}],
                "attributes": []
            }
        }
    }

    json_file = data_dir / "fall2025_2025-01-01_00-00-00-0800.json"
    json_file.write_text(json.dumps(sample_data))

    return data_dir
