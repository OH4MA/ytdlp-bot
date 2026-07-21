"""Serialized capacity + atomic artifact publication helper."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from ytdlp_bot.domain.enums import JobState, MediaType
from ytdlp_bot.domain.identity import ArtifactId, JobId
from ytdlp_bot.domain.jobs import Artifact, Job
from ytdlp_bot.ports.results import Ok
from ytdlp_bot.ports.system import IdGenerator


class CapacityPort(Protocol):
    async def grow(
        self, job_id: JobId, *, required_total: int, workspace_bytes: int = 0
    ) -> int: ...
    async def release(self, job_id: JobId) -> None: ...
    def add_artifact_bytes(self, delta: int) -> None: ...


class StorePort(Protocol):
    async def atomically_publish(self, source_path: str, storage_key: str) -> None: ...
    async def delete(self, storage_key: str) -> None: ...


class ArtifactPort(Protocol):
    async def create_available(self, artifact: Artifact) -> Artifact: ...


class JobPort(Protocol):
    async def transition(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        new_state: JobState,
        error_code: object = None,
    ) -> object: ...


class PayloadPort(Protocol):
    async def delete(self, job_id: JobId) -> None: ...


class CapacityEnsure(Protocol):
    async def ensure_capacity(
        self, *, needed_bytes: int, now: datetime, workspace_bytes: int = 0
    ) -> bool: ...


@dataclass
class PublishService:
    """CAP-09/10: capacity lock then publish file then DB row."""

    capacity: CapacityPort
    store: StorePort
    artifacts: ArtifactPort
    jobs: JobPort
    payloads: PayloadPort
    ids: IdGenerator
    retention_seconds: int
    eviction: CapacityEnsure | None = None
    _lock: asyncio.Lock | None = None

    def __post_init__(self) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()

    async def publish_candidate(
        self,
        *,
        job: Job,
        source_path: str,
        display_name: str,
        media_type: MediaType,
        now: datetime,
    ) -> Artifact:
        assert self._lock is not None
        async with self._lock:
            size = Path(source_path).stat().st_size
            if self.eviction is not None:
                reservation_for = getattr(self.capacity, "reservation_for", None)
                current = 0
                if callable(reservation_for):
                    raw = reservation_for(job.job_id)
                    current = raw if isinstance(raw, int) else 0
                need = max(0, size - current)
                if need > 0:
                    await self.eviction.ensure_capacity(needed_bytes=need, now=now)
            await self.capacity.grow(job.job_id, required_total=size)
            key = self.ids.storage_key()
            await self.store.atomically_publish(source_path, key)
            art = Artifact(
                artifact_id=ArtifactId(self.ids.artifact_id()),
                job_id=job.job_id,
                storage_key=key,
                display_name=display_name,
                media_type=media_type,
                byte_size=size,
                ready_at=now,
                expires_at=now + timedelta(seconds=self.retention_seconds),
            )
            try:
                art = await self.artifacts.create_available(art)
            except Exception:
                await self.store.delete(key)
                raise
            await self.payloads.delete(job.job_id)
            await self.capacity.release(job.job_id)
            self.capacity.add_artifact_bytes(size)
            tr = await self.jobs.transition(
                job.job_id,
                expected_version=job.version,
                new_state=JobState.DELIVERING,
            )
            _ = tr
            if isinstance(tr, Ok):
                pass
            return art
