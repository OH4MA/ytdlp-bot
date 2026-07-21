"""Process-group media worker supervision."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from ytdlp_bot.adapters.media.worker_protocol import WorkerRequestMessage
from ytdlp_bot.domain.enums import WorkerPhase
from ytdlp_bot.domain.identity import JobId
from ytdlp_bot.ports.media import EventSink, WorkerEvent, WorkerRequest

WorkerRunner = Callable[[WorkerRequestMessage, EventSink], Awaitable[None]]


@dataclass
class ProcessWorkerSupervisor:
    """Spawn worker_entrypoint in a new session/process group per job."""

    python_executable: str = sys.executable
    fixture_mode: bool = False
    _procs: dict[str, asyncio.subprocess.Process] = field(default_factory=dict)
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _cancel: set[str] = field(default_factory=set)

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
        env = os.environ.copy()
        if self.fixture_mode:
            env["YTDLP_BOT_FIXTURE_WORKER"] = "1"
        # Minimal environment for security.
        env.pop("HTTP_PROXY", None)
        env.pop("HTTPS_PROXY", None)
        if request.proxy_url:
            env["ALL_PROXY"] = request.proxy_url

        proc = await asyncio.create_subprocess_exec(
            self.python_executable,
            "-m",
            "ytdlp_bot.adapters.media.worker_entrypoint",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
            cwd=str(Path(request.workspace_path).parent),
        )
        self._procs[request.job_id.value] = proc
        assert proc.stdin is not None
        line = msg.to_json_line() + "\n"
        proc.stdin.write(line.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        task = asyncio.create_task(self._pump(request.job_id.value, proc, sink))
        self._tasks[request.job_id.value] = task

    async def _pump(
        self,
        job_id: str,
        proc: asyncio.subprocess.Process,
        sink: EventSink,
    ) -> None:
        assert proc.stdout is not None
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                try:
                    data = json.loads(raw.decode("utf-8"))
                    phase = None
                    if data.get("phase"):
                        phase = WorkerPhase(str(data["phase"]))
                    await sink.emit(
                        WorkerEvent(
                            sequence=int(data.get("sequence", 0)),
                            job_id=JobId(job_id if len(job_id) >= 22 else job_id.ljust(22, "A")),
                            kind=str(data.get("type", "unknown")),
                            phase=phase,
                            payload=data.get("payload")
                            if isinstance(data.get("payload"), dict)
                            else {},
                        )
                    )
                except Exception:
                    continue
            await proc.wait()
        finally:
            self._procs.pop(job_id, None)
            self._tasks.pop(job_id, None)
            self._cancel.discard(job_id)

    async def request_cancel(self, job_id: JobId) -> None:
        self._cancel.add(job_id.value)
        proc = self._procs.get(job_id.value)
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, 15)  # SIGTERM process group

    async def force_terminate(self, job_id: JobId) -> None:
        proc = self._procs.get(job_id.value)
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, 9)  # SIGKILL
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=2)

    async def active_jobs(self) -> list[JobId]:
        out: list[JobId] = []
        for key in sorted(self._procs):
            try:
                out.append(JobId(key if len(key) >= 22 else key.ljust(22, "A")))
            except Exception:
                continue
        return out

    async def shutdown(self) -> None:
        for job_id in list(self._procs):
            await self.force_terminate(
                JobId(job_id if len(job_id) >= 22 else job_id.ljust(22, "A"))
            )
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)


# Backward-compatible alias used by existing tests (in-process fixture runner).
@dataclass
class InProcessWorkerSupervisor:
    """In-process runner for unit tests that inject a custom coroutine."""

    runner: WorkerRunner | None = None
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _cancel: set[str] = field(default_factory=set)

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
        runner = self.runner or default_fixture_runner
        task = asyncio.create_task(self._run(msg, sink, runner, request.job_id.value))
        self._tasks[request.job_id.value] = task

    async def _run(
        self,
        msg: WorkerRequestMessage,
        sink: EventSink,
        runner: WorkerRunner,
        job_id: str,
    ) -> None:
        try:
            await runner(msg, sink)
        finally:
            self._tasks.pop(job_id, None)
            self._cancel.discard(job_id)

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


async def default_fixture_runner(msg: WorkerRequestMessage, sink: EventSink) -> None:
    """Deterministic local fixture used by unit/integration tests."""
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
