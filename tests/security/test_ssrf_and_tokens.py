"""Security suite: SSRF classification and token tampering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ytdlp_bot.adapters.security.signed_tokens import HmacTokenSigner, issue_download_link
from ytdlp_bot.domain.errors import ValidationError
from ytdlp_bot.domain.network_policy import is_blocked_ip, parse_public_http_url


@pytest.mark.security
@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "0.0.0.0",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.10.1",
        "100.64.0.1",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "2001:db8::1",
    ],
)
def test_blocked_address_classes(ip: str) -> None:
    assert is_blocked_ip(ip) is True


@pytest.mark.security
def test_reject_userinfo_and_bad_scheme() -> None:
    with pytest.raises(ValidationError):
        parse_public_http_url("https://user:secret@evil.example/")
    with pytest.raises(ValidationError):
        parse_public_http_url("file:///etc/passwd")


@pytest.mark.security
def test_token_field_tamper_generic() -> None:
    signer = HmacTokenSigner(b"S" * 32, public_base_url="https://dl.example.invalid")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    link = issue_download_link(
        signer,
        artifact_id="A" * 22,
        display_name="x.mp4",
        token_version=1,
        now=now,
        link_lifetime_seconds=600,
        artifact_expires_at=now + timedelta(hours=1),
    )
    from urllib.parse import parse_qs, urlparse

    qs = {k: v[0] for k, v in parse_qs(urlparse(link.url).query).items()}
    qs["exp"] = str(int(qs["exp"]) + 1)
    assert signer.verify({**qs, "_artifact_id": "A" * 22, "_display_name": "x.mp4"}) is None
