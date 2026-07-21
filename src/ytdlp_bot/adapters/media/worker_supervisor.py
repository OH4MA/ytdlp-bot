"""In-process and subprocess media worker supervision."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from ytdlp_bot.adapters.media.worker_protocol import (
    WorkerRequestMessage,
)
from ytdlp_bot.domain.enums import WorkerPhase
from ytdlp_bot.domain.identity import JobId
from ytdlp_bot.ports.media import EventSink, MediaWorkerSupervisor, WorkerEvent, WorkerRequest

# Optional injectable runner for deterministic tests.
WorkerRunner = Callable[[WorkerRequestMessage, EventSink], Awaitable[None]]


@dataclass
class InProcessWorkerSupervisor:
    """Runs a worker coroutine in-process (tests and simple deployments)."""

    runner: WorkerRunner | None = None
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _cancel: set[str] = field(default_factory=set)
    _sinks: dict[str, EventSink] = field(default_factory=dict)

    async def start(self, request: WorkerRequest, sink: EventSink) -> None:
        msg = WorkerRequestMessage(
            job_id=request.job_id.value,
            source_url=request.source_url,
            mode=request.mode.value,
            video_quality=request.video_quality.value if request.video_quality else None,
            audio_bitrate=request.audio_bitrate.value if request.audio_bitrate else None,
            workspace_path=request.workspace_path,
            proxy_url=request.proxy_url,
            network_attempts=request.network_attempts,
            correlation_id=request.correlation_id,
            playlist_enabled=request.playlist_enabled,
        )
        self._sinks[request.job_id.value] = sink
        runner = self.runner or default_fixture_runner
        task = asyncio.create_task(self._run(msg, sink, runner))
        self._tasks[request.job_id.value] = task

    async def _run(self, msg: WorkerRequestMessage, sink: EventSink, runner: WorkerRunner) -> None:
        try:
            await runner(msg, sink)
        finally:
            self._tasks.pop(msg.job_id, None)
            self._cancel.discard(msg.job_id)

    async def request_cancel(self, job_id: JobId) -> None:
        self._cancel.add(job_id.value)
        task = self._tasks.get(job_id.value)
        if task is not None:
            task.cancel()

    async def force_terminate(self, job_id: JobId) -> None:
        await self.request_cancel(job_id)

    async def active_jobs(self) -> list[JobId]:
        return [JobId(k) for k in sorted(self._tasks)]

    async def shutdown(self) -> None:
        for job_id in list(self._tasks):
            await self.force_terminate(JobId(job_id))
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    def is_cancel_requested(self, job_id: str) -> bool:
        return job_id in self._cancel


async def default_fixture_runner(msg: WorkerRequestMessage, sink: EventSink) -> None:
    """Deterministic local fixture: write a small file and emit success events."""
    seq = 0

    async def emit(event_type: str, phase: str | None = None, **payload: object) -> None:
        nonlocal seq
        seq += 1
        await sink.emit(
            WorkerEvent(
                sequence=seq,
                job_id=JobId(msg.job_id),
                kind=event_type,
                phase=WorkerPhase(phase) if phase else None,
                payload=dict(payload),
            )
        )

    await emit("phase_changed", phase=WorkerPhase.INSPECTING.value)
    await emit("phase_changed", phase=WorkerPhase.DOWNLOADING.value)
    workspace = Path(msg.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    if msg.mode == "audio":
        out = workspace / "audio.mp3"
        out.write_bytes(b"ID3" + b"\x00" * 64)
        media_type = "audio/mpeg"
        name = "audio.mp3"
    else:
        out = workspace / "video.mp4"
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        media_type = "video/mp4"
        name = "video.mp4"
    await emit("phase_changed", phase=WorkerPhase.POST_PROCESSING.value)
    await emit(
        "artifact_candidate",
        phase=WorkerPhase.FINALIZING.value,
        path=str(out),
        display_name=name,
        media_type=media_type,
        byte_size=out.stat().st_size,
    )
    await emit("worker_succeeded", phase=WorkerPhase.FINALIZING.value)


# Satisfy protocol naming
_ = MediaWorkerSupervisor
