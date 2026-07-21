"""Fake artifact lease registry."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ytdlp_bot.domain.enums import LeaseKind
from ytdlp_bot.domain.identity import ArtifactId


@dataclass
class FakeArtifactLeaseRegistry:
    _holders: dict[str, set[tuple[LeaseKind, str]]] = field(
        default_factory=lambda: defaultdict(set)
    )
    _invalidated: set[str] = field(default_factory=set)

    def reset(self) -> None:
        self._holders.clear()
        self._invalidated.clear()

    async def acquire(self, artifact_id: ArtifactId, kind: LeaseKind, *, holder_id: str) -> bool:
        if artifact_id.value in self._invalidated:
            return False
        self._holders[artifact_id.value].add((kind, holder_id))
        return True

    async def release(self, artifact_id: ArtifactId, kind: LeaseKind, *, holder_id: str) -> None:
        self._holders[artifact_id.value].discard((kind, holder_id))

    async def holder_count(self, artifact_id: ArtifactId) -> int:
        return len(self._holders.get(artifact_id.value, set()))

    async def has_active_leases(self, artifact_id: ArtifactId) -> bool:
        return await self.holder_count(artifact_id) > 0

    def invalidate(self, artifact_id: ArtifactId) -> None:
        self._invalidated.add(artifact_id.value)
