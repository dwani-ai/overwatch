from __future__ import annotations

import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class SlidingWindowRateLimiter:
    """In-memory per-key limit over a 60s window (best-effort; resets on process restart)."""

    def __init__(self, max_per_minute: int) -> None:
        self._max = max_per_minute
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = 60.0
        q = self._hits.setdefault(key, [])
        q[:] = [t for t in q if now - t < window]
        if len(q) >= self._max:
            return False
        q.append(now)
        return True


class ApiRateLimitMiddleware(BaseHTTPMiddleware):
    """
    Uses ``app.state._api_rate_limiter`` (a ``SlidingWindowRateLimiter``) when set at startup.
    If unset or limit is disabled, passes through.
    """

    def __init__(self, app, *, client_key: Callable[[Request], str]) -> None:
        super().__init__(app)
        self._client_key = client_key

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if request.method == "GET" and path in ("/v1/health", "/health", "/"):
            return await call_next(request)
        if path.startswith("/docs") or path.startswith("/redoc") or path == "/openapi.json":
            return await call_next(request)

        limiter = getattr(request.app.state, "_api_rate_limiter", None)
        if limiter is None:
            return await call_next(request)

        key = self._client_key(request)
        if not limiter.allow(key):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Try again in a minute.",
                    "error_code": "rate_limited",
                },
            )
        return await call_next(request)


def client_ip_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"
