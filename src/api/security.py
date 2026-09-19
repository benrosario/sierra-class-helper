"""Bot service authentication, Google account verification, and request bounds."""
import asyncio
import secrets

from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from src.config import Config
from src.api.google_identity import verify_google_identity


# Cheap admission before signature checks/certificate fetches. These are burst
# protections per worker; the paid-message caps are persistent in SQLite.
_auth_storage = MemoryStorage()
_auth_limiter = FixedWindowRateLimiter(_auth_storage)
_auth_ip_limit = parse(Config.DEMO_AUTH_IP_RATE)
_auth_global_limit = parse(Config.DEMO_AUTH_GLOBAL_RATE)


def _require_token(request: Request, token: str) -> None:
    if len(token) < 32:
        raise HTTPException(503, "Endpoint not configured")
    scheme, _, supplied = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        supplied.encode("utf-8"), token.encode("utf-8")
    ):
        raise HTTPException(401, "Invalid API credentials", headers={"WWW-Authenticate": "Bearer"})


async def require_bot(request: Request) -> None:
    _require_token(request, Config.BOT_API_TOKEN)
    # Only authenticated bot traffic may select the Discord rate-limit key.
    user = request.headers.get("X-Discord-User", "")
    if user and (not user.isascii() or not user.isdigit() or len(user) > 20):
        raise HTTPException(400, "Invalid Discord user ID")
    peer = request.client.host if request.client else "unknown"
    request.state.rate_limit_key = "discord:" + (user or peer)


async def require_demo(request: Request) -> str:
    if not Config.GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google sign-in is not configured")
    peer = request.client.host if request.client else "unknown"
    if (not _auth_limiter.hit(_auth_ip_limit, "google-auth-ip", peer)
            or not _auth_limiter.hit(_auth_global_limit, "google-auth-global")):
        raise HTTPException(429, detail={
            "code": "demo_auth_rate_limited", "message": "Too many requests. Try again shortly.",
        }, headers={"Retry-After": "60"})
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if (scheme.lower() != "bearer" or not token or not token.isascii()
            or len(token) > Config.GOOGLE_TOKEN_MAX_BYTES or any(c.isspace() for c in token)):
        raise HTTPException(401, "A Google ID token is required", headers={"WWW-Authenticate": "Bearer"})
    # google-auth signature verification and certificate I/O must not block the
    # event loop. Identity always comes from signed claims, never request headers.
    return await asyncio.to_thread(verify_google_identity, token)


class RequestSizeLimitMiddleware:
    """Bound bytes before JSON parsing, including chunked bodies with no length."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > Config.MAX_REQUEST_BYTES:
                return await JSONResponse(
                    {"detail": "Request body too large"}, status_code=413,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
