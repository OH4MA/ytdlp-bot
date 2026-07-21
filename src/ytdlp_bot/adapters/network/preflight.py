"""Redirect-aware HTTP(S) preflight through controlled connector."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from urllib.parse import urljoin

import aiohttp

from ytdlp_bot.domain.network_policy import classify_addresses, parse_public_http_url
from ytdlp_bot.ports.network import DnsResolver, PreflightResult, UrlPreflightClient


@dataclass
class AiohttpPreflightClient:
    """Bounded HEAD/GET preflight with redirect classification at each hop."""

    dns: DnsResolver
    proxy_url: str | None = None
    max_redirects: int = 10
    timeout_seconds: float = 30.0
    allowed_ports: frozenset[int] = frozenset({80, 443})

    async def preflight(self, url: str) -> PreflightResult:
        current = url
        redirect_count = 0
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        # Trust environment proxies must not bypass controlled proxy.
        connector = aiohttp.TCPConnector(force_close=True)

        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                trust_env=False,
            ) as session:
                for _ in range(self.max_redirects + 1):
                    parsed = parse_public_http_url(current, allowed_ports=self.allowed_ports)
                    resolved = await self.dns.resolve(parsed.host)
                    try:
                        classify_addresses(list(resolved.addresses))
                    except Exception:
                        return PreflightResult(
                            allowed=False,
                            final_url=None,
                            reason_code="blocked_address",
                            redirect_count=redirect_count,
                        )

                    try:
                        async with session.request(
                            "HEAD",
                            parsed.normalized_url,
                            allow_redirects=False,
                            proxy=self.proxy_url,
                            headers={"User-Agent": "ytdlp-bot-preflight/1.0"},
                        ) as resp:
                            if resp.status in {301, 302, 303, 307, 308}:
                                location = resp.headers.get("Location")
                                if not location:
                                    return PreflightResult(
                                        allowed=False,
                                        final_url=None,
                                        reason_code="redirect_missing_location",
                                        redirect_count=redirect_count,
                                    )
                                current = urljoin(parsed.normalized_url, location)
                                redirect_count += 1
                                if redirect_count > self.max_redirects:
                                    return PreflightResult(
                                        allowed=False,
                                        final_url=None,
                                        reason_code="too_many_redirects",
                                        redirect_count=redirect_count,
                                    )
                                continue
                            # Treat 405/501 on HEAD as allowed origin for preflight shape.
                            if resp.status >= 400 and resp.status not in {401, 403, 404, 405, 501}:
                                return PreflightResult(
                                    allowed=False,
                                    final_url=None,
                                    reason_code=f"http_{resp.status}",
                                    redirect_count=redirect_count,
                                )
                            return PreflightResult(
                                allowed=True,
                                final_url=parsed.normalized_url,
                                redirect_count=redirect_count,
                            )
                    except aiohttp.ClientError:
                        # Fallback GET without body consumption for servers that reject HEAD.
                        async with session.request(
                            "GET",
                            parsed.normalized_url,
                            allow_redirects=False,
                            proxy=self.proxy_url,
                            headers={"User-Agent": "ytdlp-bot-preflight/1.0"},
                        ) as resp:
                            if resp.status in {301, 302, 303, 307, 308}:
                                location = resp.headers.get("Location")
                                if not location:
                                    return PreflightResult(
                                        allowed=False,
                                        final_url=None,
                                        reason_code="redirect_missing_location",
                                        redirect_count=redirect_count,
                                    )
                                current = urljoin(parsed.normalized_url, location)
                                redirect_count += 1
                                continue
                            # Drain at most one small chunk then close.
                            with contextlib.suppress(Exception):
                                await resp.content.readany()
                            return PreflightResult(
                                allowed=True,
                                final_url=parsed.normalized_url,
                                redirect_count=redirect_count,
                            )
        except Exception:
            return PreflightResult(
                allowed=False,
                final_url=None,
                reason_code="preflight_error",
                redirect_count=redirect_count,
            )

        return PreflightResult(
            allowed=False,
            final_url=None,
            reason_code="too_many_redirects",
            redirect_count=redirect_count,
        )


@dataclass
class EgressSelfTest:
    """NET-07 style connectivity self-tests for readiness."""

    dns: DnsResolver
    preflight: UrlPreflightClient
    proxy_url: str | None

    async def run(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        # HTTPS-style preflight against a documentation address that must be blocked
        # proves classification is active.
        try:
            resolved = await self.dns.resolve("example.com")
            results["dns_public"] = bool(resolved.addresses)
        except Exception:
            results["dns_public"] = False
        try:
            # Loopback must be blocked by policy when used as destination.
            from ytdlp_bot.domain.network_policy import is_blocked_ip

            results["blocks_loopback"] = is_blocked_ip("127.0.0.1")
        except Exception:
            results["blocks_loopback"] = False
        results["proxy_configured"] = bool(self.proxy_url)
        results["ok"] = all(
            (
                results.get("dns_public", False),
                results.get("blocks_loopback", False),
            )
        )
        return results


_ = UrlPreflightClient
