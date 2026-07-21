"""Oldest-first capacity reclamation under storage pressure.

When available capacity is insufficient for a new reservation or growth step,
expired artifacts are removed first, then non-expired available artifacts by
``ready_at`` ascending. Artifacts with active HTTP stream or platform upload
leases are skipped. If eligible bytes cannot free enough space, the caller
still receives ``INSUFFICIENT_CAPACITY`` from reserve/grow.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ytdlp_bot.domain.enums import ArtifactAccessState, DeletionReason, FailureCode, JobState
from ytdlp_bot.domain.identity import ArtifactId, JobId
from ytdlp_bot.domain.jobs import Artifact, Job
from ytdlp_bot.ports.results import Ok, Result

log = logging.getLogger("ytdlp_bot.capacity_eviction")


class CapacityAccounting(Protocol):
    async def snapshot(self, *, workspace_bytes: int = 0) -> object: ...

    def add_artifact_bytes(self, delta: int) -> None: ...


class ArtifactRepo(Protocol):
    async def list_expired(self, *, now: datetime, limit: int) -> Sequence[Artifact]: ...

    async def list_eviction_candidates(
        self, *, now: datetime, limit: int
    ) -> Sequence[Artifact]: ...

    async def mark_deletion_pending(
        self,
        artifact_id: ArtifactId,
        *,
        expected_version: int,
        reason: DeletionReason,
        now: datetime,
    ) -> Result[Artifact]: ...

    async def finish_deletion(
        self,
        artifact_id: ArtifactId,
        *,
        expected_version: int,
        now: datetime,
    ) -> Result[Artifact]: ...


class FileStore(Protocol):
    async def delete(self, storage_key: str) -> None: ...


class JobRepo(Protocol):
    async def get(self, job_id: JobId) -> Job | None: ...

    async def transition(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        new_state: JobState,
        error_code: FailureCode | None = None,
    ) -> Result[Job]: ...


class LeaseView(Protocol):
    async def has_active_leases(self, artifact_id: ArtifactId) -> bool: ...


@dataclass
class CapacityEvictionService:
    """CAP-08 style ensure_capacity: expire, then oldest ready_at without leases."""

    capacity: CapacityAccounting
    artifacts: ArtifactRepo
    files: FileStore
    jobs: JobRepo
    leases: LeaseView
    candidate_batch_limit: int = 50
    max_evictions_per_call: int = 100

    async def ensure_capacity(
        self,
        *,
        needed_bytes: int,
        now: datetime,
        workspace_bytes: int = 0,
    ) -> bool:
        """Free space until ``available_bytes >= needed_bytes``.

        Returns True when the need is met (including when needed_bytes <= 0).
        Does not raise on shortfall; callers re-check via reserve/grow.
        """
        if needed_bytes <= 0:
            return True

        await self._reclaim_expired(now=now, limit=self.candidate_batch_limit)
        if await self._available(workspace_bytes=workspace_bytes) >= needed_bytes:
            return True

        evicted = 0
        # Re-list after each successful eviction so ordering stays fresh.
        while evicted < self.max_evictions_per_call:
            if await self._available(workspace_bytes=workspace_bytes) >= needed_bytes:
                return True
            candidates = await self.artifacts.list_eviction_candidates(
                now=now, limit=self.candidate_batch_limit
            )
            if not candidates:
                break
            progress = False
            for art in candidates:
                if await self._available(workspace_bytes=workspace_bytes) >= needed_bytes:
                    return True
                if art.access_state is not ArtifactAccessState.AVAILABLE:
                    continue
                if await self.leases.has_active_leases(art.artifact_id):
                    log.debug(
                        "skip leased eviction candidate",
                        extra={
                            "event": "capacity.evict_skip_lease",
                            "artifact_id": art.artifact_id.value,
                        },
                    )
                    continue
                freed = await self._delete_artifact(
                    art,
                    reason=DeletionReason.EVICTED,
                    job_state=JobState.EVICTED,
                    now=now,
                )
                if freed > 0:
                    evicted += 1
                    progress = True
                    log.info(
                        "evicted artifact for capacity",
                        extra={
                            "event": "capacity.evicted",
                            "artifact_id": art.artifact_id.value,
                            "job_id": art.job_id.value,
                            "bytes_freed": freed,
                        },
                    )
                    break  # re-list candidates
            if not progress:
                break

        return await self._available(workspace_bytes=workspace_bytes) >= needed_bytes

    async def _available(self, *, workspace_bytes: int) -> int:
        snap = await self.capacity.snapshot(workspace_bytes=workspace_bytes)
        return int(getattr(snap, "available_bytes", 0))

    async def _reclaim_expired(self, *, now: datetime, limit: int) -> int:
        expired = await self.artifacts.list_expired(now=now, limit=limit)
        total = 0
        for art in expired:
            if art.access_state is not ArtifactAccessState.AVAILABLE:
                continue
            if await self.leases.has_active_leases(art.artifact_id):
                continue
            freed = await self._delete_artifact(
                art,
                reason=DeletionReason.EXPIRED,
                job_state=JobState.EXPIRED,
                now=now,
            )
            total += freed
        return total

    async def _delete_artifact(
        self,
        art: Artifact,
        *,
        reason: DeletionReason,
        job_state: JobState,
        now: datetime,
    ) -> int:
        pending = await self.artifacts.mark_deletion_pending(
            art.artifact_id,
            expected_version=art.version,
            reason=reason,
            now=now,
        )
        if not isinstance(pending, Ok):
            return 0
        await self._invalidate(art.artifact_id)
        try:
            await self.files.delete(art.storage_key)
        except Exception:
            log.debug(
                "artifact unlink failed during reclaim",
                extra={
                    "event": "capacity.unlink_failed",
                    "artifact_id": art.artifact_id.value,
                },
            )
            return 0
        fin = await self.artifacts.finish_deletion(
            art.artifact_id,
            expected_version=pending.value.version,
            now=now,
        )
        if not isinstance(fin, Ok):
            return 0
        self.capacity.add_artifact_bytes(-art.byte_size)
        job = await self.jobs.get(art.job_id)
        if job is not None and job.state in {
            JobState.COMPLETED,
            JobState.COMPLETED_WITH_ERRORS,
            JobState.FAILED,
        }:
            await self.jobs.transition(
                art.job_id,
                expected_version=job.version,
                new_state=job_state,
            )
        return art.byte_size

    async def _invalidate(self, artifact_id: ArtifactId) -> None:
        inv: Callable[[ArtifactId], Awaitable[None] | None] | None = getattr(
            self.leases, "invalidate", None
        )
        if inv is None:
            return
        result = inv(artifact_id)
        if inspect.isawaitable(result):
            await result
