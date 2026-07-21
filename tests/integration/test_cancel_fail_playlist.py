"""Cancel, worker fail, and playlist partial archive paths."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.fakes import (
    DeterministicIdGenerator,
    FakeClock,
    FakeDnsResolver,
    FakePlatformPort,
    FakeUrlPreflightClient,
    InMemoryAccessRepository,
    InMemoryArtifactRepository,
    InMemoryJobPayloadRepository,
    InMemoryJobRepository,
)
from ytdlp_bot.adapters.media.worker_protocol import WorkerRequestMessage
from ytdlp_bot.adapters.media.worker_supervisor import InProcessWorkerSupervisor
from ytdlp_bot.adapters.security.signed_tokens import DownloadLinkIssuer, HmacTokenSigner
from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore
from ytdlp_bot.application.authorization import AuthorizationService
from ytdlp_bot.application.delivery import DeliveryService
from ytdlp_bot.application.job_service import JobService
from ytdlp_bot.application.orchestrator import Orchestrator
from ytdlp_bot.application.playlist import PlaylistEntryRecord, PlaylistOrchestrator
from ytdlp_bot.application.progress_reporter import ProgressReporter
from ytdlp_bot.application.url_safety import UrlSafetyService
from ytdlp_bot.domain.commands import CancelArgs, YtdlArgs
from ytdlp_bot.domain.enums import (
    AccessMode,
    JobState,
    MediaMode,
    Platform,
    VideoQuality,
)
from ytdlp_bot.domain.identity import Identity, MessageContext
from ytdlp_bot.ports.media import EventSink, WorkerEvent, WorkerRequest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fail_and_cancel_and_playlist(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    ids = DeterministicIdGenerator()
    jobs = InMemoryJobRepository()
    payloads = InMemoryJobPayloadRepository()
    arts = InMemoryArtifactRepository()
    access = InMemoryAccessRepository(AccessMode.ALLOW_ALL)
    platform = FakePlatformPort()
    auth = AuthorizationService(
        access=access, jobs=jobs, artifacts=arts, administrators=frozenset()
    )
    dns = FakeDnsResolver()
    dns.map("example.com", "8.8.8.8")
    job_svc = JobService(
        auth=auth,
        url_safety=UrlSafetyService(
            dns=dns, preflight=FakeUrlPreflightClient(), allowed_ports=frozenset({80, 443})
        ),
        jobs=jobs,
        payloads=payloads,
        platform=platform,
        clock=clock,
        ids=ids,
    )
    identity = Identity(platform=Platform.TELEGRAM, user_id="1")
    ctx = MessageContext(
        platform=Platform.TELEGRAM,
        chat_id="1",
        response_target="1",
        effective_upload_limit_bytes=50_000_000,
    )
    accepted = await job_svc.submit_download(
        request_id="f1",
        identity=identity,
        context=ctx,
        args=YtdlArgs(url="https://example.com/v", quality=VideoQuality.BEST),
    )
    from ytdlp_bot.domain.commands import AcceptedJob

    assert isinstance(accepted, AcceptedJob)
    job_id = accepted.job_id
    store = LocalArtifactStore(tmp_path / "store")
    signer = HmacTokenSigner(b"S" * 32, public_base_url="https://dl.example.invalid")
    delivery = DeliveryService(
        platform=platform,
        link_issuer=DownloadLinkIssuer(signer),
        link_lifetime_seconds=3600,
    )
    orch = Orchestrator(
        jobs=jobs,
        artifacts=arts,
        payloads=payloads,
        store=store,
        delivery=delivery,
        progress=ProgressReporter(edit_progress=platform.edit_progress),
        ids=ids,
        retention_seconds=43200,
        now_fn=clock.now,
    )

    class Sink:
        async def emit(self, event: WorkerEvent) -> None:
            await orch.handle_event(event)

    async def fail_runner(msg: WorkerRequestMessage, sink: EventSink) -> None:
        await sink.emit(
            WorkerEvent(
                sequence=1,
                job_id=job_id,
                kind="worker_failed",
                phase=None,
                payload={"error_code": "DOWNLOAD_FAILED"},
            )
        )

    sup = InProcessWorkerSupervisor(runner=fail_runner)
    ws = await store.create_job_workspace(job_id)
    await sup.start(
        WorkerRequest(
            job_id=job_id,
            source_url="https://example.com/v",
            mode=MediaMode.VIDEO,
            video_quality=VideoQuality.BEST,
            audio_bitrate=None,
            workspace_path=ws,
            proxy_url=None,
            network_attempts=1,
            correlation_id="c",
        ),
        Sink(),
    )
    for _ in range(50):
        if not await sup.active_jobs():
            break
        await asyncio.sleep(0.01)
    failed = await jobs.get(job_id)
    assert failed is not None and failed.state is JobState.FAILED
    assert await payloads.get(job_id) is None

    # Queued cancellation
    accepted2 = await job_svc.submit_download(
        request_id="c1",
        identity=identity,
        context=ctx,
        args=YtdlArgs(url="https://example.com/v2", quality=VideoQuality.BEST),
    )
    assert isinstance(accepted2, AcceptedJob)
    cancel = await job_svc.cancel(identity=identity, args=CancelArgs(job_id=accepted2.job_id))
    assert cancel.kind == "status"
    job2 = await jobs.get(accepted2.job_id)
    assert job2 is not None
    assert job2.state is JobState.CANCELLED
    assert await payloads.get(accepted2.job_id) is None

    # Playlist partial ZIP (application bookkeeping + adapter archive writer)
    from ytdlp_bot.adapters.media.archive import ArchiveEntry, write_playlist_zip

    f1 = tmp_path / "e1.mp4"
    f1.write_bytes(b"ok")
    pl = PlaylistOrchestrator(
        job_id=job_id,
        entries=[
            PlaylistEntryRecord(1, "id1", "One"),
            PlaylistEntryRecord(2, "id2", "Two"),
            PlaylistEntryRecord(3, "id3", "Three"),
        ],
    )
    pl.mark_downloading(1)
    pl.mark_succeeded(1, path=str(f1), title="One")
    pl.mark_downloading(2)
    pl.mark_failed(2, error_code="DOWNLOAD_FAILED")
    pl.mark_downloading(3)
    pl.mark_failed(3, error_code="POST_PROCESSING_FAILED")
    assert pl.outcome_kind() == "partial"
    zpath = write_playlist_zip(
        tmp_path / "pl.zip",
        [
            ArchiveEntry(e.index, Path(e.local_path or ""), e.title, e.extension)
            for e in pl.successful_entries()
        ],
        failures=pl.failure_rows(),
        total=len(pl.entries),
    )
    assert zpath.is_file()
    import zipfile

    with zipfile.ZipFile(zpath) as zf:
        assert "FAILURES.txt" in zf.namelist()
        assert any(n.endswith(".mp4") for n in zf.namelist())
