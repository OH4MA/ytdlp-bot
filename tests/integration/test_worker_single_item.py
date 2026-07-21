"""Controlled single-item worker fixture: success path events."""

from __future__ import annotations

from pathlib import Path

import pytest

from ytdlp_bot.adapters.media.worker_supervisor import InProcessWorkerSupervisor
from ytdlp_bot.domain.enums import MediaMode, VideoQuality
from ytdlp_bot.domain.identity import JobId
from ytdlp_bot.ports.media import WorkerEvent, WorkerRequest


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[WorkerEvent] = []

    async def emit(self, event: WorkerEvent) -> None:
        self.events.append(event)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fixture_worker_success(tmp_path: Path) -> None:
    sup = InProcessWorkerSupervisor()
    sink = RecordingSink()
    ws = tmp_path / "ws"
    ws.mkdir()
    await sup.start(
        WorkerRequest(
            job_id=JobId("J" * 22),
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
    # Wait for task completion.
    import asyncio

    for _ in range(50):
        if not await sup.active_jobs():
            break
        await asyncio.sleep(0.01)
    kinds = [e.kind for e in sink.events]
    assert "artifact_candidate" in kinds
    assert "worker_succeeded" in kinds
    assert any((ws / "video.mp4").exists() for _ in [0])
