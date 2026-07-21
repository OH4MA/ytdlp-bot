"""DB-01..03: hardened SQLite connection, lock, unit of work."""

from __future__ import annotations

from pathlib import Path

import pytest

from ytdlp_bot.adapters.persistence.sqlite.connection import (
    DatabaseError,
    InstanceLock,
    UnitOfWork,
    fetch_pragma,
    open_connection,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_open_connection_pragmas(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "service.sqlite3"
    conn = await open_connection(db_path)
    try:
        assert (await fetch_pragma(conn, "journal_mode")).lower() == "wal"
        assert await fetch_pragma(conn, "foreign_keys") == "1"
        assert (await fetch_pragma(conn, "synchronous")).lower() in {"2", "full"}
        assert await fetch_pragma(conn, "busy_timeout") == "5000"
        assert db_path.exists()
        mode = db_path.stat().st_mode & 0o777
        assert mode == 0o600 or mode == 0o640  # some FS may alter; prefer 0600
    finally:
        await conn.close()


@pytest.mark.integration
def test_instance_lock_exclusive(tmp_path: Path) -> None:
    lock_path = tmp_path / "state" / "instance.lock"
    first = InstanceLock(lock_path)
    first.acquire()
    second = InstanceLock(lock_path)
    with pytest.raises(DatabaseError, match="another instance"):
        second.acquire()
    first.release()
    second.acquire()
    second.release()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unit_of_work_commit_and_rollback(tmp_path: Path) -> None:
    conn = await open_connection(tmp_path / "db.sqlite3")
    try:
        await conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT NOT NULL)")
        await conn.commit()
        async with UnitOfWork(conn):
            await conn.execute("INSERT INTO t(v) VALUES ('a')")
        cur = await conn.execute("SELECT COUNT(*) FROM t")
        row = await cur.fetchone()
        assert row is not None and row[0] == 1

        with pytest.raises(RuntimeError):
            async with UnitOfWork(conn):
                await conn.execute("INSERT INTO t(v) VALUES ('b')")
                raise RuntimeError("boom")
        cur = await conn.execute("SELECT COUNT(*) FROM t")
        row = await cur.fetchone()
        assert row is not None and row[0] == 1
    finally:
        await conn.close()
