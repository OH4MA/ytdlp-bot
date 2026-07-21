"""Repository and transaction port protocols."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from ytdlp_bot.domain.enums import (
    AccessMode,
    ArtifactAccessState,
    DeletionReason,
    FailureCode,
    JobState,
    Platform,
)
from ytdlp_bot.domain.identity import ArtifactId, Identity, JobId, MessageReference
from ytdlp_bot.domain.jobs import Artifact, Job, JobPayload, PlaylistEntry, RuntimeSetting
from ytdlp_bot.domain.progress import ProgressSnapshot
from ytdlp_bot.ports.results import Conflict, Ok, Result


class JobRepository(Protocol):
    """Authoritative job lifecycle repository."""

    async def create(self, job: Job) -> Job: ...

    async def get(self, job_id: JobId) -> Job | None: ...

    async def get_owned(self, job_id: JobId, owner: Identity) -> Job | None: ...

    async def list_owned_recent(self, owner: Identity, *, limit: int) -> Sequence[Job]: ...

    async def claim_next(
        self, *, controller_id: str, now: datetime, expected_states: Sequence[JobState]
    ) -> Job | None: ...

    async def request_cancellation(
        self, job_id: JobId, *, expected_version: int
    ) -> Result[Job]: ...

    async def transition(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        new_state: JobState,
        error_code: FailureCode | None = None,
    ) -> Result[Job]: ...

    async def update_progress_snapshot(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        progress: ProgressSnapshot,
    ) -> Result[Job]: ...

    async def attach_message_reference(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        message_reference: MessageReference,
        acknowledged_at: datetime,
    ) -> Result[Job]: ...

    async def purge(self, job_id: JobId) -> None: ...


class JobPayloadRepository(Protocol):
    """Operational source URL payload storage."""

    async def put(self, payload: JobPayload) -> None: ...

    async def get(self, job_id: JobId) -> JobPayload | None: ...

    async def delete(self, job_id: JobId) -> None: ...


class PlaylistEntryRepository(Protocol):
    async def upsert(self, entry: PlaylistEntry) -> PlaylistEntry: ...

    async def list_for_job(self, job_id: JobId) -> Sequence[PlaylistEntry]: ...


class UnitOfWork(Protocol):
    """Short transactional boundary for multi-repository mutations."""

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(self, *exc: object) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class ArtifactRepository(Protocol):
    async def create_available(self, artifact: Artifact) -> Artifact: ...

    async def get(self, artifact_id: ArtifactId) -> Artifact | None: ...

    async def get_for_job(self, job_id: JobId) -> Artifact | None: ...

    async def mark_deletion_pending(
        self,
        artifact_id: ArtifactId,
        *,
        expected_version: int,
        reason: DeletionReason,
        now: datetime,
    ) -> Result[Artifact]: ...

    async def finish_deletion(
        self,
        artifact_id: ArtifactId,
        *,
        expected_version: int,
        now: datetime,
    ) -> Result[Artifact]: ...

    async def schedule_deletion_retry(
        self,
        artifact_id: ArtifactId,
        *,
        expected_version: int,
        next_attempt_at: datetime,
        error: str,
    ) -> Result[Artifact]: ...

    async def increment_token_version(
        self, artifact_id: ArtifactId, *, expected_version: int
    ) -> Result[Artifact]: ...

    async def purge_tombstone(self, artifact_id: ArtifactId) -> None: ...

    async def list_expired(self, *, now: datetime, limit: int) -> Sequence[Artifact]: ...

    async def list_eviction_candidates(
        self, *, now: datetime, limit: int
    ) -> Sequence[Artifact]: ...

    async def list_deletion_pending(self, *, limit: int) -> Sequence[Artifact]: ...


class SettingsRepository(Protocol):
    async def effective_values(self) -> dict[str, object]: ...

    async def set_override(
        self, key: str, value: object, *, updated_by: Identity, now: datetime
    ) -> RuntimeSetting: ...

    async def reset_override(self, key: str, *, updated_by: Identity, now: datetime) -> None: ...

    async def compare_and_set(
        self,
        key: str,
        *,
        expected: object,
        new_value: object,
        updated_by: Identity,
        now: datetime,
    ) -> Result[RuntimeSetting]: ...


class AccessRepository(Protocol):
    async def get_mode(self) -> AccessMode: ...

    async def set_mode(self, mode: AccessMode, *, updated_by: Identity, now: datetime) -> None: ...

    async def list_whitelist(self, *, platform: Platform | None = None) -> Sequence[Identity]: ...

    async def add_identity(self, identity: Identity, *, now: datetime) -> bool: ...

    async def remove_identity(self, identity: Identity) -> bool: ...


class CapacityRepository(Protocol):
    async def reserve(self, job_id: JobId, bytes_: int, *, now: datetime) -> Result[int]: ...

    async def adjust(self, job_id: JobId, bytes_: int, *, now: datetime) -> Result[int]: ...

    async def heartbeat(self, job_id: JobId, *, now: datetime) -> None: ...

    async def release(self, job_id: JobId) -> None: ...

    async def sum_reservations(self) -> int: ...

    async def clear_stale(self, *, older_than: datetime) -> int: ...


class AdminConfirmationRepository(Protocol):
    async def create(
        self,
        confirmation_id: str,
        *,
        action_fingerprint: str,
        owner: Identity,
        expires_at: datetime,
        projected_snapshot: str,
    ) -> None: ...

    async def consume_if_matching(
        self,
        confirmation_id: str,
        *,
        action_fingerprint: str,
        owner: Identity,
        now: datetime,
        projected_snapshot: str,
    ) -> bool: ...

    async def purge_expired(self, *, now: datetime) -> int: ...


class DeliveryAttemptRepository(Protocol):
    async def record_attempt(
        self,
        job_id: JobId,
        *,
        attempt: int,
        plan: str,
        outcome: str,
        now: datetime,
        error_code: str | None = None,
    ) -> None: ...

    async def list_for_job(self, job_id: JobId) -> Sequence[dict[str, object]]: ...


class NotificationOutboxRepository(Protocol):
    """Platform notification outbox (no complete signed URLs)."""

    async def enqueue(
        self,
        *,
        job_id: JobId,
        kind: str,
        payload: dict[str, object],
        now: datetime,
    ) -> str: ...

    async def claim_batch(self, *, limit: int, now: datetime) -> Sequence[dict[str, object]]: ...

    async def mark_done(self, notification_id: str) -> None: ...

    async def mark_retry(
        self, notification_id: str, *, next_attempt_at: datetime, error: str
    ) -> None: ...


# Re-export for convenience.
__all__ = [
    "AccessRepository",
    "AdminConfirmationRepository",
    "ArtifactAccessState",
    "ArtifactRepository",
    "CapacityRepository",
    "Conflict",
    "DeliveryAttemptRepository",
    "JobPayloadRepository",
    "JobRepository",
    "NotificationOutboxRepository",
    "Ok",
    "PlaylistEntryRepository",
    "Result",
    "SettingsRepository",
    "UnitOfWork",
]
