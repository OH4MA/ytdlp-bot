"""ADM service unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.fakes import (
    DeterministicIdGenerator,
    FakeClock,
    InMemoryAccessRepository,
    InMemoryAdminConfirmationRepository,
    InMemoryArtifactRepository,
    InMemoryJobRepository,
    InMemorySettingsRepository,
)
from ytdlp_bot.application.admin_service import AdminService
from ytdlp_bot.application.authorization import AuthorizationService
from ytdlp_bot.domain.commands import (
    AdminArgs,
    AdminRetentionSet,
    AdminStatus,
    CommandName,
    CommandRequest,
)
from ytdlp_bot.domain.enums import AccessMode, Platform
from ytdlp_bot.domain.identity import Identity, MessageContext


@pytest.mark.unit
@pytest.mark.asyncio
async def test_admin_retention_and_status():
    admin = Identity(platform=Platform.TELEGRAM, user_id="99")
    access = InMemoryAccessRepository(AccessMode.ALLOW_ALL)
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    auth = AuthorizationService(
        access=access, jobs=jobs, artifacts=arts, administrators=frozenset({admin})
    )
    settings = InMemorySettingsRepository({"retention_seconds": 43200, "capacity_bytes": 10**9})
    conf = InMemoryAdminConfirmationRepository()
    ids = DeterministicIdGenerator()
    svc = AdminService(
        auth=auth, settings=settings, access=access, confirmations=conf, id_confirmation=ids
    )
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    ctx = MessageContext(
        platform=Platform.TELEGRAM, chat_id="1", response_target="1", effective_upload_limit_bytes=1
    )
    req = CommandRequest(
        request_id="a1",
        identity=admin,
        context=ctx,
        command=CommandName.YTDL_ADMIN,
        arguments=AdminArgs(action=AdminStatus()),
        received_at=clock.now(),
    )
    view = await svc.handle(req, AdminArgs(action=AdminStatus()))
    assert view.message_key == "admin.status"
    req2 = CommandRequest(
        request_id="a2",
        identity=admin,
        context=ctx,
        command=CommandName.YTDL_ADMIN,
        arguments=AdminArgs(action=AdminRetentionSet(duration_seconds=7200)),
        received_at=clock.now(),
    )
    view2 = await svc.handle(req2, AdminArgs(action=AdminRetentionSet(duration_seconds=7200)))
    assert view2.safe_fields and view2.safe_fields.get("new") == 7200
    stranger = Identity(platform=Platform.TELEGRAM, user_id="1")
    bad = await svc.handle(
        CommandRequest(
            request_id="a3",
            identity=stranger,
            context=ctx,
            command=CommandName.YTDL_ADMIN,
            arguments=AdminArgs(action=AdminStatus()),
            received_at=clock.now(),
        ),
        AdminArgs(action=AdminStatus()),
    )
    assert bad.kind == "user_error"
