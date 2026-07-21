"""Migration discovery, checksum validation, and application."""

from __future__ import annotations

import contextlib
import hashlib
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import aiosqlite

from ytdlp_bot.adapters.persistence.sqlite.connection import DatabaseError
from ytdlp_bot.adapters.security.redaction import sanitize_exception

_MIGRATION_NAME_RE = re.compile(r"^(\d{4})_(.+)\.sql$")


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def discover_migrations() -> list[Migration]:
    """Load ordered migration resources shipped with the package."""
    package = "ytdlp_bot.adapters.persistence.sqlite.migrations"
    root = resources.files(package)
    found: list[Migration] = []
    for entry in root.iterdir():
        if not entry.name.endswith(".sql"):
            continue
        match = _MIGRATION_NAME_RE.fullmatch(entry.name)
        if not match:
            raise DatabaseError(f"invalid migration filename: {entry.name}")
        version = int(match.group(1))
        name = match.group(2)
        sql = entry.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        found.append(Migration(version=version, name=name, sql=sql, checksum=checksum))
    found.sort(key=lambda m: m.version)
    expected = 1
    for migration in found:
        if migration.version != expected:
            raise DatabaseError(
                f"migration version gap: expected {expected}, found {migration.version}"
            )
        expected += 1
    return found


async def applied_migrations(conn: aiosqlite.Connection) -> dict[int, str]:
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    )
    if await cur.fetchone() is None:
        return {}
    cur = await conn.execute("SELECT version, checksum FROM schema_migrations ORDER BY version")
    rows = await cur.fetchall()
    return {int(r[0]): str(r[1]) for r in rows}


async def apply_migrations(
    conn: aiosqlite.Connection,
    *,
    now_ms: int,
    backup_dir: Path | None = None,
) -> list[int]:
    """Apply pending migrations. Returns applied version numbers."""
    migrations = discover_migrations()
    if not migrations:
        raise DatabaseError("no migrations packaged")
    applied = await applied_migrations(conn)
    if applied:
        max_applied = max(applied)
        if max_applied > migrations[-1].version:
            raise DatabaseError("database schema is newer than application migrations")
        for version, checksum in applied.items():
            match = next((m for m in migrations if m.version == version), None)
            if match is None:
                raise DatabaseError(f"applied migration {version} missing from package")
            if match.checksum != checksum:
                raise DatabaseError(f"migration checksum mismatch for version {version}")

    pending = [m for m in migrations if m.version not in applied]
    applied_versions: list[int] = []
    for migration in pending:
        if backup_dir is not None and migration.version > 1:
            backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            # executescript auto-commits; apply whole migration then record.
            await conn.executescript(migration.sql)
            await conn.execute(
                """
                INSERT INTO schema_migrations(version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (migration.version, migration.name, migration.checksum, now_ms),
            )
            await conn.commit()
            applied_versions.append(migration.version)
        except Exception as exc:
            with contextlib.suppress(Exception):
                await conn.rollback()
            raise DatabaseError(
                f"migration {migration.version} failed: {sanitize_exception(exc)}"
            ) from exc

    cur = await conn.execute("PRAGMA foreign_key_check")
    fk_issues = await cur.fetchall()
    if fk_issues:
        raise DatabaseError("foreign key check failed after migration")
    cur = await conn.execute("PRAGMA quick_check")
    row = await cur.fetchone()
    if row is None or str(row[0]).lower() != "ok":
        raise DatabaseError("quick_check failed after migration")
    return applied_versions
