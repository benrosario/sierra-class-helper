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


# NOTE: TestDetectLanguage / TestIsCourseRelatedQuestion /
# TestDetectTopicContinuation removed intentionally — those three LLM-backed
# classifiers were folded into the main /chat response prompt so every request
# is one model call instead of four. Language handling, identity-vs-course
# gating, and topic-continuation now live in `get_system_prompts()` instead.


class TestBuildSearchQuery:
    """Unit tests for _build_search_query — no LLM, purely deterministic."""

    def test_no_history_returns_current_message(self):
        from src.api.server import _build_search_query, ChatRequest
        req = ChatRequest(message="physics 205", conversation_history=None)
        assert _build_search_query(req) == "physics 205"

    def test_prepends_recent_user_messages_for_followup(self):
        from src.api.server import _build_search_query, ChatRequest, ConversationMessage
        req = ChatRequest(
            message="what about summer?",
            conversation_history=[
                ConversationMessage(role="user", content="show me math classes"),
                ConversationMessage(role="assistant", content="Here are..."),
            ],
        )
        # The current message alone is uninterpretable; prior user turn is kept.
        assert _build_search_query(req) == "show me math classes what about summer?"

    def test_ignores_assistant_turns(self):
        from src.api.server import _build_search_query, ChatRequest, ConversationMessage
        req = ChatRequest(
            message="tell me more",
            conversation_history=[
                ConversationMessage(role="assistant", content="I found 3 courses..."),
            ],
        )
        assert _build_search_query(req) == "tell me more"


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
