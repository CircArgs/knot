"""Request-ID injection + access-log middleware for knot.

Every HTTP request gets a UUID request ID:
  - Taken from the incoming ``X-Request-ID`` header if present.
  - Generated as UUIDv4 otherwise.
  - Echoed back on the response in ``X-Request-ID``.
  - Stored in a ``contextvars.ContextVar`` so any logger called during the
    request can include it by using ``_request_id_var.get()``.

Access log
----------
One line is emitted per request (after the response is sent) containing:

    timestamp, request_id, method, path, status, duration_ms, principal, client_ip

Skipped paths (to cut noise):
    /docs, /openapi.json, /auth/me

Format follows ``KNOT_LOG_FORMAT``:
  - ``json`` — structured JSON object.
  - ``text`` — space-delimited human-readable line.

Usage
-----
Mount in ``api/main.py``::

    from knot.middleware import RequestIDMiddleware
    app.add_middleware(RequestIDMiddleware)
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextvars import ContextVar
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# ContextVar so any logger in the call-stack can read the current request ID.
_request_id_var: ContextVar[str] = ContextVar("knot_request_id", default="-")

_SKIP_PATHS = frozenset({"/docs", "/openapi.json", "/auth/me"})

_access_log = logging.getLogger("knot.access")


def get_request_id() -> str:
    """Return the request ID for the current async context, or ``"-"``."""
    return _request_id_var.get()


def _is_json() -> bool:
    fmt = os.environ.get("KNOT_LOG_FORMAT", "")
    if fmt:
        return fmt.lower() == "json"
    return os.environ.get("KNOT_DEV_MODE") != "1"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that attaches a request ID and emits an access log."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._json = _is_json()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 1. Determine request ID.
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = _request_id_var.set(req_id)

        # 2. Time the request.
        t0 = time.perf_counter()
        response: Response = await call_next(request)
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)

        # 3. Echo request ID back to caller.
        response.headers["X-Request-ID"] = req_id

        # 4. Emit access log (unless path is in the skip list).
        if request.url.path not in _SKIP_PATHS:
            # Principal is set by the auth dependency and stored in request.state
            # (populated by the optional helper below); fall back to "-".
            principal = getattr(request.state, "principal", "-")
            client_ip = (
                request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or (request.client.host if request.client else "-")
            )

            if self._json:
                _access_log.info(
                    "access",
                    extra={
                        "request_id": req_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "duration_ms": duration_ms,
                        "principal": principal,
                        "client_ip": client_ip,
                    },
                )
            else:
                _access_log.info(
                    "%s %s %s %s %sms %s %s",
                    req_id,
                    request.method,
                    request.url.path,
                    response.status_code,
                    duration_ms,
                    principal,
                    client_ip,
                )

        _request_id_var.reset(token)
        return response
