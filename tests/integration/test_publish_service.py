"""CAP publish path: file then DB under capacity lock."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.fakes import (
    DeterministicIdGenerator,
    InMemoryArtifactRepository,
    InMemoryJobPayloadRepository,
    InMemoryJobRepository,
)
from ytdlp_bot.adapters.storage.capacity import CapacityManager
from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore
from ytdlp_bot.application.capacity_publish import PublishService
from ytdlp_bot.domain.enums import JobState, MediaMode, MediaType, Platform
from ytdlp_bot.domain.identity import Identity, JobId, MessageContext
from ytdlp_bot.domain.jobs import Job, JobPayload


@pytest.mark.integration
@pytest.mark.asyncio
async def test_publish_creates_available_artifact(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "s")
    cap = CapacityManager(
        store=store,
        capacity_bytes=10**9,
        safety_headroom_bytes=1024,
        unknown_size_initial_reservation_bytes=1024,
        reservation_growth_bytes=1024,
    )
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    payloads = InMemoryJobPayloadRepository()
    ids = DeterministicIdGenerator()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    job = await jobs.create(
        Job(
            job_id=JobId("J" * 22),
            idempotency_key="t:1",
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
            state=JobState.POST_PROCESSING,
            dispatchable=False,
            acknowledged_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    await payloads.put(
        JobPayload(job_id=job.job_id, source_url="https://example.com/v", created_at=now)
    )
    await cap.reserve(job.job_id, known_size=100, now=now)
    ws = await store.create_job_workspace(job.job_id)
    src = Path(ws) / "out.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20)
    svc = PublishService(
        capacity=cap,
        store=store,
        artifacts=arts,
        jobs=jobs,
        payloads=payloads,
        ids=ids,
        retention_seconds=3600,
    )
    art = await svc.publish_candidate(
        job=job,
        source_path=str(src),
        display_name="out.mp4",
        media_type=MediaType.VIDEO_MP4,
        now=now,
    )
    assert art.byte_size > 0
    assert await arts.get(art.artifact_id) is not None
    assert await payloads.get(job.job_id) is None
    assert cap.reservation_total() == 0
