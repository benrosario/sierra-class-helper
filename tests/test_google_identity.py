"""Google authentication feature and regression tests; no external requests."""
import base64
import json
import threading
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from google.auth.exceptions import TransportError

from src.api import google_identity
from src.config import Config
from tests.google_tokens import CLIENT_ID, certificate_response, google_token


@pytest.fixture(autouse=True)
def verification(monkeypatch):
    monkeypatch.setattr(Config, "GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(google_identity, "_google_request", certificate_response)


@pytest.mark.parametrize("issuer", ["accounts.google.com", "https://accounts.google.com"])
def test_google_issuers_and_signed_subject_are_accepted(issuer):
    assert google_identity.verify_google_identity(google_token("123456", iss=issuer)) == "google:123456"


def test_identity_uses_subject_not_email_or_display_name():
    first = google_token("stable-id", email="before@example.com", name="Before")
    second = google_token("stable-id", email="after@example.com", name="After")
    assert google_identity.verify_google_identity(first) == google_identity.verify_google_identity(second)


@pytest.mark.parametrize("overrides", [
    {"aud": "attacker.apps.googleusercontent.com"},
    {"aud": [CLIENT_ID, "attacker.apps.googleusercontent.com"]},
    {"iss": "https://attacker.example"},
    {"iss": "https://accounts.google.com.attacker.example"},
    {"exp": 1},
    {"iat": 4000000000},
    {"iat": "not-a-timestamp"},
    {"azp": "attacker.apps.googleusercontent.com"},
    {"sub": None},
    {"sub": 123456},
])
def test_invalid_signed_claims_are_rejected(overrides):
    with pytest.raises(HTTPException) as error:
        google_identity.verify_google_identity(google_token(**overrides))
    assert error.value.status_code == 401
    assert error.value.detail == "Invalid or expired Google ID token"


@pytest.mark.parametrize("claim", ["aud", "iss", "iat", "exp", "sub"])
def test_required_claims_cannot_be_omitted(claim):
    with pytest.raises(HTTPException) as error:
        google_identity.verify_google_identity(google_token(omit=(claim,)))
    assert error.value.status_code == 401


def test_tampering_with_subject_breaks_signature():
    header, payload, signature = google_token().split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["sub"] = "fresh-allowance"
    tampered = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    with pytest.raises(HTTPException) as error:
        google_identity.verify_google_identity(f"{header}.{tampered}.{signature}")
    assert error.value.status_code == 401


def test_unsigned_token_is_rejected():
    _, payload, _ = google_token().split(".")
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    with pytest.raises(HTTPException) as error:
        google_identity.verify_google_identity(f"{header}.{payload}.")
    assert error.value.status_code == 401


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", "e30.W10."])
def test_malformed_token_is_a_401_not_a_server_error(token):
    with pytest.raises(HTTPException) as error:
        google_identity.verify_google_identity(token)
    assert error.value.status_code == 401


def test_missing_configuration_fails_closed(monkeypatch):
    monkeypatch.setattr(Config, "GOOGLE_CLIENT_ID", "")
    with pytest.raises(HTTPException) as error:
        google_identity.verify_google_identity(google_token())
    assert error.value.status_code == 503


def test_google_outage_has_safe_retryable_error(monkeypatch):
    monkeypatch.setattr(google_identity, "_google_request", Mock(side_effect=TransportError("secret transport details")))
    with pytest.raises(HTTPException) as error:
        google_identity.verify_google_identity(google_token())
    assert error.value.status_code == 503
    assert "secret" not in error.value.detail


# Retain the real transport before the autouse fixture substitutes it.
ORIGINAL_REQUEST = google_identity._google_request


def test_certificate_transport_bounds_timeout_and_reuses_session(monkeypatch):
    monkeypatch.setattr(google_identity, "_transport", threading.local())
    factory = Mock(return_value=Mock())
    monkeypatch.setattr(google_identity, "GoogleRequest", factory)
    ORIGINAL_REQUEST("https://www.googleapis.com/oauth2/v1/certs")
    ORIGINAL_REQUEST("https://www.googleapis.com/oauth2/v1/certs")
    assert factory.call_count == 1
    assert factory.return_value.call_count == 2
    assert factory.return_value.call_args.kwargs["timeout"] == Config.GOOGLE_CERT_TIMEOUT_SECONDS


@pytest.mark.parametrize("origin", [
    "*", "https://*.example.com", "https://example.com/", "https://example.com/path",
    "https://example.com?q=x", "https://example.com#fragment", "https://user:pass@example.com",
    "http://example.com", "https://example.com:bad", "https://example.com:99999",
    "https://exa mple.com", "null",
])
def test_unsafe_origin_configuration_is_rejected(monkeypatch, origin):
    monkeypatch.setattr(Config, "BOT_API_TOKEN", "")
    monkeypatch.setattr(Config, "DEMO_ALLOWED_ORIGINS", [origin])
    with pytest.raises(ValueError):
        Config.validate_api_security()


def test_local_http_is_development_only(monkeypatch):
    monkeypatch.setattr(Config, "BOT_API_TOKEN", "")
    monkeypatch.setattr(Config, "DEMO_ALLOWED_ORIGINS", ["http://localhost:3000"])
    monkeypatch.setattr(Config, "ENVIRONMENT", "development")
    Config.validate_api_security()
    monkeypatch.setattr(Config, "ENVIRONMENT", "production")
    with pytest.raises(ValueError):
        Config.validate_api_security()


def test_exact_https_origins_are_accepted(monkeypatch):
    monkeypatch.setattr(Config, "BOT_API_TOKEN", "")
    monkeypatch.setattr(Config, "DEMO_ALLOWED_ORIGINS", ["https://example.com", "https://www.example.com"])
    monkeypatch.setattr(Config, "ENVIRONMENT", "production")
    Config.validate_api_security()


def test_malformed_google_client_configuration_is_rejected(monkeypatch):
    monkeypatch.setattr(Config, "BOT_API_TOKEN", "")
    monkeypatch.setattr(Config, "GOOGLE_CLIENT_ID", "wrong-client-id")
    with pytest.raises(ValueError, match="SIERRA_GOOGLE_CLIENT_ID"):
        Config.validate_api_security()
