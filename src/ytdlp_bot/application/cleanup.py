"""Cleanup and restart reconciliation helpers."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from ytdlp_bot.domain.enums import ArtifactAccessState, DeletionReason, JobState
from ytdlp_bot.domain.identity import ArtifactId, JobId
from ytdlp_bot.domain.jobs import Artifact, Job
from ytdlp_bot.ports.results import Ok


class JobStore(Protocol):
    async def get(self, job_id: JobId) -> Job | None: ...
    async def transition(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        new_state: JobState,
        error_code: object = None,
    ) -> object: ...


class ArtifactStoreRepo(Protocol):
    async def list_expired(self, *, now: datetime, limit: int) -> object: ...
    async def mark_deletion_pending(
        self,
        artifact_id: ArtifactId,
        *,
        expected_version: int,
        reason: DeletionReason,
        now: datetime,
    ) -> object: ...
    async def finish_deletion(
        self, artifact_id: ArtifactId, *, expected_version: int, now: datetime
    ) -> object: ...
    async def get(self, artifact_id: ArtifactId) -> Artifact | None: ...
    async def list_deletion_pending(self, *, limit: int = 50) -> object: ...


class FileStore(Protocol):
    async def delete(self, storage_key: str) -> None: ...
    async def delete_workspace(self, job_id: JobId) -> None: ...
    async def exists(self, storage_key: str) -> bool: ...


class PayloadStore(Protocol):
    async def delete(self, job_id: JobId) -> None: ...


class CapacityEvictor(Protocol):
    async def ensure_capacity(self, *, needed_bytes: int, now: datetime) -> object: ...


@dataclass
class CleanupService:
    jobs: JobStore
    artifacts: ArtifactStoreRepo
    files: FileStore
    payloads: PayloadStore
    capacity: CapacityEvictor | None = None
    _running: bool = False
    last_run_at: datetime | None = None
    last_error: str | None = None
    _pending_backoff: dict[str, datetime] = field(default_factory=dict)

    async def run_once(self, *, now: datetime, limit: int = 50) -> dict[str, int]:
        """Single non-overlapping cleanup pass."""
        if self._running:
            return {"skipped": 1}
        self._running = True
        stats = {"expired": 0, "pending_retries": 0, "reconciled": 0}
        try:
            stats["pending_retries"] = await self.retry_pending_deletions(now=now, limit=limit)
            stats["expired"] = await self.expire_due_artifacts(now=now, limit=limit)
            if self.capacity is not None:
                with contextlib.suppress(Exception):
                    await self.capacity.ensure_capacity(needed_bytes=0, now=now)
            self.last_run_at = now
            self.last_error = None
        except Exception as exc:
            self.last_error = type(exc).__name__
        finally:
            self._running = False
        return stats

    async def expire_due_artifacts(self, *, now: datetime, limit: int = 50) -> int:
        expired = await self.artifacts.list_expired(now=now, limit=limit)
        count = 0
        for art in expired:
            if art.access_state is not ArtifactAccessState.AVAILABLE:
                continue
            pending = await self.artifacts.mark_deletion_pending(
                art.artifact_id,
                expected_version=art.version,
                reason=DeletionReason.EXPIRED,
                now=now,
            )
            if not isinstance(pending, Ok):
                continue
            try:
                await self.files.delete(art.storage_key)
            except Exception:
                continue
            fin = await self.artifacts.finish_deletion(
                art.artifact_id,
                expected_version=pending.value.version,
                now=now,
            )
            if isinstance(fin, Ok):
                job = await self.jobs.get(art.job_id)
                if job is not None and job.state in {
                    JobState.COMPLETED,
                    JobState.COMPLETED_WITH_ERRORS,
                    JobState.FAILED,
                }:
                    await self.jobs.transition(
                        art.job_id,
                        expected_version=job.version,
                        new_state=JobState.EXPIRED,
                    )
                count += 1
        return count

    async def retry_pending_deletions(self, *, now: datetime, limit: int = 50) -> int:
        if not hasattr(self.artifacts, "list_deletion_pending"):
            return 0
        pending = await self.artifacts.list_deletion_pending(limit=limit)
        count = 0
        for art in pending:
            key = art.artifact_id.value
            until = self._pending_backoff.get(key)
            if until is not None and now < until:
                continue
            try:
                await self.files.delete(art.storage_key)
            except Exception:
                # Exponential-ish backoff capped at 1 hour (simple doubling via version).
                delay = min(3600, 2 ** min(art.version, 12))
                self._pending_backoff[key] = now + timedelta(seconds=delay)
                continue
            fin = await self.artifacts.finish_deletion(
                art.artifact_id,
                expected_version=art.version,
                now=now,
            )
            if isinstance(fin, Ok):
                self._pending_backoff.pop(key, None)
                count += 1
        return count

    async def reconcile_interrupted_job(self, job: Job, *, now: datetime) -> JobState:
        """Mark active work cancelled_by_restart and drop payload/workspace."""
        _ = now
        if job.state in {
            JobState.QUEUED,
            JobState.INSPECTING,
            JobState.DOWNLOADING,
            JobState.POST_PROCESSING,
            JobState.ARCHIVING,
            JobState.CANCELLING,
            JobState.DELIVERING,
        }:
            # Preserve only acknowledged dispatchable queued jobs.
            if (
                job.state is JobState.QUEUED
                and job.dispatchable
                and job.acknowledged_at is not None
            ):
                return job.state
            result = await self.jobs.transition(
                job.job_id,
                expected_version=job.version,
                new_state=JobState.CANCELLED_BY_RESTART,
            )
            await self.payloads.delete(job.job_id)
            with contextlib.suppress(Exception):
                await self.files.delete_workspace(job.job_id)
            if isinstance(result, Ok):
                return result.value.state  # type: ignore[return-value]
        return job.state

    async def reconcile_artifact_file(self, art: Artifact, *, now: datetime) -> bool:
        """Return True if artifact remains available."""
        exists = True
        if hasattr(self.files, "exists"):
            exists = await self.files.exists(art.storage_key)
        if exists:
            return True
        pending = await self.artifacts.mark_deletion_pending(
            art.artifact_id,
            expected_version=art.version,
            reason=DeletionReason.RECONCILIATION,
            now=now,
        )
        if not isinstance(pending, Ok):
            return False
        await self.artifacts.finish_deletion(
            art.artifact_id,
            expected_version=pending.value.version,
            now=now,
        )
        job = await self.jobs.get(art.job_id)
        if job is not None and job.state in {
            JobState.COMPLETED,
            JobState.COMPLETED_WITH_ERRORS,
        }:
            await self.jobs.transition(
                art.job_id,
                expected_version=job.version,
                new_state=JobState.EXPIRED,
            )
        return False


@dataclass
class ScheduledCleanupRunner:
    """Periodic cleanup with overlap protection."""

    service: CleanupService
    interval: timedelta = timedelta(minutes=5)
    now_fn: Callable[[], datetime] | None = None
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            now = self.now_fn() if self.now_fn else datetime.now().astimezone()
            await self.service.run_once(now=now)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval.total_seconds())
