"""Process worker supervisor with fixture mode."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ytdlp_bot.adapters.media.worker_supervisor import ProcessWorkerSupervisor
from ytdlp_bot.domain.enums import MediaMode, VideoQuality
from ytdlp_bot.domain.identity import JobId
from ytdlp_bot.ports.media import WorkerEvent, WorkerRequest


class Sink:
    def __init__(self) -> None:
        self.events: list[WorkerEvent] = []

    async def emit(self, event: WorkerEvent) -> None:
        self.events.append(event)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_process_worker_fixture_mode(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    sup = ProcessWorkerSupervisor(fixture_mode=True)
    sink = Sink()
    jid = JobId("J" * 22)
    await sup.start(
        WorkerRequest(
            job_id=jid,
            source_url="https://example.com/v",
            mode=MediaMode.VIDEO,
            video_quality=VideoQuality.BEST,
            audio_bitrate=None,
            workspace_path=str(ws),
            proxy_url=None,
            network_attempts=1,
            correlation_id="c",
        ),
        sink,
    )
    for _ in range(200):
        if not await sup.active_jobs():
            break
        await asyncio.sleep(0.05)
    kinds = [e.kind for e in sink.events]
    assert (
        "artifact_candidate" in kinds or "worker_succeeded" in kinds or (ws / "video.mp4").exists()
    )
    await sup.shutdown()
