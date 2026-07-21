"""DB-04..09: migrations and schema foundation."""

from __future__ import annotations

from pathlib import Path

import pytest

from ytdlp_bot.adapters.persistence.sqlite.connection import DatabaseError, open_connection
from ytdlp_bot.adapters.persistence.sqlite.migrate import (
    apply_migrations,
    discover_migrations,
)


@pytest.mark.integration
def test_discover_migrations_ordered() -> None:
    migrations = discover_migrations()
    assert migrations
    assert migrations[0].version == 1
    assert migrations[0].checksum


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_fresh_and_idempotent(tmp_path: Path) -> None:
    conn = await open_connection(tmp_path / "service.sqlite3")
    try:
        applied = await apply_migrations(conn, now_ms=1_700_000_000_000)
        assert applied == [1]
        again = await apply_migrations(conn, now_ms=1_700_000_000_001)
        assert again == []

        cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in await cur.fetchall()}
        for required in {
            "jobs",
            "job_payloads",
            "artifacts",
            "command_requests",
            "service_state",
            "runtime_settings",
            "whitelist",
            "admin_confirmations",
            "delivery_attempts",
            "platform_notifications",
            "capacity_reservations",
            "playlist_entries",
            "job_events",
            "schema_migrations",
        }:
            assert required in tables

        cur = await conn.execute("SELECT settings_revision FROM service_state WHERE singleton=1")
        row = await cur.fetchone()
        assert row is not None and row[0] == 0
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_checksum_mismatch_detected(tmp_path: Path) -> None:
    conn = await open_connection(tmp_path / "service.sqlite3")
    try:
        await apply_migrations(conn, now_ms=1)
        await conn.execute("UPDATE schema_migrations SET checksum='deadbeef' WHERE version=1")
        await conn.commit()
        with pytest.raises(DatabaseError, match="checksum"):
            await apply_migrations(conn, now_ms=2)
    finally:
        await conn.close()
