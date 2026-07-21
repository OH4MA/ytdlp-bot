"""Worker event orchestration: progress, publication, delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from ytdlp_bot.application.delivery import DeliveryService
from ytdlp_bot.application.progress_reporter import ProgressReporter
from ytdlp_bot.domain.enums import JobState, MediaType, WorkerPhase
from ytdlp_bot.domain.identity import ArtifactId, JobId
from ytdlp_bot.domain.jobs import Artifact, Job
from ytdlp_bot.domain.progress import ProgressSnapshot, progress_from_worker_values
from ytdlp_bot.ports.media import WorkerEvent
from ytdlp_bot.ports.results import Ok
from ytdlp_bot.ports.system import IdGenerator


class JobMut(Protocol):
    async def get(self, job_id: JobId) -> Job | None: ...
    async def transition(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        new_state: JobState,
        error_code: Any = None,
    ) -> Any: ...
    async def update_progress_snapshot(
        self, job_id: JobId, *, expected_version: int, progress: ProgressSnapshot
    ) -> Any: ...


class ArtifactMut(Protocol):
    async def create_available(self, artifact: Artifact) -> Artifact: ...


class PayloadMut(Protocol):
    async def delete(self, job_id: JobId) -> None: ...


class StoreMut(Protocol):
    async def atomically_publish(self, source_path: str, storage_key: str) -> None: ...


@dataclass
class Orchestrator:
    jobs: JobMut
    artifacts: ArtifactMut
    payloads: PayloadMut
    store: StoreMut
    delivery: DeliveryService
    progress: ProgressReporter
    ids: IdGenerator
    retention_seconds: int
    now_fn: object  # Callable[[], datetime]

    async def handle_event(self, event: WorkerEvent) -> None:
        job = await self.jobs.get(event.job_id)
        if job is None:
            return
        if event.sequence <= job.last_event_sequence:
            return
        now: datetime = self.now_fn()  # type: ignore[operator]

        if event.kind == "phase_changed" and event.phase is not None:
            mapping = {
                WorkerPhase.INSPECTING: JobState.INSPECTING,
                WorkerPhase.DOWNLOADING: JobState.DOWNLOADING,
                WorkerPhase.POST_PROCESSING: JobState.POST_PROCESSING,
                WorkerPhase.ARCHIVING: JobState.ARCHIVING,
                WorkerPhase.FINALIZING: JobState.DELIVERING,
            }
            target = mapping.get(event.phase)
            if (
                target
                and target is not job.state
                and job.state
                not in {
                    JobState.COMPLETED,
                    JobState.FAILED,
                    JobState.CANCELLED,
                }
            ):
                await self.jobs.transition(
                    job.job_id, expected_version=job.version, new_state=target
                )
                job = await self.jobs.get(job.job_id) or job

        if event.kind == "progress_changed" and event.payload:
            snap = progress_from_worker_values(
                phase=event.phase,
                downloaded_bytes=event.payload.get("downloaded_bytes"),  # type: ignore[arg-type]
                total_bytes=event.payload.get("total_bytes"),  # type: ignore[arg-type]
                speed_bytes_per_second=event.payload.get("speed"),  # type: ignore[arg-type]
                eta_seconds=event.payload.get("eta"),  # type: ignore[arg-type]
                playlist_completed=None,
                playlist_total=None,
                current_entry_index=None,
                current_entry_title=None,
                updated_at=now,
                source_sequence=event.sequence,
            )
            await self.jobs.update_progress_snapshot(
                job.job_id, expected_version=job.version, progress=snap
            )
            job = await self.jobs.get(job.job_id) or job
            if job.message_reference is not None:
                await self.progress.on_progress(
                    job_id=job.job_id,
                    state=job.state,
                    message_reference=job.message_reference,
                    progress=snap,
                    now=now,
                )

        if event.kind == "artifact_candidate" and event.payload:
            path = str(event.payload["path"])
            display = str(event.payload.get("display_name", "artifact.bin"))
            media = str(event.payload.get("media_type", "video/mp4"))
            raw_size = event.payload.get("byte_size")
            size = int(raw_size) if isinstance(raw_size, (int, str)) else Path(path).stat().st_size
            key = self.ids.storage_key()
            await self.store.atomically_publish(path, key)
            art = Artifact(
                artifact_id=ArtifactId(self.ids.artifact_id()),
                job_id=job.job_id,
                storage_key=key,
                display_name=display,
                media_type=MediaType(media),
                byte_size=size,
                ready_at=now,
                expires_at=now + timedelta(seconds=self.retention_seconds),
            )
            art = await self.artifacts.create_available(art)
            await self.payloads.delete(job.job_id)
            tr = await self.jobs.transition(
                job.job_id, expected_version=job.version, new_state=JobState.DELIVERING
            )
            if isinstance(tr, Ok):
                job = tr.value
            if job.message_reference is not None:
                await self.delivery.deliver(
                    job_id=job.job_id,
                    artifact=art,
                    context=job.message_context,
                    message_reference=job.message_reference,
                    now=now,
                )
                current = await self.jobs.get(job.job_id)
                if current is not None:
                    await self.jobs.transition(
                        job.job_id,
                        expected_version=current.version,
                        new_state=JobState.COMPLETED,
                    )
                self.progress.mark_terminal(job.job_id)

        if event.kind == "worker_failed":
            await self.payloads.delete(job.job_id)
            await self.jobs.transition(
                job.job_id,
                expected_version=job.version,
                new_state=JobState.FAILED,
            )
            self.progress.mark_terminal(job.job_id)

        if event.kind == "worker_cancelled":
            await self.payloads.delete(job.job_id)
            await self.jobs.transition(
                job.job_id,
                expected_version=job.version,
                new_state=JobState.CANCELLED,
            )
            self.progress.mark_terminal(job.job_id)
