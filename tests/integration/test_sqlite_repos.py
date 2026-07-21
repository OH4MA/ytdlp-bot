"""SQLite job/artifact repository integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ytdlp_bot.adapters.persistence.sqlite.connection import open_connection
from ytdlp_bot.adapters.persistence.sqlite.migrate import apply_migrations
from ytdlp_bot.adapters.persistence.sqlite.repositories import (
    SqliteArtifactRepository,
    SqliteJobPayloadRepository,
    SqliteJobRepository,
)
from ytdlp_bot.domain.enums import (
    JobState,
    MediaMode,
    MediaType,
    Platform,
)
from ytdlp_bot.domain.identity import ArtifactId, Identity, JobId, MessageContext
from ytdlp_bot.domain.jobs import Artifact, Job, JobPayload
from ytdlp_bot.ports.results import Ok


def _job(job_id: str = "J" * 22) -> Job:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Job(
        job_id=JobId(job_id),
        idempotency_key="telegram:evt-1",
        owner=Identity(platform=Platform.TELEGRAM, user_id="1"),
        message_context=MessageContext(
            platform=Platform.TELEGRAM,
            chat_id="1",
            response_target="1",
            effective_upload_limit_bytes=10_000_000,
        ),
        request_mode=MediaMode.VIDEO,
        selected_preset="best",
        source_display="https://example.com",
        state=JobState.QUEUED,
        dispatchable=True,
        acknowledged_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_job_create_get_claim(tmp_path: Path) -> None:
    conn = await open_connection(tmp_path / "db.sqlite3")
    try:
        await apply_migrations(conn, now_ms=1)
        jobs = SqliteJobRepository(conn)
        payloads = SqliteJobPayloadRepository(conn)
        created = await jobs.create(_job())
        assert created.version == 1
        await payloads.put(
            JobPayload(
                job_id=created.job_id,
                source_url="https://example.com/v",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        claimed = await jobs.claim_next(
            controller_id="c1",
            now=datetime(2026, 1, 1, 1, tzinfo=UTC),
            expected_states=[JobState.QUEUED],
        )
        assert claimed is not None
        assert claimed.state is JobState.INSPECTING
        owned = await jobs.get_owned(
            created.job_id, Identity(platform=Platform.TELEGRAM, user_id="1")
        )
        assert owned is not None
        foreign = await jobs.get_owned(
            created.job_id, Identity(platform=Platform.DISCORD, user_id="1")
        )
        assert foreign is None
        result = await jobs.transition(
            created.job_id,
            expected_version=claimed.version,
            new_state=JobState.DOWNLOADING,
        )
        assert isinstance(result, Ok)
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_artifact_lifecycle(tmp_path: Path) -> None:
    conn = await open_connection(tmp_path / "db.sqlite3")
    try:
        await apply_migrations(conn, now_ms=1)
        jobs = SqliteJobRepository(conn)
        arts = SqliteArtifactRepository(conn)
        job = await jobs.create(_job())
        now = datetime(2026, 1, 1, tzinfo=UTC)
        art = await arts.create_available(
            Artifact(
                artifact_id=ArtifactId("A" * 22),
                job_id=job.job_id,
                storage_key="S" * 22,
                display_name="a.mp4",
                media_type=MediaType.VIDEO_MP4,
                byte_size=10,
                ready_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        assert art.access_state.value == "available"
        pending = await arts.mark_deletion_pending(
            art.artifact_id,
            expected_version=art.version,
            reason=__import__(
                "ytdlp_bot.domain.enums", fromlist=["DeletionReason"]
            ).DeletionReason.EXPIRED,
            now=now,
        )
        assert isinstance(pending, Ok)
        assert pending.value.token_version == 2
    finally:
        await conn.close()
