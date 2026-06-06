"""
Integration tests for the FastAPI server endpoints and LLM functions.

These tests require OPENAI_API_KEY and call real APIs.
"""
import pytest
from tests.conftest import requires_openai, requires_faiss_index


@requires_openai
@requires_faiss_index
class TestSearchEndpoint:
    """Integration tests for POST /search endpoint"""

    @pytest.fixture
    def client(self):
        """Create test client for API"""
        from fastapi.testclient import TestClient
        from src.api.server import app
        return TestClient(app)

    def test_search_basic_query(self, client):
        """Should return courses for a basic query"""
        response = client.post("/search", json={
            "query": "math classes",
            "num_results": 3
        })
        assert response.status_code == 200
        data = response.json()
        assert "courses" in data
        assert "num_results" in data
        assert isinstance(data["courses"], list)

    def test_search_returns_requested_count(self, client):
        """Should respect num_results parameter"""
        response = client.post("/search", json={
            "query": "programming",
            "num_results": 2
        })
        assert response.status_code == 200
        data = response.json()
        assert len(data["courses"]) <= 2

    def test_search_minimal_query(self, client):
        """Should handle minimal queries"""
        response = client.post("/search", json={
            "query": "a",
            "num_results": 1
        })
        assert response.status_code == 200


@requires_openai
@requires_faiss_index
class TestChatEndpoint:
    """Integration tests for POST /chat endpoint"""

    @pytest.fixture
    def client(self):
        """Create test client for API"""
        from fastapi.testclient import TestClient
        from src.api.server import app
        return TestClient(app)

    def test_chat_course_question(self, client):
        """Should return response with courses for course questions"""
        response = client.post("/chat", json={
            "message": "What CS classes are available?",
            "num_courses": 3
        })
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert "courses_searched" in data
        assert isinstance(data["response"], str)
        assert len(data["response"]) > 0

    def test_chat_identity_question(self, client):
        """Should handle identity questions without searching courses"""
        response = client.post("/chat", json={
            "message": "What are you?",
            "num_courses": 3
        })
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert data["courses_searched"] == 0

    def test_chat_with_history(self, client):
        """Should handle conversation history"""
        response = client.post("/chat", json={
            "message": "What about in spring?",
            "num_courses": 3,
            "conversation_history": [
                {"role": "user", "content": "What CS classes are available in fall?"},
                {"role": "assistant", "content": "Here are some CS classes..."}
            ]
        })
        assert response.status_code == 200
        data = response.json()
        assert "response" in data


@requires_openai
class TestDetectLanguage:
    """Tests for detect_language function"""

    def test_detect_english(self):
        """Should detect English text"""
        from src.api.server import detect_language
        code, name = detect_language("What classes are available?")
        assert code == "en"
        assert name == "English"

    def test_detect_spanish(self):
        """Should detect Spanish text"""
        from src.api.server import detect_language
        code, name = detect_language("Que clases hay disponibles?")
        assert code == "es"
        assert name == "Spanish"

    def test_returns_tuple(self):
        """Should return (code, name) tuple"""
        from src.api.server import detect_language
        result = detect_language("Hello")
        assert isinstance(result, tuple)
        assert len(result) == 2


@requires_openai
class TestIsCourseRelatedQuestion:
    """Tests for is_course_related_question function"""

    def test_course_question_true(self):
        """Should return True for course-related questions"""
        from src.api.server import is_course_related_question
        result = is_course_related_question("What math classes are offered?")
        assert result is True

    def test_identity_question_false(self):
        """Should return False for identity questions"""
        from src.api.server import is_course_related_question
        result = is_course_related_question("Who are you?")
        assert result is False

    def test_greeting_false(self):
        """Should return False for greetings"""
        from src.api.server import is_course_related_question
        result = is_course_related_question("Hello")
        assert result is False

    def test_specific_course_true(self):
        """Should return True for specific course queries"""
        from src.api.server import is_course_related_question
        result = is_course_related_question("When does MATH 10 meet?")
        assert result is True


@requires_openai
class TestDetectTopicContinuation:
    """Tests for detect_topic_continuation function"""

    def test_same_topic_followup(self):
        """Should detect follow-up questions as same topic"""
        from src.api.server import detect_topic_continuation, ConversationMessage
        history = [
            ConversationMessage(role="user", content="What CS classes are available?")
        ]
        result = detect_topic_continuation("Which one teaches Python?", history)
        assert result is True

    def test_new_topic_different_subject(self):
        """Should detect topic change when switching subjects"""
        from src.api.server import detect_topic_continuation, ConversationMessage
        history = [
            ConversationMessage(role="user", content="What CS classes are available?")
        ]
        result = detect_topic_continuation("Show me biology courses", history)
        assert result is False

    def test_empty_history(self):
        """Should return False for empty history"""
        from src.api.server import detect_topic_continuation
        result = detect_topic_continuation("Any question", [])
        assert result is False

    def test_none_history(self):
        """Should handle None history gracefully"""
        from src.api.server import detect_topic_continuation
        result = detect_topic_continuation("Any question", None)
        assert result is False


@requires_openai
class TestHealthEndpoints:
    """Tests for health check endpoints"""

    @pytest.fixture
    def client(self):
        """Create test client for API"""
        from fastapi.testclient import TestClient
        from src.api.server import app
        return TestClient(app)

    def test_root_endpoint(self, client):
        """GET / should return status"""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "online"
        assert "service" in data

    def test_health_endpoint(self, client):
        """GET /health should return health info"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "openai_configured" in data
