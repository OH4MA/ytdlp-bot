"""Role-differentiated /ytdl_help content for users vs administrators."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.fakes import (
    InMemoryAccessRepository,
    InMemoryArtifactRepository,
    InMemoryJobPayloadRepository,
    InMemoryJobRepository,
)
from tests.fakes.network import FakeDnsResolver, FakeUrlPreflightClient
from tests.fakes.platform import FakePlatformPort
from tests.fakes.system import DeterministicIdGenerator, FakeClock
from ytdlp_bot.application.authorization import AuthorizationService
from ytdlp_bot.application.command_service import CommandService
from ytdlp_bot.application.job_service import JobService
from ytdlp_bot.application.url_safety import UrlSafetyService
from ytdlp_bot.domain.commands import CommandName, CommandRequest, HelpArgs, HelpView
from ytdlp_bot.domain.enums import AccessMode, Platform
from ytdlp_bot.domain.identity import Identity, MessageContext


def _ctx() -> MessageContext:
    return MessageContext(
        platform=Platform.TELEGRAM,
        chat_id="1",
        response_target="1",
        effective_upload_limit_bytes=50_000_000,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_help_message_key_by_role() -> None:
    user = Identity(platform=Platform.TELEGRAM, user_id="1")
    admin = Identity(platform=Platform.TELEGRAM, user_id="99")
    access = InMemoryAccessRepository(AccessMode.ALLOW_ALL)
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    auth = AuthorizationService(
        access=access,
        jobs=jobs,
        artifacts=arts,
        administrators=frozenset({admin}),
    )
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    job_svc = JobService(
        auth=auth,
        url_safety=UrlSafetyService(
            dns=FakeDnsResolver(),
            preflight=FakeUrlPreflightClient(),
            allowed_ports=frozenset({80, 443}),
            max_redirects=5,
        ),
        jobs=jobs,
        payloads=InMemoryJobPayloadRepository(),
        platform=FakePlatformPort(),
        clock=clock,
        ids=DeterministicIdGenerator(),
    )
    cmd = CommandService(jobs=job_svc)

    async def _help(identity: Identity) -> HelpView:
        result = await cmd.handle(
            CommandRequest(
                request_id="h1",
                identity=identity,
                context=_ctx(),
                command=CommandName.YTDL_HELP,
                arguments=HelpArgs(),
                received_at=clock.now(),
            )
        )
        assert isinstance(result, HelpView)
        return result

    assert (await _help(user)).message_key == "help.main"
    assert (await _help(admin)).message_key == "help.admin"
