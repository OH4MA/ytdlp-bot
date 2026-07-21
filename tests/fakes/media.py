"""Fake media worker supervisor."""

from __future__ import annotations

from dataclasses import dataclass, field

from ytdlp_bot.domain.identity import JobId
from ytdlp_bot.ports.media import EventSink, WorkerEvent, WorkerRequest


@dataclass
class FakeMediaWorker:
    started: list[WorkerRequest] = field(default_factory=list)
    cancelled: list[JobId] = field(default_factory=list)
    terminated: list[JobId] = field(default_factory=list)
    _active: set[str] = field(default_factory=set)
    _sinks: dict[str, EventSink] = field(default_factory=dict)

    def reset(self) -> None:
        self.started.clear()
        self.cancelled.clear()
        self.terminated.clear()
        self._active.clear()
        self._sinks.clear()

    async def start(self, request: WorkerRequest, sink: EventSink) -> None:
        self.started.append(request)
        self._active.add(request.job_id.value)
        self._sinks[request.job_id.value] = sink

    async def request_cancel(self, job_id: JobId) -> None:
        self.cancelled.append(job_id)

    async def force_terminate(self, job_id: JobId) -> None:
        self.terminated.append(job_id)
        self._active.discard(job_id.value)

    async def active_jobs(self) -> list[JobId]:
        return [JobId(v) for v in sorted(self._active)]

    async def shutdown(self) -> None:
        self._active.clear()

    async def emit(self, job_id: JobId, event: WorkerEvent) -> None:
        sink = self._sinks.get(job_id.value)
        if sink is not None:
            await sink.emit(event)
