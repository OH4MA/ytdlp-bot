"""Recursive structured-field redaction for logs and diagnostics.

Complete bearer URLs, secrets, path/query/userinfo components, and raw
external exception text must never reach sinks after redaction.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

REDACTED = "[REDACTED]"
_MAX_STRING = 2048
_MAX_DEPTH = 12
_MAX_LIST = 64
_MAX_KEYS = 64

# Keys whose entire value is always redacted.
_SECRET_KEY_RE = re.compile(
    r"(token|secret|password|passwd|authorization|cookie|set-cookie|"
    r"api[_-]?key|bearer|signing|proxy[_-]?auth|credential)",
    re.IGNORECASE,
)

# Keys allowed to keep a hostname-only sanitized view.
_SAFE_HOST_KEYS = frozenset(
    {
        "host",
        "source_display",
        "public_base_url_host",
        "sanitized_host",
        "hostname",
    }
)

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_BEARER_RE = re.compile(r"bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_TOKEN_ASSIGN_RE = re.compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|signing[_-]?secret)\s*[:=]\s*\S+"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_host(url_or_host: str) -> str:
    """Return scheme+host or bare host; strip path/query/userinfo."""
    text = url_or_host.strip()
    if "://" not in text:
        # Bare host.
        host = text.split("/")[0].split("?")[0]
        return host[:253]
    parsed = urlparse(text)
    if not parsed.hostname:
        return REDACTED
    scheme = parsed.scheme or "https"
    host = parsed.hostname
    if parsed.port:
        return f"{scheme}://{host}:{parsed.port}"
    return f"{scheme}://{host}"


def redact_url(url: str) -> str:
    """Reduce a URL to scheme+host or redacted marker."""
    try:
        return sanitize_host(url)
    except Exception:
        return REDACTED


def redact_string(value: str, *, field: str | None = None) -> str:
    """Sanitize a single string field."""
    if field and _SECRET_KEY_RE.search(field):
        return REDACTED
    if field and field in _SAFE_HOST_KEYS:
        return sanitize_host(value)

    text = _CONTROL_RE.sub("", value)
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = _TOKEN_ASSIGN_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    text = _URL_RE.sub(lambda m: redact_url(m.group(0)), text)
    if len(text) > _MAX_STRING:
        text = text[:_MAX_STRING] + "…"
    return text


def redact_value(value: Any, *, field: str | None = None, depth: int = 0) -> Any:
    """Recursively redact mappings, sequences, and scalars."""
    if depth > _MAX_DEPTH:
        return REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_string(value, field=field)
    if isinstance(value, bytes):
        return REDACTED
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_KEYS:
                out["…"] = REDACTED
                break
            key_s = str(key)
            if _SECRET_KEY_RE.search(key_s):
                out[key_s] = REDACTED
            else:
                out[key_s] = redact_value(item, field=key_s, depth=depth + 1)
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)[:_MAX_LIST]
        return [redact_value(item, field=field, depth=depth + 1) for item in items]
    # Unknown objects: never serialize repr which may contain secrets.
    return REDACTED


def sanitize_exception(exc: BaseException, *, max_len: int = 512) -> str:
    """English diagnostic string for logs (bounded, redacted)."""
    parts: list[str] = []
    current: BaseException | None = exc
    seen = 0
    while current is not None and seen < 5:
        name = type(current).__name__
        msg = redact_string(str(current) if str(current) else name)
        parts.append(f"{name}: {msg}")
        current = current.__cause__ or current.__context__
        seen += 1
    text = " | ".join(parts)
    if len(text) > max_len:
        return text[:max_len] + "…"
    return text


def contains_canary(blob: str, canaries: Sequence[str]) -> list[str]:
    """Return which canary strings appear in blob (test helper)."""
    return [c for c in canaries if c in blob]
