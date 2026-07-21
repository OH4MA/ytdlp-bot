"""CAP eviction selection ordering tests (repository candidates)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.fakes import InMemoryArtifactRepository, InMemoryJobRepository
from ytdlp_bot.domain.enums import JobState, MediaMode, MediaType, Platform
from ytdlp_bot.domain.identity import ArtifactId, Identity, JobId, MessageContext
from ytdlp_bot.domain.jobs import Artifact, Job


@pytest.mark.unit
@pytest.mark.asyncio
async def test_eviction_oldest_ready_first() -> None:
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    async def make(i: int, ready: datetime) -> Artifact:
        jid = JobId(("J" + str(i)) * 11)[:22].ljust(22, "A")
        await jobs.create(
            Job(
                job_id=jid,
                idempotency_key=f"t:{i}",
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
                ready_at=ready,
            )
        )
        aid = ArtifactId(("A" + str(i)) * 11)[:22].ljust(22, "A")
        key = (("S" + str(i)) * 11)[:22].ljust(22, "A")
        return await arts.create_available(
            Artifact(
                artifact_id=aid,
                job_id=jid,
                storage_key=key,
                display_name=f"{i}.mp4",
                media_type=MediaType.VIDEO_MP4,
                byte_size=10,
                ready_at=ready,
                expires_at=now + timedelta(hours=12),
            )
        )

    a0 = await make(0, now)
    a1 = await make(1, now + timedelta(hours=1))
    cands = await arts.list_eviction_candidates(now=now, limit=10)
    assert cands[0].artifact_id == a0.artifact_id
    assert cands[1].artifact_id == a1.artifact_id
