"""Hardened aiosqlite connection management and instance lock."""

from __future__ import annotations

import contextlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from ytdlp_bot.adapters.security.redaction import sanitize_exception

if sys.platform != "win32":
    import fcntl
else:  # pragma: no cover - deployment target is Linux
    fcntl = None  # type: ignore[assignment]


class DatabaseError(Exception):
    """Safe database failure (English diagnostic, no secrets)."""


@dataclass
class InstanceLock:
    """Nonblocking exclusive lock for single-instance deployment."""

    path: Path
    _fh: Any = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # mode 0600
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        self._fh = os.fdopen(fd, "r+")
        if fcntl is None:
            raise DatabaseError("instance lock requires POSIX fcntl")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._fh.close()
            self._fh = None
            raise DatabaseError("another instance holds the service lock") from exc
        # Diagnostic-only contents; never used as a live PID signal.
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write("owner=ytdlp-bot\n")
        self._fh.flush()
        os.chmod(self.path, 0o600)

    def release(self) -> None:
        if self._fh is None:
            return
        if fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()
        self._fh = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


async def open_connection(path: Path) -> aiosqlite.Connection:
    """Open a hardened SQLite connection with required PRAGMAs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = await aiosqlite.connect(path)
    except aiosqlite.Error as exc:
        raise DatabaseError(sanitize_exception(exc)) from exc

    conn.row_factory = aiosqlite.Row
    pragmas = [
        "PRAGMA journal_mode=WAL",
        "PRAGMA foreign_keys=ON",
        "PRAGMA synchronous=FULL",
        "PRAGMA busy_timeout=5000",
        "PRAGMA secure_delete=ON",
        "PRAGMA trusted_schema=OFF",
        "PRAGMA recursive_triggers=ON",
    ]
    try:
        for stmt in pragmas:
            await conn.execute(stmt)
        # Defensive: query journal_mode
        cur = await conn.execute("PRAGMA journal_mode")
        row = await cur.fetchone()
        mode = str(row[0]).lower() if row else ""
        if mode != "wal":
            await conn.close()
            raise DatabaseError(f"failed to enable WAL mode (got {mode!r})")
    except aiosqlite.Error as exc:
        await conn.close()
        raise DatabaseError(sanitize_exception(exc)) from exc

    try:
        os.chmod(path, 0o600)
        wal = Path(str(path) + "-wal")
        shm = Path(str(path) + "-shm")
        for side in (wal, shm):
            if side.exists():
                os.chmod(side, 0o600)
    except OSError:
        # Best-effort permissions on some filesystems.
        pass
    return conn


async def fetch_pragma(conn: aiosqlite.Connection, name: str) -> str:
    cur = await conn.execute(f"PRAGMA {name}")
    row = await cur.fetchone()
    return str(row[0]) if row else ""


class UnitOfWork:
    """Short explicit write transaction with BEGIN IMMEDIATE."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._active = False

    async def __aenter__(self) -> UnitOfWork:
        if self._active:
            raise DatabaseError("nested unit of work is not supported")
        await self._conn.execute("BEGIN IMMEDIATE")
        self._active = True
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if not self._active:
            return
        self._active = False
        if exc_type is not None:
            await self._conn.execute("ROLLBACK")
            return
        await self._conn.execute("COMMIT")

    async def commit(self) -> None:
        if not self._active:
            raise DatabaseError("unit of work is not active")
        await self._conn.execute("COMMIT")
        self._active = False

    async def rollback(self) -> None:
        if not self._active:
            return
        await self._conn.execute("ROLLBACK")
        self._active = False
