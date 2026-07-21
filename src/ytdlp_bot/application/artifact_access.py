"""Coordinate artifact leases with repository access state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ytdlp_bot.domain.enums import ArtifactAccessState, LeaseKind
from ytdlp_bot.domain.identity import ArtifactId
from ytdlp_bot.ports.repositories import ArtifactRepository
from ytdlp_bot.ports.system import ArtifactLeaseRegistry


@dataclass
class InMemoryArtifactLeaseRegistry:
    _holders: dict[str, set[tuple[LeaseKind, str]]] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _invalidated: set[str] = field(default_factory=set)
    storage_epoch: int = 0

    def _lock_for(self, artifact_id: str) -> asyncio.Lock:
        if artifact_id not in self._locks:
            self._locks[artifact_id] = asyncio.Lock()
        return self._locks[artifact_id]

    async def acquire(self, artifact_id: ArtifactId, kind: LeaseKind, *, holder_id: str) -> bool:
        aid = artifact_id.value
        async with self._lock_for(aid):
            if aid in self._invalidated:
                return False
            holders = self._holders.setdefault(aid, set())
            first = len(holders) == 0
            holders.add((kind, holder_id))
            if first:
                self.storage_epoch += 1
            return True

    async def release(self, artifact_id: ArtifactId, kind: LeaseKind, *, holder_id: str) -> None:
        aid = artifact_id.value
        async with self._lock_for(aid):
            holders = self._holders.get(aid, set())
            holders.discard((kind, holder_id))
            if not holders:
                self._holders.pop(aid, None)
                self.storage_epoch += 1

    async def holder_count(self, artifact_id: ArtifactId) -> int:
        return len(self._holders.get(artifact_id.value, set()))

    async def has_active_leases(self, artifact_id: ArtifactId) -> bool:
        return await self.holder_count(artifact_id) > 0

    async def invalidate(self, artifact_id: ArtifactId) -> None:
        aid = artifact_id.value
        async with self._lock_for(aid):
            self._invalidated.add(aid)
            self.storage_epoch += 1


class ArtifactAccessCoordinator:
    """Combine ArtifactRepository + lease registry under per-artifact locks."""

    def __init__(
        self,
        artifacts: ArtifactRepository,
        leases: ArtifactLeaseRegistry,
    ) -> None:
        self._artifacts = artifacts
        self._leases = leases

    async def try_begin_stream(
        self,
        artifact_id: ArtifactId,
        *,
        holder_id: str,
        expected_token_version: int | None = None,
        expected_display_name: str | None = None,
    ) -> bool:
        art = await self._artifacts.get(artifact_id)
        if art is None or art.access_state is not ArtifactAccessState.AVAILABLE:
            return False
        if expected_token_version is not None and art.token_version != expected_token_version:
            return False
        if expected_display_name is not None and art.display_name != expected_display_name:
            return False
        return await self._leases.acquire(artifact_id, LeaseKind.HTTP_STREAM, holder_id=holder_id)

    async def end_stream(self, artifact_id: ArtifactId, *, holder_id: str) -> None:
        await self._leases.release(artifact_id, LeaseKind.HTTP_STREAM, holder_id=holder_id)

    async def try_begin_upload(self, artifact_id: ArtifactId, *, holder_id: str) -> bool:
        art = await self._artifacts.get(artifact_id)
        if art is None or art.access_state is not ArtifactAccessState.AVAILABLE:
            return False
        return await self._leases.acquire(
            artifact_id, LeaseKind.PLATFORM_UPLOAD, holder_id=holder_id
        )

    async def end_upload(self, artifact_id: ArtifactId, *, holder_id: str) -> None:
        await self._leases.release(artifact_id, LeaseKind.PLATFORM_UPLOAD, holder_id=holder_id)

    async def invalidate(self, artifact_id: ArtifactId) -> None:
        if hasattr(self._leases, "invalidate"):
            await self._leases.invalidate(artifact_id)  # type: ignore[attr-defined]
