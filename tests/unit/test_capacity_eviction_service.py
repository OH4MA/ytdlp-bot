"""Capacity eviction: oldest ready_at first, skip active leases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.fakes import (
    FakeArtifactLeaseRegistry,
    InMemoryArtifactRepository,
    InMemoryJobRepository,
)
from ytdlp_bot.adapters.storage.capacity import CapacityManager
from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore
from ytdlp_bot.application.capacity_eviction import CapacityEvictionService
from ytdlp_bot.domain.enums import (
    ArtifactAccessState,
    JobState,
    LeaseKind,
    MediaMode,
    MediaType,
    Platform,
)
from ytdlp_bot.domain.errors import DomainError
from ytdlp_bot.domain.identity import ArtifactId, Identity, JobId, MessageContext
from ytdlp_bot.domain.jobs import Artifact, Job


async def _completed_job(
    jobs: InMemoryJobRepository, jid: str, now: datetime, *, ready: datetime
) -> Job:
    return await jobs.create(
        Job(
            job_id=JobId(jid),
            idempotency_key=f"k:{jid}",
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


async def _publish_art(
    *,
    store: LocalArtifactStore,
    arts: InMemoryArtifactRepository,
    job: Job,
    aid: str,
    key: str,
    size: int,
    ready: datetime,
    expires: datetime,
) -> Artifact:
    ws = await store.create_job_workspace(job.job_id)
    path = Path(ws) / "a.bin"
    path.write_bytes(b"x" * size)
    await store.atomically_publish(str(path), key)
    return await arts.create_available(
        Artifact(
            artifact_id=ArtifactId(aid),
            job_id=job.job_id,
            storage_key=key,
            display_name=f"{aid}.bin",
            media_type=MediaType.VIDEO_MP4,
            byte_size=size,
            ready_at=ready,
            expires_at=expires,
        )
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_capacity_evicts_oldest_first(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "s")
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    leases = FakeArtifactLeaseRegistry()
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    cap = CapacityManager(
        store=store,
        capacity_bytes=10_000,
        safety_headroom_bytes=1_000,
        unknown_size_initial_reservation_bytes=2_000,
        reservation_growth_bytes=1_000,
    )
    # logical free without artifacts: 10000 - 1000 headroom = 9000
    # two artifacts 4000 each → available 1000
    j0 = await _completed_job(jobs, "0" * 22, now, ready=now - timedelta(hours=2))
    j1 = await _completed_job(jobs, "1" * 22, now, ready=now - timedelta(hours=1))
    a0 = await _publish_art(
        store=store,
        arts=arts,
        job=j0,
        aid="A" * 22,
        key="S" * 22,
        size=4_000,
        ready=now - timedelta(hours=2),
        expires=now + timedelta(hours=12),
    )
    a1 = await _publish_art(
        store=store,
        arts=arts,
        job=j1,
        aid="B" * 22,
        key="T" * 22,
        size=4_000,
        ready=now - timedelta(hours=1),
        expires=now + timedelta(hours=12),
    )
    cap.set_artifact_bytes(8_000)
    evictor = CapacityEvictionService(
        capacity=cap, artifacts=arts, files=store, jobs=jobs, leases=leases
    )

    ok = await evictor.ensure_capacity(needed_bytes=3_000, now=now)
    assert ok is True
    deleted_artifact = await arts.get(a0.artifact_id)
    available_artifact = await arts.get(a1.artifact_id)
    assert deleted_artifact is not None
    assert available_artifact is not None
    assert deleted_artifact.access_state is ArtifactAccessState.DELETED
    assert available_artifact.access_state is ArtifactAccessState.AVAILABLE
    job0 = await jobs.get(j0.job_id)
    assert job0 is not None and job0.state is JobState.EVICTED
    snap = await cap.snapshot()
    assert snap.available_bytes >= 3_000


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_capacity_skips_leased_then_evicts_next(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "s")
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    leases = FakeArtifactLeaseRegistry()
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    cap = CapacityManager(
        store=store,
        capacity_bytes=10_000,
        safety_headroom_bytes=1_000,
        unknown_size_initial_reservation_bytes=2_000,
        reservation_growth_bytes=1_000,
    )
    j0 = await _completed_job(jobs, "0" * 22, now, ready=now - timedelta(hours=2))
    j1 = await _completed_job(jobs, "1" * 22, now, ready=now - timedelta(hours=1))
    a0 = await _publish_art(
        store=store,
        arts=arts,
        job=j0,
        aid="A" * 22,
        key="S" * 22,
        size=4_000,
        ready=now - timedelta(hours=2),
        expires=now + timedelta(hours=12),
    )
    a1 = await _publish_art(
        store=store,
        arts=arts,
        job=j1,
        aid="B" * 22,
        key="T" * 22,
        size=4_000,
        ready=now - timedelta(hours=1),
        expires=now + timedelta(hours=12),
    )
    cap.set_artifact_bytes(8_000)
    await leases.acquire(a0.artifact_id, LeaseKind.HTTP_STREAM, holder_id="h1")
    evictor = CapacityEvictionService(
        capacity=cap, artifacts=arts, files=store, jobs=jobs, leases=leases
    )

    ok = await evictor.ensure_capacity(needed_bytes=3_000, now=now)
    assert ok is True
    leased_artifact = await arts.get(a0.artifact_id)
    deleted_artifact = await arts.get(a1.artifact_id)
    assert leased_artifact is not None
    assert deleted_artifact is not None
    assert leased_artifact.access_state is ArtifactAccessState.AVAILABLE
    assert deleted_artifact.access_state is ArtifactAccessState.DELETED
    job1 = await jobs.get(j1.job_id)
    assert job1 is not None and job1.state is JobState.EVICTED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_capacity_all_leased_returns_false(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "s")
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    leases = FakeArtifactLeaseRegistry()
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    cap = CapacityManager(
        store=store,
        capacity_bytes=5_000,
        safety_headroom_bytes=1_000,
        unknown_size_initial_reservation_bytes=2_000,
        reservation_growth_bytes=1_000,
    )
    j0 = await _completed_job(jobs, "0" * 22, now, ready=now)
    a0 = await _publish_art(
        store=store,
        arts=arts,
        job=j0,
        aid="A" * 22,
        key="S" * 22,
        size=3_500,
        ready=now,
        expires=now + timedelta(hours=12),
    )
    cap.set_artifact_bytes(3_500)
    await leases.acquire(a0.artifact_id, LeaseKind.HTTP_STREAM, holder_id="h1")
    evictor = CapacityEvictionService(
        capacity=cap, artifacts=arts, files=store, jobs=jobs, leases=leases
    )

    ok = await evictor.ensure_capacity(needed_bytes=2_000, now=now)
    assert ok is False
    leased_artifact = await arts.get(a0.artifact_id)
    assert leased_artifact is not None
    assert leased_artifact.access_state is ArtifactAccessState.AVAILABLE
    with pytest.raises(DomainError):
        await cap.reserve(JobId("N" * 22), known_size=2_000, now=now)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reserve_succeeds_after_eviction(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "s")
    jobs = InMemoryJobRepository()
    arts = InMemoryArtifactRepository()
    leases = FakeArtifactLeaseRegistry()
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    cap = CapacityManager(
        store=store,
        capacity_bytes=10_000,
        safety_headroom_bytes=1_000,
        unknown_size_initial_reservation_bytes=3_000,
        reservation_growth_bytes=1_000,
    )
    j0 = await _completed_job(jobs, "0" * 22, now, ready=now - timedelta(hours=1))
    await _publish_art(
        store=store,
        arts=arts,
        job=j0,
        aid="A" * 22,
        key="S" * 22,
        size=7_000,
        ready=now - timedelta(hours=1),
        expires=now + timedelta(hours=12),
    )
    cap.set_artifact_bytes(7_000)
    # available = 10000 - 7000 - 1000 = 2000 < 3000 initial reserve
    evictor = CapacityEvictionService(
        capacity=cap, artifacts=arts, files=store, jobs=jobs, leases=leases
    )
    await evictor.ensure_capacity(needed_bytes=cap.unknown_size_initial, now=now)
    amount = await cap.reserve(JobId("N" * 22), known_size=None, now=now)
    assert amount == 3_000
