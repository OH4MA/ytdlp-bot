"""Cleanup and restart reconciliation helpers."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ytdlp_bot.domain.enums import ArtifactAccessState, DeletionReason, JobState
from ytdlp_bot.domain.identity import ArtifactId, JobId
from ytdlp_bot.domain.jobs import Artifact, Job
from ytdlp_bot.ports.results import Ok


class JobStore(Protocol):
    async def get(self, job_id: JobId) -> Job | None: ...
    async def transition(
        self, job_id: JobId, *, expected_version: int, new_state: JobState, error_code=None
    ): ...


class ArtifactStoreRepo(Protocol):
    async def list_expired(self, *, now: datetime, limit: int): ...
    async def mark_deletion_pending(
        self,
        artifact_id: ArtifactId,
        *,
        expected_version: int,
        reason: DeletionReason,
        now: datetime,
    ): ...
    async def finish_deletion(
        self, artifact_id: ArtifactId, *, expected_version: int, now: datetime
    ): ...
    async def get(self, artifact_id: ArtifactId) -> Artifact | None: ...


class FileStore(Protocol):
    async def delete(self, storage_key: str) -> None: ...
    async def delete_workspace(self, job_id: JobId) -> None: ...


class PayloadStore(Protocol):
    async def delete(self, job_id: JobId) -> None: ...


@dataclass
class CleanupService:
    jobs: JobStore
    artifacts: ArtifactStoreRepo
    files: FileStore
    payloads: PayloadStore

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

    async def reconcile_interrupted_job(self, job: Job, *, now: datetime) -> JobState:
        """Mark active work cancelled_by_restart and drop payload/workspace."""
        if job.state in {
            JobState.QUEUED,
            JobState.INSPECTING,
            JobState.DOWNLOADING,
            JobState.POST_PROCESSING,
            JobState.ARCHIVING,
            JobState.CANCELLING,
        }:
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
