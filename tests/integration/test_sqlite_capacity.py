"""DB capacity reservation repository integration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ytdlp_bot.adapters.persistence.sqlite.connection import open_connection
from ytdlp_bot.adapters.persistence.sqlite.migrate import apply_migrations
from ytdlp_bot.adapters.persistence.sqlite.repositories import (
    SqliteCapacityRepository,
    SqliteJobRepository,
)
from ytdlp_bot.domain.enums import JobState, MediaMode, Platform
from ytdlp_bot.domain.identity import Identity, JobId, MessageContext
from ytdlp_bot.domain.jobs import Job
from ytdlp_bot.ports.results import Ok


@pytest.mark.integration
@pytest.mark.asyncio
async def test_capacity_reserve_sum_release(tmp_path: Path):
    conn = await open_connection(tmp_path / "db.sqlite3")
    try:
        await apply_migrations(conn, now_ms=1)
        jobs = SqliteJobRepository(conn)
        cap = SqliteCapacityRepository(conn)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        jid = JobId("J" * 22)
        await jobs.create(
            Job(
                job_id=jid,
                idempotency_key="t:1",
                owner=Identity(platform=Platform.TELEGRAM, user_id="1"),
                message_context=MessageContext(
                    platform=Platform.TELEGRAM,
                    chat_id="1",
                    response_target="1",
                    effective_upload_limit_bytes=1,
                ),
                request_mode=MediaMode.VIDEO,
                selected_preset="best",
                source_display="https://example.com",
                state=JobState.QUEUED,
                dispatchable=False,
                acknowledged_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        r = await cap.reserve(jid, 1000, now=now)
        assert isinstance(r, Ok)
        assert await cap.sum_reservations() == 1000
        await cap.release(jid)
        assert await cap.sum_reservations() == 0
        await cap.reserve(jid, 500, now=now)
        n = await cap.clear_stale(older_than=now + timedelta(seconds=1))
        assert n == 1
    finally:
        await conn.close()
