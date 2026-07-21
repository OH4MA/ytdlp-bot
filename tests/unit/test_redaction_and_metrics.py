"""OBS-01..04: redaction, diagnostics, logging, metrics."""

from __future__ import annotations

import io
import json
import logging

import pytest

from ytdlp_bot.adapters.security.logging_config import configure_logging
from ytdlp_bot.adapters.security.metrics import InMemoryMetricsSink, MetricsError
from ytdlp_bot.adapters.security.redaction import (
    REDACTED,
    contains_canary,
    redact_value,
    sanitize_exception,
    sanitize_host,
)

CANARY_TOKEN = "BOTTOKEN_CANARY_abc123xyz"
CANARY_URL = "https://user:pass@evil.example/path?sig=secret#frag"
CANARY_BEARER = "Bearer SECRETBEARERVALUE99"


@pytest.mark.unit
def test_redact_nested_structures() -> None:
    payload = {
        "event": "job.failed",
        "token": CANARY_TOKEN,
        "nested": {
            "authorization": CANARY_BEARER,
            "source_url": CANARY_URL,
            "count": 3,
            "items": [CANARY_URL, {"cookie": "a=b"}],
        },
        "source_display": "https://cdn.example/video?x=1",
    }
    redacted = redact_value(payload)
    blob = json.dumps(redacted)
    assert CANARY_TOKEN not in blob
    assert "SECRETBEARER" not in blob
    assert "user:pass" not in blob
    assert "/path" not in blob or REDACTED in blob
    assert redacted["token"] == REDACTED
    assert redacted["source_display"] == "https://cdn.example"
    assert redacted["nested"]["count"] == 3


@pytest.mark.unit
def test_sanitize_host_and_exception() -> None:
    assert sanitize_host(CANARY_URL) == "https://evil.example"
    exc = ValueError(f"download failed for {CANARY_URL} token={CANARY_TOKEN}")
    text = sanitize_exception(exc)
    assert CANARY_TOKEN not in text
    assert "user:pass" not in text


@pytest.mark.unit
def test_json_logging_redacts(caplog: pytest.LogCaptureFixture) -> None:
    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)
    logger = logging.getLogger("ytdlp_bot.test")
    logger.info(
        "failed %s",
        CANARY_URL,
        extra={"event": "job.failed", "token": CANARY_TOKEN, "host": "https://h.example/x"},
    )
    # token extra is not approved so dropped; message URL redacted.
    line = stream.getvalue().strip().splitlines()[-1]
    data = json.loads(line)
    blob = json.dumps(data)
    assert contains_canary(blob, [CANARY_TOKEN, "user:pass", "SECRETBEARER"]) == []
    assert data["event"] == "job.failed"
    assert data["host"] == "https://h.example"


@pytest.mark.unit
def test_noisy_third_party_loggers_capped_at_warning() -> None:
    stream = io.StringIO()
    configure_logging(level="DEBUG", stream=stream)
    for name in ("asyncio", "aiosqlite", "aiogram", "aiogram.event"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
    # App loggers still follow root DEBUG.
    app = logging.getLogger("ytdlp_bot.test.quiet")
    app.debug("app debug line")
    assert "app debug line" in stream.getvalue()


@pytest.mark.unit
def test_metrics_label_allowlist() -> None:
    m = InMemoryMetricsSink()
    m.incr("jobs_total", tags={"platform": "telegram", "outcome": "completed"})
    m.gauge("queue_depth", 3.0, tags={"component": "dispatcher"})
    snap = m.snapshot()
    assert snap["counters"]
    with pytest.raises(MetricsError):
        m.incr("jobs_total", tags={"job_id": "abc"})
    with pytest.raises(MetricsError):
        m.incr("jobs_total", tags={"host": "evil.example"})
