"""Unauthorized access denial scratchpad for whitelist onboarding."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.fakes import (
    InMemoryAccessDenialRepository,
    InMemoryAccessRepository,
    InMemoryArtifactRepository,
    InMemoryJobRepository,
)
from ytdlp_bot.adapters.platform.messages import render_command_result
from ytdlp_bot.application.admin_service import AdminService
from ytdlp_bot.application.authorization import AuthorizationService
from ytdlp_bot.domain.commands import (
    AdminArgs,
    AdminWhitelistAdd,
    AdminWhitelistPending,
    CommandName,
    CommandRequest,
    UserError,
)
from ytdlp_bot.domain.enums import AccessMode, FailureCode, Platform
from ytdlp_bot.domain.errors import AuthorizationError
from ytdlp_bot.domain.identity import Identity, MessageContext
from ytdlp_bot.domain.locale import load_zh_tw_catalog


@pytest.mark.unit
@pytest.mark.asyncio
async def test_require_user_access_records_denial() -> None:
    user = Identity(platform=Platform.TELEGRAM, user_id="5001")
    admin = Identity(platform=Platform.TELEGRAM, user_id="99")
    access = InMemoryAccessRepository(AccessMode.WHITELIST)
    denials = InMemoryAccessDenialRepository()
    auth = AuthorizationService(
        access=access,
        jobs=InMemoryJobRepository(),
        artifacts=InMemoryArtifactRepository(),
        administrators=frozenset({admin}),
        denials=denials,
    )
    now = datetime(2026, 7, 23, tzinfo=UTC)
    with pytest.raises(AuthorizationError):
        await auth.require_user_access(user, now=now, command="ytdl")
    with pytest.raises(AuthorizationError):
        await auth.require_user_access(user, now=now + timedelta(minutes=1), command="ytmp3")

    rows = await auth.list_access_denials(now=now)
    assert len(rows) == 1
    assert rows[0].identity == user
    assert rows[0].attempt_count == 2
    assert rows[0].last_command == "ytmp3"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_admin_whitelist_pending_and_add_clears() -> None:
    admin = Identity(platform=Platform.TELEGRAM, user_id="99")
    user = Identity(platform=Platform.TELEGRAM, user_id="5001")
    access = InMemoryAccessRepository(AccessMode.WHITELIST)
    denials = InMemoryAccessDenialRepository()
    auth = AuthorizationService(
        access=access,
        jobs=InMemoryJobRepository(),
        artifacts=InMemoryArtifactRepository(),
        administrators=frozenset({admin}),
        denials=denials,
    )
    now = datetime(2026, 7, 23, tzinfo=UTC)
    with pytest.raises(AuthorizationError):
        await auth.require_user_access(user, now=now, command="message")

    admin_svc = AdminService(
        auth=auth,
        settings=_FakeSettings(),
        access=access,
        confirmations=_FakeConfirmations(),
        id_confirmation=_FakeIds(),
    )
    ctx = MessageContext(
        platform=Platform.TELEGRAM,
        chat_id="1",
        response_target="1",
        effective_upload_limit_bytes=1,
    )
    req = CommandRequest(
        request_id="1",
        identity=admin,
        context=ctx,
        command=CommandName.YTDL_ADMIN,
        arguments=AdminArgs(action=AdminWhitelistPending()),
        received_at=now,
    )
    pending = await admin_svc.handle(req, AdminArgs(action=AdminWhitelistPending()))
    assert pending.message_key == "admin.whitelist_pending"
    assert "5001" in str((pending.safe_fields or {}).get("body", ""))

    add = await admin_svc.handle(
        req,
        AdminArgs(action=AdminWhitelistAdd(identity=user)),
    )
    assert add.message_key == "admin.view"
    empty = await admin_svc.handle(req, AdminArgs(action=AdminWhitelistPending()))
    assert empty.message_key == "admin.whitelist_pending_empty"
    await auth.require_user_access(user, now=now)


@pytest.mark.unit
def test_unauthorized_message_includes_identity() -> None:
    err = UserError(
        code=FailureCode.NOT_AUTHORIZED,
        message_key="failure.not_authorized",
        safe_context={"identity": "telegram:5001", "platform": "telegram", "user_id": "5001"},
    )
    text = render_command_result(err)
    assert text is not None
    assert "telegram:5001" in text
    catalog = load_zh_tw_catalog()
    assert "識別碼" in catalog["failure.not_authorized"]


@pytest.mark.unit
def test_parse_whitelist_pending() -> None:
    from ytdlp_bot.domain.commands import parse_admin_args

    args = parse_admin_args(["whitelist", "pending"])
    assert isinstance(args.action, AdminWhitelistPending)


class _FakeSettings:
    async def effective_values(self) -> dict[str, object]:
        return {}

    async def set_override(self, key: str, value: object, *, updated_by, now) -> None:
        return None


class _FakeConfirmations:
    async def create(self, *args, **kwargs) -> None:
        return None

    async def consume_if_matching(self, *args, **kwargs) -> bool:
        return True


class _FakeIds:
    def confirmation_id(self) -> str:
        return "C" * 22
