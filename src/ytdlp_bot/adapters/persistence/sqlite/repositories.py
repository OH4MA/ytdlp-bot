"""SQLite implementations of repository ports."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

import aiosqlite

from ytdlp_bot.adapters.persistence.sqlite.mappers import (
    artifact_from_row,
    dt_to_ms,
    job_from_row,
    job_to_context_json,
    progress_to_json,
)
from ytdlp_bot.domain.enums import (
    AccessMode,
    DeletionReason,
    FailureCode,
    JobState,
    Platform,
)
from ytdlp_bot.domain.identity import ArtifactId, Identity, JobId, MessageReference
from ytdlp_bot.domain.jobs import Artifact, Job, JobPayload, RuntimeSetting
from ytdlp_bot.domain.progress import ProgressSnapshot
from ytdlp_bot.ports.results import Conflict, Ok, Result


class SqliteJobRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def create(self, job: Job) -> Job:
        await self._conn.execute(
            """
            INSERT INTO jobs (
              job_id, owner_platform, owner_user_id, request_mode, selected_preset,
              source_display, media_kind, state, completion_outcome, context_json,
              acknowledged_at, dispatchable, cancellation_requested,
              controller_instance_id, worker_instance_id, worker_lease_expires_at,
              last_worker_sequence, progress_json, warning_codes_json, error_code,
              error_detail, version, created_at, started_at, ready_at, terminal_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.job_id.value,
                job.owner.platform.value,
                job.owner.user_id,
                job.request_mode.value,
                job.selected_preset,
                job.source_display,
                job.kind.value,
                job.state.value,
                job_to_context_json(job),
                dt_to_ms(job.acknowledged_at),
                1 if job.dispatchable else 0,
                1 if job.cancellation_requested else 0,
                job.worker_lease.controller_id if job.worker_lease else None,
                dt_to_ms(job.worker_lease.heartbeat_at) if job.worker_lease else None,
                job.last_event_sequence,
                progress_to_json(job.progress),
                json.dumps([w.value for w in job.warning_codes]),
                job.error_code.value if job.error_code else None,
                job.version,
                dt_to_ms(job.created_at) or 0,
                dt_to_ms(job.started_at),
                dt_to_ms(job.ready_at),
                dt_to_ms(job.terminal_at),
                dt_to_ms(job.updated_at) or dt_to_ms(job.created_at) or 0,
            ),
        )
        await self._conn.commit()
        loaded = await self.get(job.job_id)
        assert loaded is not None
        return loaded

    async def get(self, job_id: JobId) -> Job | None:
        cur = await self._conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id.value,))
        row = await cur.fetchone()
        return job_from_row(row) if row else None

    async def get_owned(self, job_id: JobId, owner: Identity) -> Job | None:
        cur = await self._conn.execute(
            """
            SELECT * FROM jobs
            WHERE job_id = ? AND owner_platform = ? AND owner_user_id = ?
            """,
            (job_id.value, owner.platform.value, owner.user_id),
        )
        row = await cur.fetchone()
        return job_from_row(row) if row else None

    async def list_owned_recent(self, owner: Identity, *, limit: int) -> Sequence[Job]:
        cur = await self._conn.execute(
            """
            SELECT * FROM jobs
            WHERE owner_platform = ? AND owner_user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (owner.platform.value, owner.user_id, limit),
        )
        rows = await cur.fetchall()
        return [job_from_row(r) for r in rows]

    async def claim_next(
        self,
        *,
        controller_id: str,
        now: datetime,
        expected_states: Sequence[JobState],
    ) -> Job | None:
        states = [s.value for s in expected_states]
        placeholders = ",".join("?" * len(states))
        cur = await self._conn.execute(
            f"""
            SELECT * FROM jobs
            WHERE state IN ({placeholders})
              AND dispatchable = 1
              AND cancellation_requested = 0
            ORDER BY created_at ASC, job_id ASC
            LIMIT 1
            """,
            tuple(states),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        job_id = str(row["job_id"])
        version = int(row["version"])
        now_ms = dt_to_ms(now) or 0
        cur = await self._conn.execute(
            """
            UPDATE jobs
            SET state = 'inspecting',
                version = version + 1,
                controller_instance_id = ?,
                worker_lease_expires_at = ?,
                started_at = COALESCE(started_at, ?),
                updated_at = ?
            WHERE job_id = ? AND version = ? AND state = 'queued' AND dispatchable = 1
            """,
            (controller_id, now_ms, now_ms, now_ms, job_id, version),
        )
        await self._conn.commit()
        if cur.rowcount != 1:
            return None
        return await self.get(JobId(job_id))

    async def request_cancellation(self, job_id: JobId, *, expected_version: int) -> Result[Job]:
        job = await self.get(job_id)
        if job is None or job.version != expected_version:
            return Conflict(
                expected_version=expected_version,
                actual_version=job.version if job else None,
            )
        if job.cancellation_requested:
            return Ok(job)
        cur = await self._conn.execute(
            """
            UPDATE jobs
            SET cancellation_requested = 1,
                state = CASE WHEN state = 'queued' THEN 'cancelling' ELSE 'cancelling' END,
                version = version + 1,
                updated_at = updated_at
            WHERE job_id = ? AND version = ?
            """,
            (job_id.value, expected_version),
        )
        await self._conn.commit()
        if cur.rowcount != 1:
            return Conflict(expected_version=expected_version)
        loaded = await self.get(job_id)
        assert loaded is not None
        return Ok(loaded)

    async def transition(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        new_state: JobState,
        error_code: FailureCode | None = None,
    ) -> Result[Job]:
        cur = await self._conn.execute(
            """
            UPDATE jobs
            SET state = ?,
                error_code = COALESCE(?, error_code),
                version = version + 1,
                updated_at = updated_at
            WHERE job_id = ? AND version = ?
            """,
            (
                new_state.value,
                error_code.value if error_code else None,
                job_id.value,
                expected_version,
            ),
        )
        await self._conn.commit()
        if cur.rowcount != 1:
            job = await self.get(job_id)
            return Conflict(
                expected_version=expected_version,
                actual_version=job.version if job else None,
            )
        loaded = await self.get(job_id)
        assert loaded is not None
        return Ok(loaded)

    async def update_progress_snapshot(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        progress: ProgressSnapshot,
    ) -> Result[Job]:
        cur = await self._conn.execute(
            """
            UPDATE jobs
            SET progress_json = ?,
                last_worker_sequence = ?,
                version = version + 1
            WHERE job_id = ? AND version = ?
            """,
            (
                progress_to_json(progress),
                progress.source_sequence,
                job_id.value,
                expected_version,
            ),
        )
        await self._conn.commit()
        if cur.rowcount != 1:
            job = await self.get(job_id)
            return Conflict(
                expected_version=expected_version,
                actual_version=job.version if job else None,
            )
        loaded = await self.get(job_id)
        assert loaded is not None
        return Ok(loaded)

    async def attach_message_reference(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        message_reference: MessageReference,
        acknowledged_at: datetime,
    ) -> Result[Job]:
        job = await self.get(job_id)
        if job is None or job.version != expected_version:
            return Conflict(
                expected_version=expected_version,
                actual_version=job.version if job else None,
            )
        updated = job.with_updates(
            message_reference=message_reference,
            acknowledged_at=acknowledged_at,
            dispatchable=True,
            version=job.version + 1,
        )
        cur = await self._conn.execute(
            """
            UPDATE jobs
            SET context_json = ?,
                acknowledged_at = ?,
                dispatchable = 1,
                version = version + 1
            WHERE job_id = ? AND version = ?
            """,
            (
                job_to_context_json(updated),
                dt_to_ms(acknowledged_at),
                job_id.value,
                expected_version,
            ),
        )
        await self._conn.commit()
        if cur.rowcount != 1:
            return Conflict(expected_version=expected_version)
        loaded = await self.get(job_id)
        assert loaded is not None
        return Ok(loaded)

    async def purge(self, job_id: JobId) -> None:
        await self._conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id.value,))
        await self._conn.commit()


class SqliteJobPayloadRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def put(self, payload: JobPayload) -> None:
        await self._conn.execute(
            """
            INSERT INTO job_payloads(job_id, source_url, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET source_url = excluded.source_url
            """,
            (payload.job_id.value, payload.source_url, dt_to_ms(payload.created_at) or 0),
        )
        await self._conn.commit()

    async def get(self, job_id: JobId) -> JobPayload | None:
        cur = await self._conn.execute(
            "SELECT job_id, source_url, created_at FROM job_payloads WHERE job_id = ?",
            (job_id.value,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        from ytdlp_bot.adapters.persistence.sqlite.mappers import ms_to_dt

        return JobPayload(
            job_id=JobId(str(row["job_id"])),
            source_url=str(row["source_url"]),
            created_at=ms_to_dt(row["created_at"]) or datetime.fromtimestamp(0),
        )

    async def delete(self, job_id: JobId) -> None:
        await self._conn.execute("DELETE FROM job_payloads WHERE job_id = ?", (job_id.value,))
        await self._conn.commit()


class SqliteArtifactRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def create_available(self, artifact: Artifact) -> Artifact:
        await self._conn.execute(
            """
            INSERT INTO artifacts (
              artifact_id, job_id, storage_key, display_name, media_type, byte_size,
              access_state, deletion_reason, token_version, ready_at, expires_at,
              deletion_attempts, next_deletion_attempt_at, last_deletion_error,
              deleted_at, updated_at, version
            ) VALUES (?, ?, ?, ?, ?, ?, 'available', NULL, ?, ?, ?, 0, NULL, NULL, NULL, ?, ?)
            """,
            (
                artifact.artifact_id.value,
                artifact.job_id.value,
                artifact.storage_key,
                artifact.display_name,
                artifact.media_type.value,
                artifact.byte_size,
                artifact.token_version,
                dt_to_ms(artifact.ready_at),
                dt_to_ms(artifact.expires_at),
                dt_to_ms(artifact.ready_at),
                artifact.version,
            ),
        )
        await self._conn.execute(
            """
            UPDATE service_state
            SET artifact_catalog_revision = artifact_catalog_revision + 1,
                updated_at = ?
            WHERE singleton = 1
            """,
            (dt_to_ms(artifact.ready_at),),
        )
        await self._conn.commit()
        loaded = await self.get(artifact.artifact_id)
        assert loaded is not None
        return loaded

    async def get(self, artifact_id: ArtifactId) -> Artifact | None:
        cur = await self._conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id.value,)
        )
        row = await cur.fetchone()
        return artifact_from_row(row) if row else None

    async def get_for_job(self, job_id: JobId) -> Artifact | None:
        cur = await self._conn.execute("SELECT * FROM artifacts WHERE job_id = ?", (job_id.value,))
        row = await cur.fetchone()
        return artifact_from_row(row) if row else None

    async def mark_deletion_pending(
        self,
        artifact_id: ArtifactId,
        *,
        expected_version: int,
        reason: DeletionReason,
        now: datetime,
    ) -> Result[Artifact]:
        cur = await self._conn.execute(
            """
            UPDATE artifacts
            SET access_state = 'deletion_pending',
                deletion_reason = ?,
                token_version = token_version + 1,
                version = version + 1,
                updated_at = ?
            WHERE artifact_id = ? AND version = ? AND access_state = 'available'
            """,
            (reason.value, dt_to_ms(now), artifact_id.value, expected_version),
        )
        if cur.rowcount == 1:
            await self._conn.execute(
                """
                UPDATE service_state
                SET artifact_catalog_revision = artifact_catalog_revision + 1,
                    storage_epoch = storage_epoch + 1,
                    updated_at = ?
                WHERE singleton = 1
                """,
                (dt_to_ms(now),),
            )
        await self._conn.commit()
        if cur.rowcount != 1:
            art = await self.get(artifact_id)
            return Conflict(
                expected_version=expected_version,
                actual_version=art.version if art else None,
            )
        loaded = await self.get(artifact_id)
        assert loaded is not None
        return Ok(loaded)

    async def finish_deletion(
        self,
        artifact_id: ArtifactId,
        *,
        expected_version: int,
        now: datetime,
    ) -> Result[Artifact]:
        cur = await self._conn.execute(
            """
            UPDATE artifacts
            SET access_state = 'deleted',
                deleted_at = ?,
                version = version + 1,
                updated_at = ?
            WHERE artifact_id = ? AND version = ?
              AND access_state = 'deletion_pending'
            """,
            (dt_to_ms(now), dt_to_ms(now), artifact_id.value, expected_version),
        )
        await self._conn.commit()
        if cur.rowcount != 1:
            art = await self.get(artifact_id)
            return Conflict(
                expected_version=expected_version,
                actual_version=art.version if art else None,
            )
        loaded = await self.get(artifact_id)
        assert loaded is not None
        return Ok(loaded)

    async def schedule_deletion_retry(
        self,
        artifact_id: ArtifactId,
        *,
        expected_version: int,
        next_attempt_at: datetime,
        error: str,
    ) -> Result[Artifact]:
        cur = await self._conn.execute(
            """
            UPDATE artifacts
            SET deletion_attempts = deletion_attempts + 1,
                next_deletion_attempt_at = ?,
                last_deletion_error = ?,
                version = version + 1
            WHERE artifact_id = ? AND version = ?
            """,
            (dt_to_ms(next_attempt_at), error[:256], artifact_id.value, expected_version),
        )
        await self._conn.commit()
        if cur.rowcount != 1:
            art = await self.get(artifact_id)
            return Conflict(
                expected_version=expected_version,
                actual_version=art.version if art else None,
            )
        loaded = await self.get(artifact_id)
        assert loaded is not None
        return Ok(loaded)

    async def increment_token_version(
        self, artifact_id: ArtifactId, *, expected_version: int
    ) -> Result[Artifact]:
        cur = await self._conn.execute(
            """
            UPDATE artifacts
            SET token_version = token_version + 1,
                version = version + 1
            WHERE artifact_id = ? AND version = ?
            """,
            (artifact_id.value, expected_version),
        )
        await self._conn.commit()
        if cur.rowcount != 1:
            art = await self.get(artifact_id)
            return Conflict(
                expected_version=expected_version,
                actual_version=art.version if art else None,
            )
        loaded = await self.get(artifact_id)
        assert loaded is not None
        return Ok(loaded)

    async def purge_tombstone(self, artifact_id: ArtifactId) -> None:
        await self._conn.execute(
            "DELETE FROM artifacts WHERE artifact_id = ? AND access_state = 'deleted'",
            (artifact_id.value,),
        )
        await self._conn.commit()

    async def list_expired(self, *, now: datetime, limit: int) -> Sequence[Artifact]:
        cur = await self._conn.execute(
            """
            SELECT * FROM artifacts
            WHERE access_state = 'available' AND expires_at <= ?
            ORDER BY expires_at ASC, artifact_id ASC
            LIMIT ?
            """,
            (dt_to_ms(now), limit),
        )
        return [artifact_from_row(r) for r in await cur.fetchall()]

    async def list_eviction_candidates(self, *, now: datetime, limit: int) -> Sequence[Artifact]:
        cur = await self._conn.execute(
            """
            SELECT * FROM artifacts
            WHERE access_state = 'available' AND expires_at > ?
            ORDER BY ready_at ASC, artifact_id ASC
            LIMIT ?
            """,
            (dt_to_ms(now), limit),
        )
        return [artifact_from_row(r) for r in await cur.fetchall()]

    async def list_deletion_pending(self, *, limit: int) -> Sequence[Artifact]:
        cur = await self._conn.execute(
            """
            SELECT * FROM artifacts
            WHERE access_state = 'deletion_pending'
            ORDER BY next_deletion_attempt_at ASC NULLS FIRST
            LIMIT ?
            """,
            (limit,),
        )
        return [artifact_from_row(r) for r in await cur.fetchall()]


class SqliteSettingsRepository:
    def __init__(self, conn: aiosqlite.Connection, defaults: dict[str, object]) -> None:
        self._conn = conn
        self._defaults = dict(defaults)

    async def effective_values(self) -> dict[str, object]:
        out = dict(self._defaults)
        cur = await self._conn.execute("SELECT setting_key, value_json FROM runtime_settings")
        for row in await cur.fetchall():
            out[str(row["setting_key"])] = json.loads(row["value_json"])
        return out

    async def set_override(
        self, key: str, value: object, *, updated_by: Identity, now: datetime
    ) -> RuntimeSetting:
        await self._conn.execute(
            """
            INSERT INTO runtime_settings(
              setting_key, value_json, revision, updated_by_platform,
              updated_by_user_id, updated_at
            ) VALUES (?, ?, 1, ?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
              value_json = excluded.value_json,
              revision = runtime_settings.revision + 1,
              updated_by_platform = excluded.updated_by_platform,
              updated_by_user_id = excluded.updated_by_user_id,
              updated_at = excluded.updated_at
            """,
            (
                key,
                json.dumps(value),
                updated_by.platform.value,
                updated_by.user_id,
                dt_to_ms(now),
            ),
        )
        await self._conn.execute(
            """
            UPDATE service_state
            SET settings_revision = settings_revision + 1, updated_at = ?
            WHERE singleton = 1
            """,
            (dt_to_ms(now),),
        )
        await self._conn.commit()
        return RuntimeSetting(key=key, value=value, updated_at=now, updated_by=updated_by)

    async def reset_override(self, key: str, *, updated_by: Identity, now: datetime) -> None:
        await self._conn.execute("DELETE FROM runtime_settings WHERE setting_key = ?", (key,))
        await self._conn.execute(
            """
            UPDATE service_state
            SET settings_revision = settings_revision + 1, updated_at = ?
            WHERE singleton = 1
            """,
            (dt_to_ms(now),),
        )
        await self._conn.commit()

    async def compare_and_set(
        self,
        key: str,
        *,
        expected: object,
        new_value: object,
        updated_by: Identity,
        now: datetime,
    ) -> Result[RuntimeSetting]:
        current = (await self.effective_values()).get(key)
        if current != expected:
            return Conflict(expected_version=0, message="value mismatch")
        setting = await self.set_override(key, new_value, updated_by=updated_by, now=now)
        return Ok(setting)


class SqliteAccessRepository:
    def __init__(self, conn: aiosqlite.Connection, initial_mode: AccessMode) -> None:
        self._conn = conn
        self._mode = initial_mode

    async def get_mode(self) -> AccessMode:
        values = await SqliteSettingsRepository(
            self._conn, {"access_mode": self._mode.value}
        ).effective_values()
        return AccessMode(str(values.get("access_mode", self._mode.value)))

    async def set_mode(self, mode: AccessMode, *, updated_by: Identity, now: datetime) -> None:
        repo = SqliteSettingsRepository(self._conn, {})
        await repo.set_override("access_mode", mode.value, updated_by=updated_by, now=now)
        self._mode = mode

    async def list_whitelist(self, *, platform: Platform | None = None) -> Sequence[Identity]:
        if platform is None:
            cur = await self._conn.execute(
                "SELECT platform, user_id FROM whitelist ORDER BY platform, user_id"
            )
        else:
            cur = await self._conn.execute(
                """
                SELECT platform, user_id FROM whitelist
                WHERE platform = ? ORDER BY user_id
                """,
                (platform.value,),
            )
        return [
            Identity(platform=Platform(r["platform"]), user_id=str(r["user_id"]))
            for r in await cur.fetchall()
        ]

    async def add_identity(self, identity: Identity, *, now: datetime) -> bool:
        cur = await self._conn.execute(
            """
            INSERT OR IGNORE INTO whitelist(
              platform, user_id, created_by_platform, created_by_user_id, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                identity.platform.value,
                identity.user_id,
                identity.platform.value,
                identity.user_id,
                dt_to_ms(now),
            ),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def remove_identity(self, identity: Identity) -> bool:
        cur = await self._conn.execute(
            "DELETE FROM whitelist WHERE platform = ? AND user_id = ?",
            (identity.platform.value, identity.user_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1
