"""FND-08/09/10/13/14/15: port fakes and contract smoke tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.fakes import (
    DeterministicIdGenerator,
    FakeArtifactLeaseRegistry,
    FakeClock,
    FakeDnsResolver,
    FakeMediaWorker,
    FakePlatformPort,
    FakeUrlPreflightClient,
    InMemoryAccessRepository,
    InMemoryArtifactRepository,
    InMemoryCapacityRepository,
    InMemoryJobPayloadRepository,
    InMemoryJobRepository,
    InMemoryNotificationOutboxRepository,
    InMemorySettingsRepository,
    TemporaryArtifactStore,
)
from ytdlp_bot.domain.enums import (
    AccessMode,
    DeletionReason,
    JobState,
    LeaseKind,
    MediaMode,
    MediaType,
    Platform,
    UploadOutcome,
    VideoQuality,
)
from ytdlp_bot.domain.identity import ArtifactId, Identity, JobId, MessageContext
from ytdlp_bot.domain.jobs import Artifact, Job, JobPayload
from ytdlp_bot.ports.media import WorkerRequest
from ytdlp_bot.ports.results import Conflict, Ok


def _job(job_id: str, owner: Identity) -> Job:
    return Job(
        job_id=JobId(job_id),
        idempotency_key=f"{owner.platform.value}:evt",
        owner=owner,
        message_context=MessageContext(
            platform=owner.platform,
            chat_id="1",
            response_target="1",
            effective_upload_limit_bytes=1000,
        ),
        request_mode=MediaMode.VIDEO,
        selected_preset="best",
        source_display="https://example.com",
        state=JobState.QUEUED,
        dispatchable=True,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.contract
@pytest.mark.asyncio
async def test_job_repo_optimistic_conflict() -> None:
    repo = InMemoryJobRepository()
    owner = Identity(platform=Platform.TELEGRAM, user_id="1")
    jid = "J" * 22
    await repo.create(_job(jid, owner))
    result = await repo.transition(JobId(jid), expected_version=99, new_state=JobState.FAILED)
    assert isinstance(result, Conflict)
    ok = await repo.transition(JobId(jid), expected_version=1, new_state=JobState.INSPECTING)
    assert isinstance(ok, Ok)
    assert ok.value.version == 2


@pytest.mark.contract
@pytest.mark.asyncio
async def test_payload_and_claim() -> None:
    jobs = InMemoryJobRepository()
    payloads = InMemoryJobPayloadRepository()
    owner = Identity(platform=Platform.TELEGRAM, user_id="1")
    jid = "J" * 22
    job = _job(jid, owner)
    await jobs.create(job)
    await payloads.put(
        JobPayload(
            job_id=JobId(jid),
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
    assert (await payloads.get(JobId(jid))) is not None


@pytest.mark.contract
@pytest.mark.asyncio
async def test_artifact_repo_and_token_version() -> None:
    repo = InMemoryArtifactRepository()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    art = Artifact(
        artifact_id=ArtifactId("A" * 22),
        job_id=JobId("J" * 22),
        storage_key="S" * 22,
        display_name="a.mp4",
        media_type=MediaType.VIDEO_MP4,
        byte_size=10,
        ready_at=now,
        expires_at=now + timedelta(hours=1),
    )
    await repo.create_available(art)
    r = await repo.mark_deletion_pending(
        ArtifactId("A" * 22),
        expected_version=1,
        reason=DeletionReason.EXPIRED,
        now=now,
    )
    assert isinstance(r, Ok)
    assert r.value.token_version == 2
    assert r.value.access_state.value == "deletion_pending"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_settings_access_capacity() -> None:
    settings = InMemorySettingsRepository(defaults={"capacity_bytes": 1000})
    access = InMemoryAccessRepository()
    capacity = InMemoryCapacityRepository()
    owner = Identity(platform=Platform.TELEGRAM, user_id="1")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    await settings.set_override("capacity_bytes", 2000, updated_by=owner, now=now)
    assert (await settings.effective_values())["capacity_bytes"] == 2000
    await access.set_mode(AccessMode.WHITELIST, updated_by=owner, now=now)
    assert await access.add_identity(owner, now=now) is True
    assert await access.add_identity(owner, now=now) is False
    await capacity.reserve(JobId("J" * 22), 100, now=now)
    assert await capacity.sum_reservations() == 100


@pytest.mark.contract
@pytest.mark.asyncio
async def test_outbox_rejects_signed_url_key() -> None:
    outbox = InMemoryNotificationOutboxRepository()
    with pytest.raises(ValueError):
        await outbox.enqueue(
            job_id=JobId("J" * 22),
            kind="final",
            payload={"signed_url": "https://evil/secret"},
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )


@pytest.mark.contract
@pytest.mark.asyncio
async def test_platform_media_network_fakes() -> None:
    platform = FakePlatformPort()
    media = FakeMediaWorker()
    dns = FakeDnsResolver()
    pre = FakeUrlPreflightClient()
    ctx = MessageContext(
        platform=Platform.TELEGRAM,
        chat_id="1",
        response_target="1",
        effective_upload_limit_bytes=10,
    )
    ref = await platform.acknowledge_job(ctx, JobId("J" * 22), JobState.QUEUED)
    assert ref.message_id == "1"
    platform.upload_outcome = UploadOutcome.TOO_LARGE

    class Sink:
        async def emit(self, event) -> None:
            return None

    await media.start(
        WorkerRequest(
            job_id=JobId("J" * 22),
            source_url="https://example.com",
            mode=MediaMode.VIDEO,
            video_quality=VideoQuality.BEST,
            audio_bitrate=None,
            workspace_path="/tmp/x",
            proxy_url=None,
            network_attempts=3,
            correlation_id="c",
        ),
        Sink(),
    )
    assert await media.active_jobs()
    dns.map("example.com", "203.0.113.1")
    resolved = await dns.resolve("example.com")
    assert resolved.addresses == ("203.0.113.1",)
    assert (await pre.preflight("https://example.com")).allowed


@pytest.mark.contract
@pytest.mark.asyncio
async def test_clock_ids_store_leases(tmp_path: Path) -> None:
    clock = FakeClock()
    ids = DeterministicIdGenerator()
    t0 = clock.now()
    clock.advance(timedelta(seconds=5))
    assert clock.now() == t0 + timedelta(seconds=5)
    assert clock.monotonic() == 5.0
    j1 = ids.job_id()
    j2 = ids.job_id()
    assert j1 != j2
    assert len(j1) >= 22

    store = TemporaryArtifactStore(tmp_path / "store")
    jid = JobId(j1 if len(j1) >= 22 else "J" * 22)
    # Ensure valid job id for store
    jid = JobId("J" * 22)
    ws = await store.create_job_workspace(jid)
    file_path = Path(ws) / "out.bin"
    file_path.write_bytes(b"hello")
    key = "S" * 22
    await store.atomically_publish(str(file_path), key)
    st = await store.stat(key)
    assert st.size == 5

    leases = FakeArtifactLeaseRegistry()
    aid = ArtifactId("A" * 22)
    assert await leases.acquire(aid, LeaseKind.HTTP_STREAM, holder_id="h1")
    assert await leases.has_active_leases(aid)
    await leases.release(aid, LeaseKind.HTTP_STREAM, holder_id="h1")
    assert not await leases.has_active_leases(aid)
