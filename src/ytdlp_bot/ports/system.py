"""System ports: clock, IDs, tokens, leases, metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from ytdlp_bot.domain.enums import LeaseKind
from ytdlp_bot.domain.identity import ArtifactId, JobId


class Clock(Protocol):
    def now(self) -> datetime:
        """UTC wall clock."""
        ...

    def monotonic(self) -> float:
        """Monotonic seconds."""
        ...


class IdGenerator(Protocol):
    def job_id(self) -> str: ...

    def artifact_id(self) -> str: ...

    def confirmation_id(self) -> str: ...

    def link_nonce(self) -> str: ...

    def storage_key(self) -> str: ...

    def correlation_id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Canonical signed download claims (no complete URL)."""

    artifact_id: str
    token_version: int
    exp: int
    nonce: str
    job_id: str | None = None


@dataclass(frozen=True, slots=True)
class SignedToken:
    """Token material without composing the public base URL."""

    claims: TokenClaims
    signature: str
    query_string: str


class TokenSigner(Protocol):
    def sign(self, claims: TokenClaims) -> SignedToken: ...

    def verify(self, query_params: dict[str, str]) -> TokenClaims | None: ...


class ArtifactLeaseRegistry(Protocol):
    async def acquire(
        self, artifact_id: ArtifactId, kind: LeaseKind, *, holder_id: str
    ) -> bool: ...

    async def release(
        self, artifact_id: ArtifactId, kind: LeaseKind, *, holder_id: str
    ) -> None: ...

    async def holder_count(self, artifact_id: ArtifactId) -> int: ...

    async def has_active_leases(self, artifact_id: ArtifactId) -> bool: ...


class MetricsSink(Protocol):
    def incr(self, name: str, *, tags: dict[str, str] | None = None, value: int = 1) -> None: ...

    def gauge(self, name: str, value: float, *, tags: dict[str, str] | None = None) -> None: ...

    def timing(
        self, name: str, duration: timedelta, *, tags: dict[str, str] | None = None
    ) -> None: ...


class ArtifactAccessCoordinator(Protocol):
    """Combines artifact row state with lease acquisition."""

    async def try_begin_stream(self, artifact_id: ArtifactId, *, holder_id: str) -> bool: ...

    async def end_stream(self, artifact_id: ArtifactId, *, holder_id: str) -> None: ...

    async def try_begin_upload(self, artifact_id: ArtifactId, *, holder_id: str) -> bool: ...

    async def end_upload(self, artifact_id: ArtifactId, *, holder_id: str) -> None: ...

    async def invalidate(self, artifact_id: ArtifactId) -> None: ...


# Silence unused import for JobId in type surface documentation.
_ = JobId
