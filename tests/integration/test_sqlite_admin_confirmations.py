"""ADM-07/08: SQLite durable capacity confirmations on shipped path."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.fakes import DeterministicIdGenerator
from tests.fakes.repositories import (
    InMemoryAccessRepository,
    InMemoryArtifactRepository,
    InMemoryJobRepository,
)
from ytdlp_bot.adapters.persistence.sqlite.connection import open_connection
from ytdlp_bot.adapters.persistence.sqlite.migrate import apply_migrations
from ytdlp_bot.adapters.persistence.sqlite.repositories import (
    SqliteAdminConfirmationRepository,
    SqliteSettingsRepository,
    _confirmation_digest,
)
from ytdlp_bot.application.admin_service import AdminService
from ytdlp_bot.application.authorization import AuthorizationService
from ytdlp_bot.domain.commands import (
    AdminArgs,
    AdminCapacitySet,
    CommandName,
    CommandRequest,
)
from ytdlp_bot.domain.enums import AccessMode, Platform
from ytdlp_bot.domain.identity import Identity, MessageContext


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sqlite_confirmation_digest_one_time_and_restart(tmp_path: Path) -> None:
    db = tmp_path / "state" / "service.sqlite3"
    conn = await open_connection(db)
    await apply_migrations(conn, now_ms=1)
    repo = SqliteAdminConfirmationRepository(conn)
    owner = Identity(platform=Platform.TELEGRAM, user_id="99")
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    cid = "C" * 22
    fp = hashlib.sha256(b"capacity_set:123").hexdigest()
    snap = "cap=123"

    await repo.create(
        cid,
        action_fingerprint=fp,
        owner=owner,
        expires_at=now + timedelta(seconds=60),
        projected_snapshot=snap,
    )

    # Raw confirmation ID must not appear as PK; only digest is stored.
    digests = await repo.digests()
    assert digests == [_confirmation_digest(cid)]
    assert cid.encode() not in digests[0]  # digest is not plaintext id

    # Owner/fingerprint/snapshot match → consume once.
    ok = await repo.consume_if_matching(
        cid,
        action_fingerprint=fp,
        owner=owner,
        now=now + timedelta(seconds=10),
        projected_snapshot=snap,
    )
    assert ok is True
    # Replay fails.
    ok2 = await repo.consume_if_matching(
        cid,
        action_fingerprint=fp,
        owner=owner,
        now=now + timedelta(seconds=11),
        projected_snapshot=snap,
    )
    assert ok2 is False

    # Foreign owner fails on a fresh confirmation.
    cid2 = "D" * 22
    await repo.create(
        cid2,
        action_fingerprint=fp,
        owner=owner,
        expires_at=now + timedelta(seconds=60),
        projected_snapshot=snap,
    )
    stranger = Identity(platform=Platform.TELEGRAM, user_id="1")
    assert (
        await repo.consume_if_matching(
            cid2,
            action_fingerprint=fp,
            owner=stranger,
            now=now,
            projected_snapshot=snap,
        )
        is False
    )

    # Expiry fails.
    cid3 = "E" * 22
    await repo.create(
        cid3,
        action_fingerprint=fp,
        owner=owner,
        expires_at=now + timedelta(seconds=5),
        projected_snapshot=snap,
    )
    assert (
        await repo.consume_if_matching(
            cid3,
            action_fingerprint=fp,
            owner=owner,
            now=now + timedelta(seconds=6),
            projected_snapshot=snap,
        )
        is False
    )

    await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_capacity_confirm_uses_sqlite_repo(tmp_path: Path) -> None:
    """AdminService capacity path persists confirmations via SQLite (shipped composition shape)."""
    db = tmp_path / "state" / "service.sqlite3"
    conn = await open_connection(db)
    await apply_migrations(conn, now_ms=1)
    confirmations = SqliteAdminConfirmationRepository(conn)
    settings = SqliteSettingsRepository(
        conn,
        {
            "capacity_bytes": 10**9,
            "retention_seconds": 43200,
            "link_expiry_seconds": 3600,
            "access_mode": AccessMode.ALLOW_ALL.value,
        },
    )
    admin = Identity(platform=Platform.TELEGRAM, user_id="99")
    access = InMemoryAccessRepository(AccessMode.ALLOW_ALL)
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    auth = AuthorizationService(
        access=access, jobs=jobs, artifacts=arts, administrators=frozenset({admin})
    )
    ids = DeterministicIdGenerator()
    svc = AdminService(
        auth=auth,
        settings=settings,
        access=access,
        confirmations=confirmations,
        id_confirmation=ids,
    )
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    ctx = MessageContext(
        platform=Platform.TELEGRAM,
        chat_id="1",
        response_target="1",
        effective_upload_limit_bytes=1,
    )
    # First call: create confirmation (no confirmation_id).
    view = await svc.handle(
        CommandRequest(
            request_id="c1",
            identity=admin,
            context=ctx,
            command=CommandName.YTDL_ADMIN,
            arguments=AdminArgs(action=AdminCapacitySet(capacity_bytes=500_000_000)),
            received_at=now,
        ),
        AdminArgs(action=AdminCapacitySet(capacity_bytes=500_000_000)),
    )
    assert view.message_key == "admin.confirmation_required"
    cid = view.safe_fields["confirmation_id"]  # type: ignore[index]
    assert await confirmations.raw_row_count() == 1
    assert await confirmations.digests() == [_confirmation_digest(str(cid))]

    # Second call: consume confirmation and apply.
    view2 = await svc.handle(
        CommandRequest(
            request_id="c2",
            identity=admin,
            context=ctx,
            command=CommandName.YTDL_ADMIN,
            arguments=AdminArgs(
                action=AdminCapacitySet(capacity_bytes=500_000_000, confirmation_id=str(cid))
            ),
            received_at=now + timedelta(seconds=5),
        ),
        AdminArgs(action=AdminCapacitySet(capacity_bytes=500_000_000, confirmation_id=str(cid))),
    )
    assert view2.message_key == "admin.setting_updated"
    # Replay same confirmation fails.
    view3 = await svc.handle(
        CommandRequest(
            request_id="c3",
            identity=admin,
            context=ctx,
            command=CommandName.YTDL_ADMIN,
            arguments=AdminArgs(
                action=AdminCapacitySet(capacity_bytes=500_000_000, confirmation_id=str(cid))
            ),
            received_at=now + timedelta(seconds=6),
        ),
        AdminArgs(action=AdminCapacitySet(capacity_bytes=500_000_000, confirmation_id=str(cid))),
    )
    assert view3.kind == "user_error"
    await conn.close()
