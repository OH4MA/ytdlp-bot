"""AUTH, NET policy, format policy unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.fakes.network import FakeDnsResolver, FakeUrlPreflightClient
from tests.fakes.repositories import (
    InMemoryAccessRepository,
    InMemoryArtifactRepository,
    InMemoryJobRepository,
)
from ytdlp_bot.application.authorization import AuthorizationService
from ytdlp_bot.application.url_safety import UrlSafetyService
from ytdlp_bot.domain.enums import AccessMode, MediaMode, Platform, VideoQuality
from ytdlp_bot.domain.errors import AuthorizationError, NotFoundError, ValidationError
from ytdlp_bot.domain.format_policy import build_format_selection, height_within_ceiling
from ytdlp_bot.domain.identity import Identity, JobId
from ytdlp_bot.domain.network_policy import is_blocked_ip, parse_public_http_url


@pytest.mark.unit
def test_parse_url_and_blocked_ips() -> None:
    p = parse_public_http_url("https://Example.COM/path")
    assert p.host == "example.com"
    assert p.scheme == "https"
    with pytest.raises(ValidationError):
        parse_public_http_url("https://user:pass@example.com/")
    with pytest.raises(ValidationError):
        parse_public_http_url("ftp://example.com/")
    assert is_blocked_ip("127.0.0.1")
    assert is_blocked_ip("10.0.0.1")
    assert is_blocked_ip("192.168.1.1")
    assert is_blocked_ip("169.254.1.1")
    assert is_blocked_ip("100.64.1.1")
    assert is_blocked_ip("203.0.113.10")  # documentation
    assert is_blocked_ip("::1")
    assert not is_blocked_ip("8.8.8.8")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_url_safety_public_host() -> None:
    dns = FakeDnsResolver()
    dns.map("cdn.example", "8.8.8.8")
    pre = FakeUrlPreflightClient()
    svc = UrlSafetyService(dns=dns, preflight=pre, allowed_ports=frozenset({80, 443}))
    result = await svc.validate("https://cdn.example/v", now=datetime.now(UTC))
    assert result.source_display == "https://cdn.example"
    dns.map("cdn.example", "10.0.0.2")
    with pytest.raises(ValidationError):
        await svc.validate("https://cdn.example/v", now=datetime.now(UTC))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_authorization_modes() -> None:
    access = InMemoryAccessRepository(AccessMode.WHITELIST)
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    admin = Identity(platform=Platform.TELEGRAM, user_id="99")
    user = Identity(platform=Platform.TELEGRAM, user_id="1")
    auth = AuthorizationService(
        access=access,
        jobs=jobs,
        artifacts=arts,
        administrators=frozenset({admin}),
    )
    await auth.require_administrator(admin)
    with pytest.raises(AuthorizationError):
        await auth.require_user_access(user)
    await access.add_identity(user, now=datetime.now(UTC))
    await auth.require_user_access(user)
    with pytest.raises(NotFoundError):
        await auth.require_job_owner(JobId("J" * 22), user)


@pytest.mark.unit
def test_format_policy_ceiling() -> None:
    sel = build_format_selection(MediaMode.VIDEO, quality=VideoQuality.P720)
    assert "720" in sel.format_string
    assert height_within_ceiling(720, VideoQuality.P720)
    assert not height_within_ceiling(1080, VideoQuality.P720)
    audio = build_format_selection(MediaMode.AUDIO)
    assert audio.postprocessors
