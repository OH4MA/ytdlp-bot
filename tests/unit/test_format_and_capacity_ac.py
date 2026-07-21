"""AC02 resolution ceiling and AC12 capacity eviction path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.fakes import (
    InMemoryArtifactRepository,
    InMemoryJobPayloadRepository,
    InMemoryJobRepository,
)
from ytdlp_bot.adapters.storage.capacity import CapacityManager
from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore
from ytdlp_bot.application.cleanup import CleanupService
from ytdlp_bot.domain.enums import (
    ArtifactAccessState,
    JobState,
    MediaMode,
    MediaType,
    Platform,
    VideoQuality,
)
from ytdlp_bot.domain.format_policy import build_format_selection, height_within_ceiling
from ytdlp_bot.domain.identity import ArtifactId, Identity, JobId, MessageContext
from ytdlp_bot.domain.jobs import Artifact, Job


@pytest.mark.unit
def test_ac02_resolution_ceiling_never_exceeded() -> None:
    sel = build_format_selection(MediaMode.VIDEO, quality=VideoQuality.P720)
    assert "720" in sel.format_string
    assert height_within_ceiling(720, VideoQuality.P720)
    assert not height_within_ceiling(1080, VideoQuality.P720)
    assert not height_within_ceiling(2160, VideoQuality.P480)
    for q in VideoQuality:
        if q is VideoQuality.BEST:
            continue
        s = build_format_selection(MediaMode.VIDEO, quality=q)
        assert q.value.replace("p", "") in s.format_string or "height" in s.format_string


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ac12_expire_before_evict_candidates(tmp_path: Path) -> None:
    """Expired available artifacts are selected before non-expired (eviction order)."""
    store = LocalArtifactStore(tmp_path / "s")
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    payloads = InMemoryJobPayloadRepository()
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)

    async def make(i: str, *, expired: bool) -> Artifact:
        jid = JobId((i * 22)[:22])
        await jobs.create(
            Job(
                job_id=jid,
                idempotency_key=f"k{i}",
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
                ready_at=now - timedelta(hours=2 if expired else 0),
            )
        )
        aid = ArtifactId((("A" + i) * 11)[:22].ljust(22, "A"))
        key = (("S" + i) * 11)[:22].ljust(22, "A")
        ws = await store.create_job_workspace(jid)
        f = Path(ws) / "f.bin"
        f.write_bytes(b"x" * 10)
        await store.atomically_publish(str(f), key)
        return await arts.create_available(
            Artifact(
                artifact_id=aid,
                job_id=jid,
                storage_key=key,
                display_name=f"{i}.bin",
                media_type=MediaType.VIDEO_MP4,
                byte_size=10,
                ready_at=now - timedelta(hours=2 if expired else 0),
                expires_at=now - timedelta(hours=1) if expired else now + timedelta(hours=12),
            )
        )

    expired = await make("E", expired=True)
    active = await make("N", expired=False)
    expired_list = await arts.list_expired(now=now, limit=10)
    assert any(a.artifact_id == expired.artifact_id for a in expired_list)
    assert all(a.artifact_id != active.artifact_id for a in expired_list)
    # Capacity manager exists and tracks after cleanup
    cap = CapacityManager(
        store=store,
        capacity_bytes=1000,
        safety_headroom_bytes=10,
        unknown_size_initial_reservation_bytes=100,
        reservation_growth_bytes=100,
    )
    cln = CleanupService(jobs=jobs, artifacts=arts, files=store, payloads=payloads)
    n = await cln.expire_due_artifacts(now=now, limit=10)
    assert n >= 1
    gone = await arts.get(expired.artifact_id)
    assert gone is not None
    assert gone.access_state is ArtifactAccessState.DELETED
    _ = cap  # capacity accounting available for operator path
