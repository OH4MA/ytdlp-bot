"""CLN scheduled runner and pending deletion retry."""

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


async def _job(jobs: InMemoryJobRepository, jid: str, state: JobState, now: datetime) -> Job:
    return await jobs.create(
        Job(
            job_id=JobId(jid),
            idempotency_key=f"k{jid}",
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
            state=state,
            dispatchable=state is JobState.QUEUED,
            acknowledged_at=now,
            created_at=now,
            updated_at=now,
            ready_at=now if state is JobState.COMPLETED else None,
        )
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_once_no_overlap_and_preserve_queued(tmp_path: Path) -> None:
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    payloads = InMemoryJobPayloadRepository()
    store = LocalArtifactStore(tmp_path / "s")
    cln = CleanupService(jobs=jobs, artifacts=arts, files=store, payloads=payloads)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    queued = await _job(jobs, "Q" * 22, JobState.QUEUED, now)
    active = await _job(jobs, "A" * 22, JobState.DOWNLOADING, now)
    assert await cln.reconcile_interrupted_job(queued, now=now) is JobState.QUEUED
    assert await cln.reconcile_interrupted_job(active, now=now) is JobState.CANCELLED_BY_RESTART

    completed = await _job(jobs, "C" * 22, JobState.COMPLETED, now)
    ws = await store.create_job_workspace(completed.job_id)
    f = Path(ws) / "x.bin"
    f.write_bytes(b"data")
    key = "K" * 22
    await store.atomically_publish(str(f), key)
    art = await arts.create_available(
        Artifact(
            artifact_id=ArtifactId("Z" * 22),
            job_id=completed.job_id,
            storage_key=key,
            display_name="x.bin",
            media_type=MediaType.VIDEO_MP4,
            byte_size=4,
            ready_at=now - timedelta(hours=13),
            expires_at=now - timedelta(minutes=1),
        )
    )
    stats = await cln.run_once(now=now)
    assert stats["expired"] >= 1
    # Overlap skip
    cln._running = True
    skipped = await cln.run_once(now=now)
    assert skipped.get("skipped") == 1
    cln._running = False
    gone = await arts.get(art.artifact_id)
    assert gone is not None and gone.access_state is ArtifactAccessState.DELETED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_artifact_reconciliation(tmp_path: Path) -> None:
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    payloads = InMemoryJobPayloadRepository()
    store = LocalArtifactStore(tmp_path / "s")
    cln = CleanupService(jobs=jobs, artifacts=arts, files=store, payloads=payloads)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    job = await _job(jobs, "M" * 22, JobState.COMPLETED, now)
    art = await arts.create_available(
        Artifact(
            artifact_id=ArtifactId("N" * 22),
            job_id=job.job_id,
            storage_key="X" * 22,
            display_name="missing.bin",
            media_type=MediaType.VIDEO_MP4,
            byte_size=1,
            ready_at=now,
            expires_at=now + timedelta(hours=12),
        )
    )
    ok = await cln.reconcile_artifact_file(art, now=now)
    assert ok is False
    final = await arts.get(art.artifact_id)
    assert final is not None
    assert final.access_state is ArtifactAccessState.DELETED
