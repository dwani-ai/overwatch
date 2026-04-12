from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("overwatch.http")


class RequestLogMiddleware(BaseHTTPMiddleware):
    """Assign ``X-Request-Id``, log method/path/status/latency."""

    async def dispatch(self, request: Request, call_next) -> Response:
        rid = str(uuid.uuid4())[:12]
        request.state.request_id = rid
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            dt_ms = (time.perf_counter() - t0) * 1000
            logger.exception(
                "request_id=%s %s %s failed after %.1fms",
                rid,
                request.method,
                request.url.path,
                dt_ms,
            )
            raise
        dt_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "request_id=%s %s %s -> %s %.1fms",
            rid,
            request.method,
            request.url.path,
            response.status_code,
            dt_ms,
        )
        response.headers["X-Request-Id"] = rid
        return response
