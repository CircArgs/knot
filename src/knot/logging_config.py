"""Structured logging configuration for knot.

Environment knobs
-----------------
``KNOT_LOG_FORMAT``
    ``"json"`` — one-line JSON per record (production default).
    ``"text"`` — human-readable colourless text (dev default).
    Defaults to ``"text"`` when ``KNOT_DEV_MODE=1``, otherwise ``"json"``.

``KNOT_LOG_LEVEL``
    Standard Python level name: ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``,
    ``CRITICAL``.  Defaults to ``"INFO"``.

JSON record fields
------------------
Every JSON log line contains:
    timestamp   ISO-8601 UTC (e.g. ``"2026-05-07T14:32:01.123456Z"``)
    level       ``"INFO"`` etc.
    logger      dotted logger name
    message     the formatted message
    ...         any extra keyword args passed to the log call

Uvicorn integration
-------------------
Uvicorn ships its own logging config and will REPLACE the root handler
unless you disable it.  When starting via uvicorn, use:

    uvicorn knot.api.main:app \\
        --log-config /dev/null \\
        --no-access-log

Then knot's own ``configure_logging()`` (called inside the lifespan) owns
all output.  Alternatively, pass ``--log-level`` to uvicorn to silence its
access log and let knot emit its own access lines via the middleware.

The simplest production invocation:

    KNOT_LOG_FORMAT=json uvicorn knot.api.main:app \\
        --no-access-log --log-config /dev/null
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from knot.config import get_settings


# ── JSON formatter ────────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record on a single line."""

    # Fields that are part of every LogRecord but clutter JSON output.
    _SKIP = frozenset({
        "args", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "message", "module", "msecs",
        "msg", "name", "pathname", "process", "processName", "relativeCreated",
        "stack_info", "taskName", "thread", "threadName",
    })

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        if record.exc_info:
            record.exc_text = self.formatException(record.exc_info)

        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        ) + "Z"

        obj: dict = {
            "timestamp": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
        }

        # Attach extra fields (anything not in the standard LogRecord attrs).
        for key, val in record.__dict__.items():
            if key not in self._SKIP and not key.startswith("_"):
                obj[key] = val

        if record.exc_text:
            obj["exc"] = record.exc_text

        return json.dumps(obj, default=str)


# ── Text formatter ─────────────────────────────────────────────────────────────

_TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
_TEXT_DATEFMT = "%Y-%m-%dT%H:%M:%S"


# ── Public entry point ─────────────────────────────────────────────────────────

def configure_logging() -> None:
    """Apply knot's logging configuration to the root logger.

    Call once at application startup (inside the FastAPI lifespan).
    Safe to call multiple times — subsequent calls are no-ops once the
    handler is already attached (guards against double-init in tests).
    """
    root = logging.getLogger()

    # Idempotency guard — don't add a second handler on re-import.
    if any(isinstance(h, logging.StreamHandler) and getattr(h, "_knot", False)
           for h in root.handlers):
        return

    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    fmt_name = settings.effective_log_format

    if fmt_name == "json":
        formatter: logging.Formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(_TEXT_FORMAT, datefmt=_TEXT_DATEFMT)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler._knot = True  # type: ignore[attr-defined]

    # Remove any existing handlers that uvicorn or pytest may have added.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet noisy third-party loggers at INFO+ unless DEBUG requested.
    if level > logging.DEBUG:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
