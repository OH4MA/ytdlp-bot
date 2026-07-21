"""Single-item path: submit → worker fixture → publish → deliver signed/direct."""

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
from ytdlp_bot.adapters.media.worker_supervisor import InProcessWorkerSupervisor
from ytdlp_bot.adapters.security.signed_tokens import DownloadLinkIssuer, HmacTokenSigner
from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore
from ytdlp_bot.application.authorization import AuthorizationService
from ytdlp_bot.application.delivery import DeliveryService
from ytdlp_bot.application.job_service import JobService
from ytdlp_bot.application.orchestrator import Orchestrator
from ytdlp_bot.application.progress_reporter import ProgressReporter
from ytdlp_bot.application.url_safety import UrlSafetyService
from ytdlp_bot.domain.commands import YtdlArgs
from ytdlp_bot.domain.enums import AccessMode, JobState, MediaMode, Platform, VideoQuality
from ytdlp_bot.domain.identity import Identity, MessageContext
from ytdlp_bot.ports.media import WorkerRequest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_single_item_success_pipeline(tmp_path: Path) -> None:
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
            dns=dns,
            preflight=FakeUrlPreflightClient(),
            allowed_ports=frozenset({80, 443}),
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
        request_id="e1",
        identity=identity,
        context=ctx,
        args=YtdlArgs(url="https://example.com/v", quality=VideoQuality.BEST),
    )
    from ytdlp_bot.domain.commands import AcceptedJob

    assert isinstance(accepted, AcceptedJob)
    job_id = accepted.job_id
    job = await jobs.get(job_id)
    assert job is not None and job.dispatchable

    store = LocalArtifactStore(tmp_path / "store")
    ws = await store.create_job_workspace(job_id)
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
        async def emit(self, event) -> None:
            await orch.handle_event(event)

    sup = InProcessWorkerSupervisor()
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
    for _ in range(100):
        if not await sup.active_jobs():
            break
        await asyncio.sleep(0.01)

    final = await jobs.get(job_id)
    assert final is not None
    assert final.state is JobState.COMPLETED
    art = await arts.get_for_job(job_id)
    assert art is not None
    assert await payloads.get(job_id) is None
    assert any(c[0] == "send_final" for c in platform.calls)
