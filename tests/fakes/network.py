"""Fake DNS and preflight clients."""

from __future__ import annotations

from dataclasses import dataclass, field

from ytdlp_bot.ports.network import PreflightResult, ResolvedAddress


@dataclass
class FakeDnsResolver:
    records: dict[str, tuple[str, ...]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.calls.clear()

    def map(self, host: str, *addresses: str) -> None:
        self.records[host.lower()] = addresses

    async def resolve(self, host: str) -> ResolvedAddress:
        self.calls.append(host)
        addrs = self.records.get(host.lower(), ("203.0.113.10",))
        return ResolvedAddress(host=host, addresses=addrs)


@dataclass
class FakeUrlPreflightClient:
    allowed: bool = True
    reason_code: str | None = None
    calls: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.calls.clear()
        self.allowed = True
        self.reason_code = None

    async def preflight(self, url: str) -> PreflightResult:
        self.calls.append(url)
        if not self.allowed:
            return PreflightResult(
                allowed=False, final_url=None, reason_code=self.reason_code or "blocked"
            )
        return PreflightResult(allowed=True, final_url=url, redirect_count=0)
