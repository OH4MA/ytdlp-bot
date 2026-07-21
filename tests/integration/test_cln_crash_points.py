"""CLN-11: crash-point convergence for publish/delete paths."""

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
async def test_crash_after_mark_pending_before_unlink(tmp_path: Path) -> None:
    """Simulate crash after deletion_pending: recovery finishes unlink."""
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    payloads = InMemoryJobPayloadRepository()
    store = LocalArtifactStore(tmp_path / "s")
    cln = CleanupService(jobs=jobs, artifacts=arts, files=store, payloads=payloads)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    jid = JobId("J" * 22)
    await jobs.create(
        Job(
            job_id=jid,
            idempotency_key="k",
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
    ws = await store.create_job_workspace(jid)
    f = Path(ws) / "a.bin"
    f.write_bytes(b"hello")
    key = "S" * 22
    await store.atomically_publish(str(f), key)
    art = await arts.create_available(
        Artifact(
            artifact_id=ArtifactId("A" * 22),
            job_id=jid,
            storage_key=key,
            display_name="a.bin",
            media_type=MediaType.VIDEO_MP4,
            byte_size=5,
            ready_at=now - timedelta(hours=13),
            expires_at=now - timedelta(minutes=1),
        )
    )
    # Crash simulation: mark pending without finishing.
    from ytdlp_bot.domain.enums import DeletionReason
    from ytdlp_bot.ports.results import Ok

    pending = await arts.mark_deletion_pending(
        art.artifact_id,
        expected_version=art.version,
        reason=DeletionReason.EXPIRED,
        now=now,
    )
    assert isinstance(pending, Ok)
    # Recovery/retry converges.
    n = await cln.retry_pending_deletions(now=now)
    assert n >= 1
    final = await arts.get(art.artifact_id)
    assert final is not None
    assert final.access_state is ArtifactAccessState.DELETED
    # File gone.
    assert not await store.exists(key)
    # Idempotent second recovery.
    n2 = await cln.retry_pending_deletions(now=now)
    assert n2 == 0
