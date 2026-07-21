"""DNS and network preflight ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ResolvedAddress:
    host: str
    addresses: tuple[str, ...]
    ttl_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class PreflightResult:
    allowed: bool
    final_url: str | None
    reason_code: str | None = None
    redirect_count: int = 0


class DnsResolver(Protocol):
    async def resolve(self, host: str) -> ResolvedAddress: ...


class UrlPreflightClient(Protocol):
    async def preflight(self, url: str) -> PreflightResult: ...
