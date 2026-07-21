"""Deterministic clock and ID generators for tests."""

from __future__ import annotations

import base64
import secrets
from datetime import UTC, datetime, timedelta


class FakeClock:
    """Controllable UTC clock and monotonic time."""

    def __init__(self, start: datetime | None = None) -> None:
        base = start or datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        if base.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        self._now = base.astimezone(UTC)
        self._monotonic = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, delta: timedelta | float) -> datetime:
        if isinstance(delta, (int, float)):
            delta = timedelta(seconds=float(delta))
        self._now = self._now + delta
        self._monotonic += delta.total_seconds()
        return self._now

    def set(self, when: datetime) -> None:
        if when.tzinfo is None:
            raise ValueError("when must be timezone-aware")
        self._now = when.astimezone(UTC)

    def reset(self, start: datetime | None = None) -> None:
        base = start or datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        self._now = base.astimezone(UTC)
        self._monotonic = 0.0


class DeterministicIdGenerator:
    """Counter-based opaque IDs (still valid length/charset)."""

    def __init__(self, seed: int = 1) -> None:
        self._counter = seed

    def _next_token(self, prefix: bytes) -> str:
        self._counter += 1
        raw = prefix + self._counter.to_bytes(16, "big")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")[:32].ljust(22, "A")

    def job_id(self) -> str:
        return self._next_token(b"J")

    def artifact_id(self) -> str:
        return self._next_token(b"A")

    def confirmation_id(self) -> str:
        return self._next_token(b"C")

    def link_nonce(self) -> str:
        return self._next_token(b"N")

    def storage_key(self) -> str:
        return self._next_token(b"S")

    def correlation_id(self) -> str:
        return self._next_token(b"R")

    def reset(self, seed: int = 1) -> None:
        self._counter = seed


class RandomIdGenerator:
    """Production-like random IDs for integration tests."""

    def _token(self, nbytes: int = 16) -> str:
        return base64.urlsafe_b64encode(secrets.token_bytes(nbytes)).decode("ascii").rstrip("=")

    def job_id(self) -> str:
        return self._token()

    def artifact_id(self) -> str:
        return self._token()

    def confirmation_id(self) -> str:
        return self._token()

    def link_nonce(self) -> str:
        return self._token()

    def storage_key(self) -> str:
        return self._token(18)

    def correlation_id(self) -> str:
        return self._token()
