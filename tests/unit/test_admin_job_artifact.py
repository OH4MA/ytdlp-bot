"""ADM cancel and artifact invalidation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.fakes import (
    DeterministicIdGenerator,
    InMemoryAccessRepository,
    InMemoryAdminConfirmationRepository,
    InMemoryArtifactRepository,
    InMemoryJobRepository,
    InMemorySettingsRepository,
)
from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore
from ytdlp_bot.application.admin_service import AdminService
from ytdlp_bot.application.authorization import AuthorizationService
from ytdlp_bot.domain.commands import (
    AdminArgs,
    AdminArtifactDelete,
    AdminCancel,
    CommandName,
    CommandRequest,
)
from ytdlp_bot.domain.enums import AccessMode, JobState, MediaMode, MediaType, Platform
from ytdlp_bot.domain.identity import ArtifactId, Identity, JobId, MessageContext
from ytdlp_bot.domain.jobs import Artifact, Job


@pytest.mark.unit
@pytest.mark.asyncio
async def test_admin_cancel_and_delete_artifact(tmp_path: Path) -> None:
    admin = Identity(platform=Platform.TELEGRAM, user_id="99")
    access = InMemoryAccessRepository(AccessMode.ALLOW_ALL)
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    store = LocalArtifactStore(tmp_path / "s")
    auth = AuthorizationService(
        access=access, jobs=jobs, artifacts=arts, administrators=frozenset({admin})
    )
    settings = InMemorySettingsRepository({"retention_seconds": 43200, "capacity_bytes": 10**9})
    svc = AdminService(
        auth=auth,
        settings=settings,
        access=access,
        confirmations=InMemoryAdminConfirmationRepository(),
        id_confirmation=DeterministicIdGenerator(),
        jobs=jobs,
        artifacts=arts,
        files=store,
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    jid = JobId("J" * 22)
    job = await jobs.create(
        Job(
            job_id=jid,
            idempotency_key="k1",
            owner=Identity(platform=Platform.TELEGRAM, user_id="1"),
            message_context=MessageContext(
                platform=Platform.TELEGRAM,
                chat_id="1",
                response_target="1",
                effective_upload_limit_bytes=1,
            ),
            request_mode=MediaMode.VIDEO,
            selected_preset="best",
            source_display="https://example.com",
            state=JobState.DOWNLOADING,
            dispatchable=False,
            acknowledged_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    ctx = MessageContext(
        platform=Platform.TELEGRAM, chat_id="1", response_target="1", effective_upload_limit_bytes=1
    )
    res = await svc.handle(
        CommandRequest(
            request_id="1",
            identity=admin,
            context=ctx,
            command=CommandName.YTDL_ADMIN,
            arguments=AdminArgs(action=AdminCancel(job_id=jid)),
            received_at=now,
        ),
        AdminArgs(action=AdminCancel(job_id=jid)),
    )
    assert res.kind == "status"
    final = await jobs.get(jid)
    assert final is not None and final.state is JobState.CANCELLED

    ws = await store.create_job_workspace(JobId("K" * 22))
    f = Path(ws) / "a.bin"
    f.write_bytes(b"data")
    key = "S" * 22
    await store.atomically_publish(str(f), key)
    art = await arts.create_available(
        Artifact(
            artifact_id=ArtifactId("A" * 22),
            job_id=job.job_id,
            storage_key=key,
            display_name="a.bin",
            media_type=MediaType.VIDEO_MP4,
            byte_size=4,
            ready_at=now,
            expires_at=now,
        )
    )
    del_res = await svc.handle(
        CommandRequest(
            request_id="2",
            identity=admin,
            context=ctx,
            command=CommandName.YTDL_ADMIN,
            arguments=AdminArgs(action=AdminArtifactDelete(artifact_id=art.artifact_id)),
            received_at=now,
        ),
        AdminArgs(action=AdminArtifactDelete(artifact_id=art.artifact_id)),
    )
    assert del_res.kind == "admin"
    gone = await arts.get(art.artifact_id)
    assert gone is not None
    from ytdlp_bot.domain.enums import ArtifactAccessState

    assert gone.access_state is ArtifactAccessState.DELETED
