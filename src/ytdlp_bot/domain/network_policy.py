"""Pure URL scheme/host/port and IP classification policy."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from ytdlp_bot.domain.enums import FailureCode
from ytdlp_bot.domain.errors import ValidationError, failure

_DEFAULT_PORTS = {80, 443}
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$"
)


@dataclass(frozen=True, slots=True)
class ParsedUrl:
    scheme: str
    host: str
    port: int
    normalized_url: str
    path: str


def parse_public_http_url(
    raw: str,
    *,
    allowed_ports: frozenset[int] | None = None,
) -> ParsedUrl:
    """Parse and canonicalize an absolute HTTP(S) URL for policy checks."""
    if not isinstance(raw, str) or not raw or len(raw) > 4096:
        raise ValidationError(failure(FailureCode.INVALID_URL, diagnostic="url length"))
    if any(ord(c) < 32 for c in raw):
        raise ValidationError(failure(FailureCode.INVALID_URL, diagnostic="controls"))
    parsed = urlparse(raw.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError(failure(FailureCode.INVALID_URL, diagnostic="scheme"))
    if parsed.username or parsed.password:
        raise ValidationError(failure(FailureCode.INVALID_URL, diagnostic="userinfo"))
    if not parsed.hostname:
        raise ValidationError(failure(FailureCode.INVALID_URL, diagnostic="host"))
    host = parsed.hostname
    # Reject zone IDs in hostname form.
    if "%" in host:
        raise ValidationError(failure(FailureCode.INVALID_URL, diagnostic="zone id"))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    ports = allowed_ports or frozenset(_DEFAULT_PORTS)
    if port not in ports:
        raise ValidationError(failure(FailureCode.BLOCKED_DESTINATION, diagnostic="port"))

    # Canonical host: lowercase DNS or normalize IP.
    try:
        ip = ipaddress.ip_address(host)
        host_out = ip.compressed
        if isinstance(ip, ipaddress.IPv6Address):
            host_out = f"[{host_out}]"
    except ValueError:
        try:
            host_out = host.encode("idna").decode("ascii").lower().rstrip(".")
        except Exception as exc:
            raise ValidationError(
                failure(FailureCode.INVALID_URL, diagnostic="idna")
            ) from exc
        if not _HOSTNAME_RE.fullmatch(host_out):
            raise ValidationError(
                failure(FailureCode.INVALID_URL, diagnostic="hostname")
            ) from None

    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""
    # Fragments are ignored for destination identity (not used as data).
    netloc = host_out if parsed.port is None else f"{host_out}:{port}"
    normalized = f"{parsed.scheme}://{netloc}{path}{query}"
    return ParsedUrl(
        scheme=parsed.scheme,
        host=host_out.strip("[]"),
        port=port,
        normalized_url=normalized,
        path=path,
    )


def is_blocked_ip(address: str) -> bool:
    """Return True if the IP must not be contacted (fail-closed classes)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    if ip.is_loopback or ip.is_unspecified or ip.is_link_local:
        return True
    if ip.is_private or ip.is_multicast or ip.is_reserved:
        return True
    # Documentation / benchmark / CGNAT.
    if isinstance(ip, ipaddress.IPv4Address):
        if ip in ipaddress.ip_network("100.64.0.0/10"):  # CGNAT
            return True
        if ip in ipaddress.ip_network("192.0.2.0/24"):
            return True
        if ip in ipaddress.ip_network("198.51.100.0/24"):
            return True
        if ip in ipaddress.ip_network("203.0.113.0/24"):
            return True
        if ip in ipaddress.ip_network("198.18.0.0/15"):  # benchmarking
            return True
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_blocked_ip(str(ip.ipv4_mapped))
        if ip in ipaddress.ip_network("2001:db8::/32"):
            return True
    return False


def classify_addresses(addresses: list[str]) -> None:
    """Raise if any resolved address is blocked."""
    if not addresses:
        raise ValidationError(failure(FailureCode.BLOCKED_DESTINATION, diagnostic="no addresses"))
    for addr in addresses:
        if is_blocked_ip(addr):
            raise ValidationError(
                failure(FailureCode.BLOCKED_DESTINATION, diagnostic="blocked address class")
            )
