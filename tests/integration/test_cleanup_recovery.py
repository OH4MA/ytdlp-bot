"""CLN: expiry cleanup and restart reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.fakes import (
    InMemoryArtifactRepository,
    InMemoryJobPayloadRepository,
    InMemoryJobRepository,
)
from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore
from ytdlp_bot.application.cleanup import CleanupService
from ytdlp_bot.domain.enums import (
    ArtifactAccessState,
    JobState,
    MediaMode,
    MediaType,
    Platform,
)
from ytdlp_bot.domain.identity import ArtifactId, Identity, JobId, MessageContext
from ytdlp_bot.domain.jobs import Artifact, Job


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expire_and_restart_reconcile(tmp_path: Path) -> None:
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    payloads = InMemoryJobPayloadRepository()
    store = LocalArtifactStore(tmp_path / "store")
    cln = CleanupService(jobs=jobs, artifacts=arts, files=store, payloads=payloads)

    now = datetime(2026, 1, 1, tzinfo=UTC)
    job = await jobs.create(
        Job(
            job_id=JobId("J" * 22),
            idempotency_key="telegram:1",
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
            state=JobState.COMPLETED,
            dispatchable=False,
            acknowledged_at=now,
            created_at=now,
            updated_at=now,
            ready_at=now,
        )
    )
    ws = await store.create_job_workspace(job.job_id)
    src = Path(ws) / "f.bin"
    src.write_bytes(b"hello")
    key = "S" * 22
    await store.atomically_publish(str(src), key)
    art = await arts.create_available(
        Artifact(
            artifact_id=ArtifactId("A" * 22),
            job_id=job.job_id,
            storage_key=key,
            display_name="f.bin",
            media_type=MediaType.VIDEO_MP4,
            byte_size=5,
            ready_at=now - timedelta(hours=13),
            expires_at=now - timedelta(hours=1),
        )
    )
    assert art.access_state is ArtifactAccessState.AVAILABLE
    n = await cln.expire_due_artifacts(now=now, limit=10)
    assert n == 1
    gone = await arts.get(art.artifact_id)
    assert gone is not None
    assert gone.access_state is ArtifactAccessState.DELETED

    active = await jobs.create(
        Job(
            job_id=JobId("K" * 22),
            idempotency_key="telegram:2",
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
    state = await cln.reconcile_interrupted_job(active, now=now)
    assert state is JobState.CANCELLED_BY_RESTART
