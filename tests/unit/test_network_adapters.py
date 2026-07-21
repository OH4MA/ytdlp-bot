"""Network DNS/preflight adapter unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.fakes.network import FakeUrlPreflightClient
from ytdlp_bot.adapters.network.preflight import EgressSelfTest
from ytdlp_bot.adapters.network.resolver import StaticDnsResolver
from ytdlp_bot.application.url_safety import UrlSafetyService
from ytdlp_bot.domain.errors import ValidationError


@pytest.mark.unit
@pytest.mark.asyncio
async def test_static_dns_and_egress_self_test() -> None:
    dns = StaticDnsResolver(records={"example.com": ("93.184.216.34",)})
    resolved = await dns.resolve("example.com")
    assert resolved.addresses == ("93.184.216.34",)
    pre = FakeUrlPreflightClient()
    egress = EgressSelfTest(dns=dns, preflight=pre, proxy_url="http://proxy:1")
    results = await egress.run()
    assert results["dns_public"] is True
    assert results["blocks_loopback"] is True
    assert results["ok"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_url_safety_with_static_dns() -> None:
    dns = StaticDnsResolver(records={"cdn.example": ("8.8.8.8",)})
    pre = FakeUrlPreflightClient()
    svc = UrlSafetyService(dns=dns, preflight=pre, allowed_ports=frozenset({80, 443}))
    v = await svc.validate("https://cdn.example/a", now=datetime.now(UTC))
    assert "cdn.example" in v.source_display
    dns.records["cdn.example"] = ("10.0.0.1",)
    with pytest.raises(ValidationError):
        await svc.validate("https://cdn.example/a", now=datetime.now(UTC))
