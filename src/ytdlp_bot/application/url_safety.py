"""Redirect-aware URL validation orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ytdlp_bot.domain.enums import FailureCode
from ytdlp_bot.domain.errors import ValidationError, failure
from ytdlp_bot.domain.jobs import sanitize_source_display
from ytdlp_bot.domain.network_policy import classify_addresses, parse_public_http_url
from ytdlp_bot.ports.network import DnsResolver, PreflightResult, UrlPreflightClient


@dataclass(frozen=True, slots=True)
class ValidatedUrl:
    normalized_url: str
    source_display: str
    addresses: tuple[str, ...]
    validated_at: datetime
    redirect_count: int = 0


class UrlSafetyService:
    def __init__(
        self,
        *,
        dns: DnsResolver,
        preflight: UrlPreflightClient,
        allowed_ports: frozenset[int],
        max_redirects: int = 10,
        blocked_cidrs: tuple[str, ...] = (),
    ) -> None:
        self._dns = dns
        self._preflight = preflight
        self._allowed_ports = allowed_ports
        self._max_redirects = max_redirects
        self._blocked_cidrs = blocked_cidrs

    async def validate(self, url: str, *, now: datetime) -> ValidatedUrl:
        parsed = parse_public_http_url(url, allowed_ports=self._allowed_ports)
        resolved = await self._dns.resolve(parsed.host)
        classify_addresses(list(resolved.addresses))
        result: PreflightResult = await self._preflight.preflight(parsed.normalized_url)
        if not result.allowed:
            raise ValidationError(
                failure(
                    FailureCode.BLOCKED_DESTINATION,
                    diagnostic=result.reason_code or "preflight denied",
                )
            )
        if result.redirect_count > self._max_redirects:
            raise ValidationError(
                failure(FailureCode.BLOCKED_DESTINATION, diagnostic="too many redirects")
            )
        final_url = result.final_url or parsed.normalized_url
        final_parsed = parse_public_http_url(final_url, allowed_ports=self._allowed_ports)
        final_resolved = await self._dns.resolve(final_parsed.host)
        classify_addresses(list(final_resolved.addresses))
        display = sanitize_source_display(final_parsed.scheme, final_parsed.host)
        return ValidatedUrl(
            normalized_url=final_parsed.normalized_url,
            source_display=display,
            addresses=final_resolved.addresses,
            validated_at=now,
            redirect_count=result.redirect_count,
        )
