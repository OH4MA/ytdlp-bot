"""CAP capacity equation unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ytdlp_bot.adapters.storage.capacity import CapacityManager
from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore
from ytdlp_bot.domain.errors import DomainError
from ytdlp_bot.domain.identity import JobId


@pytest.mark.unit
@pytest.mark.asyncio
async def test_capacity_reserve_and_deny(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "root")
    mgr = CapacityManager(
        store=store,
        capacity_bytes=10_000,
        safety_headroom_bytes=1_000,
        unknown_size_initial_reservation_bytes=2_000,
        reservation_growth_bytes=1_000,
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    amount = await mgr.reserve(JobId("J" * 22), known_size=3_000, now=now)
    assert amount == 3_000
    snap = await mgr.snapshot()
    assert snap.available_bytes < 10_000
    with pytest.raises(DomainError):
        await mgr.reserve(JobId("K" * 22), known_size=100_000, now=now)
    await mgr.release(JobId("J" * 22))
    assert mgr.reservation_total() == 0
