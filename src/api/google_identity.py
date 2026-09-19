"""Verify Google-issued ID tokens; no user registration or shared demo secret."""
import threading

import cachecontrol
import requests
from fastapi import HTTPException
from google.auth import exceptions
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

from src.config import Config

_transport = threading.local()


def _google_request(url, method="GET", body=None, headers=None, **kwargs):
    """Respect Google's certificate cache headers and bound network waits.

    Each executor thread owns its Session, avoiding concurrent Session mutation.
    google-auth supplies a fixed Google certificates URL, not a URL from a JWT.
    Only public signing certificates are cached; never credentials or claims.
    """
    if not hasattr(_transport, "request"):
        _transport.request = GoogleRequest(session=cachecontrol.CacheControl(requests.Session()))
    return _transport.request(
        url, method=method, body=body, headers=headers,
        timeout=Config.GOOGLE_CERT_TIMEOUT_SECONDS,
    )


def verify_google_identity(token: str) -> str:
    """Return a namespaced Google subject only after cryptographic verification."""
    if not Config.GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google sign-in is not configured")
    try:
        claims = id_token.verify_oauth2_token(
            token, _google_request, audience=Config.GOOGLE_CLIENT_ID,
        )
        # The library validates signature, issuer, audience, iat, and exp.
        # Require a single intended web client, and reject a different presenter.
        if claims.get("aud") != Config.GOOGLE_CLIENT_ID:
            raise ValueError("Wrong audience")
        if claims.get("azp", Config.GOOGLE_CLIENT_ID) != Config.GOOGLE_CLIENT_ID:
            raise ValueError("Wrong authorized party")
        subject = claims.get("sub")
        if (not isinstance(subject, str) or not 1 <= len(subject) <= 255
                or any(ord(c) < 33 or ord(c) > 126 for c in subject)):
            raise ValueError("Invalid subject")
    except exceptions.TransportError:
        raise HTTPException(503, "Google sign-in is temporarily unavailable") from None
    except (ValueError, TypeError, KeyError, exceptions.GoogleAuthError):
        # Do not return/log tokens, decoded profiles, or verifier exception text.
        raise HTTPException(401, "Invalid or expired Google ID token",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    return "google:" + subject
