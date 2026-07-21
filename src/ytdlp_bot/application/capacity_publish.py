"""Serialized capacity + atomic artifact publication helper."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ytdlp_bot.adapters.storage.capacity import CapacityManager
from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore
from ytdlp_bot.domain.enums import JobState, MediaType
from ytdlp_bot.domain.identity import ArtifactId
from ytdlp_bot.domain.jobs import Artifact, Job
from ytdlp_bot.ports.results import Ok
from ytdlp_bot.ports.system import IdGenerator


@dataclass
class PublishService:
    """CAP-09/10: capacity lock then publish file then DB row."""

    capacity: CapacityManager
    store: LocalArtifactStore
    artifacts: object  # ArtifactRepository
    jobs: object  # JobRepository
    payloads: object  # JobPayloadRepository
    ids: IdGenerator
    retention_seconds: int
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
            # Ensure reservation can cover final size.
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
                art = await self.artifacts.create_available(art)  # type: ignore[misc]
            except Exception:
                # Quarantine unregistered final file.
                await self.store.delete(key)
                raise
            await self.payloads.delete(job.job_id)  # type: ignore[misc]
            await self.capacity.release(job.job_id)
            self.capacity.add_artifact_bytes(size)
            tr = await self.jobs.transition(  # type: ignore[misc]
                job.job_id,
                expected_version=job.version,
                new_state=JobState.DELIVERING,
            )
            if not isinstance(tr, Ok):
                # Row exists; leave for recovery.
                pass
            return art
