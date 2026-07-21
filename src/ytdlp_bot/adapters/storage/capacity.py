"""Capacity equation and reservation manager."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from ytdlp_bot.domain.enums import FailureCode
from ytdlp_bot.domain.errors import DomainError, failure
from ytdlp_bot.domain.identity import JobId
from ytdlp_bot.ports.storage import ArtifactStore


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    capacity_bytes: int
    artifact_bytes: int
    workspace_bytes: int
    reservation_bytes: int
    safety_headroom_bytes: int
    filesystem_free_bytes: int
    available_bytes: int


class CapacityManager:
    """Serialize capacity-changing decisions with one async lock."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        capacity_bytes: int,
        safety_headroom_bytes: int,
        unknown_size_initial_reservation_bytes: int,
        reservation_growth_bytes: int,
    ) -> None:
        self._store = store
        self.capacity_bytes = capacity_bytes
        self.safety_headroom_bytes = safety_headroom_bytes
        self.unknown_size_initial = unknown_size_initial_reservation_bytes
        self.growth_bytes = reservation_growth_bytes
        self._lock = asyncio.Lock()
        self._reservations: dict[str, int] = {}
        self._artifact_bytes = 0

    def set_artifact_bytes(self, total: int) -> None:
        self._artifact_bytes = max(0, total)

    def add_artifact_bytes(self, delta: int) -> None:
        self._artifact_bytes = max(0, self._artifact_bytes + delta)

    async def snapshot(self, *, workspace_bytes: int = 0) -> CapacitySnapshot:
        usage = await self._store.filesystem_usage()
        reserved = sum(self._reservations.values())
        logical = (
            self.capacity_bytes
            - self._artifact_bytes
            - workspace_bytes
            - reserved
            - self.safety_headroom_bytes
        )
        physical = usage.free_bytes - self.safety_headroom_bytes
        available = max(0, min(logical, physical))
        return CapacitySnapshot(
            capacity_bytes=self.capacity_bytes,
            artifact_bytes=self._artifact_bytes,
            workspace_bytes=workspace_bytes,
            reservation_bytes=reserved,
            safety_headroom_bytes=self.safety_headroom_bytes,
            filesystem_free_bytes=usage.free_bytes,
            available_bytes=available,
        )

    async def reserve(
        self,
        job_id: JobId,
        *,
        known_size: int | None,
        now: datetime,
        workspace_bytes: int = 0,
    ) -> int:
        async with self._lock:
            if job_id.value in self._reservations:
                return self._reservations[job_id.value]
            amount = (
                known_size
                if known_size is not None and known_size > 0
                else self.unknown_size_initial
            )
            snap = await self.snapshot(workspace_bytes=workspace_bytes)
            if amount > snap.available_bytes:
                raise DomainError(
                    failure(
                        FailureCode.INSUFFICIENT_CAPACITY,
                        operator_actionable=True,
                        diagnostic="reservation exceeds available capacity",
                    )
                )
            self._reservations[job_id.value] = amount
            return amount

    async def grow(
        self,
        job_id: JobId,
        *,
        required_total: int,
        workspace_bytes: int = 0,
    ) -> int:
        async with self._lock:
            current = self._reservations.get(job_id.value, 0)
            if required_total <= current:
                return current
            # Grow in configured chunks.
            target = current
            while target < required_total:
                target += self.growth_bytes
            need = target - current
            snap = await self.snapshot(workspace_bytes=workspace_bytes)
            if need > snap.available_bytes:
                raise DomainError(
                    failure(
                        FailureCode.INSUFFICIENT_CAPACITY,
                        operator_actionable=True,
                        diagnostic="growth denied",
                    )
                )
            self._reservations[job_id.value] = target
            return target

    async def release(self, job_id: JobId) -> None:
        async with self._lock:
            self._reservations.pop(job_id.value, None)

    def reservation_total(self) -> int:
        return sum(self._reservations.values())
