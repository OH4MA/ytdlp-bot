"""Structured JSON logging configuration."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from ytdlp_bot.adapters.security.redaction import redact_value, sanitize_exception

# Approved top-level log record fields after redaction.
_APPROVED_EXTRA = frozenset(
    {
        "event",
        "job_id",
        "artifact_id",
        "request_id",
        "correlation_id",
        "platform",
        "state",
        "from_state",
        "to_state",
        "byte_size",
        "duration_ms",
        "retry_count",
        "host",
        "source_display",
        "error_code",
        "component",
        "worker_phase",
        "attempt",
        "outcome",
        "command",
        "kind",
        "worker_exit_code",
        "reason",
    }
)


class JsonFormatter(logging.Formatter):
    """Emit one redacted JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_value(record.getMessage()),
        }
        if record.exc_info and record.exc_info[1] is not None:
            payload["exception"] = sanitize_exception(record.exc_info[1])
        for key in _APPROVED_EXTRA:
            if hasattr(record, key):
                payload[key] = redact_value(getattr(record, key), field=key)
        # Drop any other arbitrary attributes that might leak secrets.
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# Third-party loggers that drown useful diagnostics when root is DEBUG.
_QUIET_LOGGERS: tuple[str, ...] = (
    "asyncio",
    "aiosqlite",
    "aiogram",
)


def configure_logging(*, level: str = "INFO", stream: Any | None = None) -> None:
    """Configure root logger with JSON formatter (idempotent enough for tests)."""
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Keep app DEBUG usable without SQL/update-handling spam.
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
