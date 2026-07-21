"""Controlled DNS resolver adapter."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass

from ytdlp_bot.ports.network import DnsResolver, ResolvedAddress


@dataclass
class SystemDnsResolver:
    """Resolve A/AAAA via getaddrinfo on the controller host policy path."""

    timeout_seconds: float = 5.0

    async def resolve(self, host: str) -> ResolvedAddress:
        loop = asyncio.get_running_loop()

        def _lookup() -> tuple[str, ...]:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            addrs: list[str] = []
            for info in infos:
                sockaddr = info[4]
                if not sockaddr:
                    continue
                ip = sockaddr[0]
                if ip not in addrs:
                    addrs.append(ip)
            return tuple(addrs)

        try:
            addresses = await asyncio.wait_for(
                loop.run_in_executor(None, _lookup),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            raise OSError("dns timeout for host") from exc
        except socket.gaierror as exc:
            raise OSError("dns resolution failed") from exc
        return ResolvedAddress(host=host, addresses=addresses, ttl_seconds=None)


@dataclass
class StaticDnsResolver:
    """Test/deterministic resolver mapping."""

    records: dict[str, tuple[str, ...]]

    async def resolve(self, host: str) -> ResolvedAddress:
        key = host.lower()
        if key not in self.records:
            raise OSError("dns resolution failed")
        return ResolvedAddress(host=host, addresses=self.records[key])


# Structural conformance helper for type checkers.
_ = DnsResolver
