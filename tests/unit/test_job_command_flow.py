"""Job service + command routing with fakes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.fakes import (
    DeterministicIdGenerator,
    FakeClock,
    FakeDnsResolver,
    FakePlatformPort,
    FakeUrlPreflightClient,
    InMemoryAccessRepository,
    InMemoryArtifactRepository,
    InMemoryJobPayloadRepository,
    InMemoryJobRepository,
)
from ytdlp_bot.application.authorization import AuthorizationService
from ytdlp_bot.application.command_service import CommandService
from ytdlp_bot.application.job_service import JobService
from ytdlp_bot.application.url_safety import UrlSafetyService
from ytdlp_bot.domain.commands import (
    AcceptedJob,
    CancelArgs,
    CommandName,
    CommandRequest,
    HelpView,
    StatusArgs,
    YtdlArgs,
)
from ytdlp_bot.domain.enums import AccessMode, Platform, VideoQuality
from ytdlp_bot.domain.identity import Identity, MessageContext


def _ctx() -> MessageContext:
    return MessageContext(
        platform=Platform.TELEGRAM,
        chat_id="1",
        response_target="1",
        effective_upload_limit_bytes=50_000_000,
    )


@pytest.fixture
def services() -> tuple[CommandService, FakePlatformPort, FakeClock]:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    ids = DeterministicIdGenerator()
    jobs = InMemoryJobRepository()
    payloads = InMemoryJobPayloadRepository()
    access = InMemoryAccessRepository(AccessMode.ALLOW_ALL)
    arts = InMemoryArtifactRepository()
    auth = AuthorizationService(
        access=access,
        jobs=jobs,
        artifacts=arts,
        administrators=frozenset(),
    )
    dns = FakeDnsResolver()
    dns.map("example.com", "8.8.8.8")
    url = UrlSafetyService(
        dns=dns,
        preflight=FakeUrlPreflightClient(),
        allowed_ports=frozenset({80, 443}),
    )
    platform = FakePlatformPort()
    job_svc = JobService(
        auth=auth,
        url_safety=url,
        jobs=jobs,
        payloads=payloads,
        platform=platform,
        clock=clock,
        ids=ids,
    )
    return CommandService(jobs=job_svc), platform, clock


@pytest.mark.unit
@pytest.mark.asyncio
async def test_submit_status_cancel_help(services) -> None:
    cmd, platform, clock = services
    identity = Identity(platform=Platform.TELEGRAM, user_id="1")
    req = CommandRequest(
        request_id="u1",
        identity=identity,
        context=_ctx(),
        command=CommandName.YTDL,
        arguments=YtdlArgs(url="https://example.com/v", quality=VideoQuality.BEST),
        received_at=clock.now(),
    )
    result = await cmd.handle(req)
    assert isinstance(result, AcceptedJob)
    assert any(c[0] == "acknowledge_job" for c in platform.calls)

    st = await cmd.handle(
        CommandRequest(
            request_id="u2",
            identity=identity,
            context=_ctx(),
            command=CommandName.YTDL_STATUS,
            arguments=StatusArgs(job_id=result.job_id),
            received_at=clock.now(),
        )
    )
    assert st.job_id == result.job_id  # type: ignore[union-attr]

    help_r = await cmd.handle(
        CommandRequest(
            request_id="u3",
            identity=identity,
            context=_ctx(),
            command=CommandName.YTDL_HELP,
            arguments=__import__("ytdlp_bot.domain.commands", fromlist=["HelpArgs"]).HelpArgs(),
            received_at=clock.now(),
        )
    )
    assert isinstance(help_r, HelpView)
    assert help_r.message_key == "help.main"

    cancelled = await cmd.handle(
        CommandRequest(
            request_id="u4",
            identity=identity,
            context=_ctx(),
            command=CommandName.YTDL_CANCEL,
            arguments=CancelArgs(job_id=result.job_id),
            received_at=clock.now(),
        )
    )
    assert cancelled.state in {"cancelled", "cancelling"}  # type: ignore[union-attr]
