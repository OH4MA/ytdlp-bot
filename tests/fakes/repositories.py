"""In-memory repository fakes for contract and unit tests."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime

from ytdlp_bot.domain.enums import AccessMode, ArtifactAccessState, DeletionReason, JobState
from ytdlp_bot.domain.identity import ArtifactId, Identity, JobId, MessageReference
from ytdlp_bot.domain.jobs import Artifact, Job, JobPayload, RuntimeSetting
from ytdlp_bot.domain.progress import ProgressSnapshot
from ytdlp_bot.ports.results import Conflict, Ok, Result


class InMemoryJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def reset(self) -> None:
        self._jobs.clear()

    async def create(self, job: Job) -> Job:
        if job.job_id.value in self._jobs:
            raise ValueError("duplicate job")
        self._jobs[job.job_id.value] = job
        return job

    async def get(self, job_id: JobId) -> Job | None:
        return self._jobs.get(job_id.value)

    async def get_owned(self, job_id: JobId, owner: Identity) -> Job | None:
        job = self._jobs.get(job_id.value)
        if job is None or job.owner != owner:
            return None
        return job

    async def list_owned_recent(self, owner: Identity, *, limit: int) -> Sequence[Job]:
        owned = [j for j in self._jobs.values() if j.owner == owner]
        owned.sort(key=lambda j: j.created_at or datetime.min, reverse=True)
        return owned[:limit]

    async def claim_next(
        self,
        *,
        controller_id: str,
        now: datetime,
        expected_states: Sequence[JobState],
    ) -> Job | None:
        candidates = [
            j
            for j in self._jobs.values()
            if j.state in expected_states and j.dispatchable and not j.cancellation_requested
        ]
        candidates.sort(key=lambda j: j.created_at or datetime.min)
        if not candidates:
            return None
        job = candidates[0]
        from ytdlp_bot.domain.jobs import WorkerLease

        updated = job.with_updates(
            state=JobState.INSPECTING,
            version=job.version + 1,
            worker_lease=WorkerLease(controller_id=controller_id, heartbeat_at=now),
            started_at=now,
            updated_at=now,
        )
        self._jobs[job.job_id.value] = updated
        return updated

    async def request_cancellation(self, job_id: JobId, *, expected_version: int) -> Result[Job]:
        job = self._jobs.get(job_id.value)
        if job is None:
            return Conflict(expected_version=expected_version)
        if job.version != expected_version:
            return Conflict(expected_version=expected_version, actual_version=job.version)
        if job.cancellation_requested:
            return Ok(job)
        updated = job.with_updates(
            cancellation_requested=True,
            state=JobState.CANCELLING,
            version=job.version + 1,
        )
        self._jobs[job_id.value] = updated
        return Ok(updated)

    async def transition(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        new_state: JobState,
        error_code=None,
    ) -> Result[Job]:
        job = self._jobs.get(job_id.value)
        if job is None or job.version != expected_version:
            return Conflict(
                expected_version=expected_version,
                actual_version=job.version if job else None,
            )
        updated = job.with_updates(
            state=new_state,
            error_code=error_code if error_code is not None else job.error_code,
            version=job.version + 1,
        )
        self._jobs[job_id.value] = updated
        return Ok(updated)

    async def update_progress_snapshot(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        progress: ProgressSnapshot,
    ) -> Result[Job]:
        job = self._jobs.get(job_id.value)
        if job is None or job.version != expected_version:
            return Conflict(
                expected_version=expected_version,
                actual_version=job.version if job else None,
            )
        # Progress-only: version may stay for downloading self-edge design,
        # but repository CAS still bumps for persistence simplicity in fake.
        updated = job.with_updates(progress=progress, version=job.version + 1)
        self._jobs[job_id.value] = updated
        return Ok(updated)

    async def attach_message_reference(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        message_reference: MessageReference,
        acknowledged_at: datetime,
    ) -> Result[Job]:
        job = self._jobs.get(job_id.value)
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
        self._jobs[job_id.value] = updated
        return Ok(updated)

    async def purge(self, job_id: JobId) -> None:
        self._jobs.pop(job_id.value, None)


class InMemoryJobPayloadRepository:
    def __init__(self) -> None:
        self._payloads: dict[str, JobPayload] = {}

    def reset(self) -> None:
        self._payloads.clear()

    async def put(self, payload: JobPayload) -> None:
        self._payloads[payload.job_id.value] = payload

    async def get(self, job_id: JobId) -> JobPayload | None:
        return self._payloads.get(job_id.value)

    async def delete(self, job_id: JobId) -> None:
        self._payloads.pop(job_id.value, None)


class InMemoryArtifactRepository:
    def __init__(self) -> None:
        self._items: dict[str, Artifact] = {}
        self._by_job: dict[str, str] = {}

    def reset(self) -> None:
        self._items.clear()
        self._by_job.clear()

    async def create_available(self, artifact: Artifact) -> Artifact:
        if artifact.job_id.value in self._by_job:
            raise ValueError("job already has artifact")
        self._items[artifact.artifact_id.value] = artifact
        self._by_job[artifact.job_id.value] = artifact.artifact_id.value
        return artifact

    async def get(self, artifact_id: ArtifactId) -> Artifact | None:
        return self._items.get(artifact_id.value)

    async def get_for_job(self, job_id: JobId) -> Artifact | None:
        aid = self._by_job.get(job_id.value)
        return self._items.get(aid) if aid else None

    async def mark_deletion_pending(
        self,
        artifact_id: ArtifactId,
        *,
        expected_version: int,
        reason: DeletionReason,
        now: datetime,
    ) -> Result[Artifact]:
        art = self._items.get(artifact_id.value)
        if art is None or art.version != expected_version:
            return Conflict(
                expected_version=expected_version,
                actual_version=art.version if art else None,
            )
        updated = art.with_updates(
            access_state=ArtifactAccessState.DELETION_PENDING,
            deletion_reason=reason,
            token_version=art.token_version + 1,
            version=art.version + 1,
        )
        self._items[artifact_id.value] = updated
        return Ok(updated)

    async def finish_deletion(
        self,
        artifact_id: ArtifactId,
        *,
        expected_version: int,
        now: datetime,
    ) -> Result[Artifact]:
        art = self._items.get(artifact_id.value)
        if art is None or art.version != expected_version:
            return Conflict(
                expected_version=expected_version,
                actual_version=art.version if art else None,
            )
        updated = art.with_updates(
            access_state=ArtifactAccessState.DELETED,
            version=art.version + 1,
        )
        self._items[artifact_id.value] = updated
        return Ok(updated)

    async def schedule_deletion_retry(
        self,
        artifact_id: ArtifactId,
        *,
        expected_version: int,
        next_attempt_at: datetime,
        error: str,
    ) -> Result[Artifact]:
        art = self._items.get(artifact_id.value)
        if art is None or art.version != expected_version:
            return Conflict(
                expected_version=expected_version,
                actual_version=art.version if art else None,
            )
        updated = art.with_updates(
            deletion_retry_count=art.deletion_retry_count + 1,
            deletion_next_attempt_at=next_attempt_at,
            deletion_last_error=error[:256],
            version=art.version + 1,
        )
        self._items[artifact_id.value] = updated
        return Ok(updated)

    async def increment_token_version(
        self, artifact_id: ArtifactId, *, expected_version: int
    ) -> Result[Artifact]:
        art = self._items.get(artifact_id.value)
        if art is None or art.version != expected_version:
            return Conflict(
                expected_version=expected_version,
                actual_version=art.version if art else None,
            )
        updated = art.with_updates(
            token_version=art.token_version + 1,
            version=art.version + 1,
        )
        self._items[artifact_id.value] = updated
        return Ok(updated)

    async def purge_tombstone(self, artifact_id: ArtifactId) -> None:
        art = self._items.pop(artifact_id.value, None)
        if art:
            self._by_job.pop(art.job_id.value, None)

    async def list_expired(self, *, now: datetime, limit: int) -> Sequence[Artifact]:
        items = [
            a
            for a in self._items.values()
            if a.access_state is ArtifactAccessState.AVAILABLE and a.expires_at <= now
        ]
        items.sort(key=lambda a: a.expires_at)
        return items[:limit]

    async def list_eviction_candidates(self, *, now: datetime, limit: int) -> Sequence[Artifact]:
        items = [
            a
            for a in self._items.values()
            if a.access_state is ArtifactAccessState.AVAILABLE and a.expires_at > now
        ]
        items.sort(key=lambda a: a.ready_at)
        return items[:limit]

    async def list_deletion_pending(self, *, limit: int) -> Sequence[Artifact]:
        items = [
            a
            for a in self._items.values()
            if a.access_state is ArtifactAccessState.DELETION_PENDING
        ]
        return items[:limit]


class InMemorySettingsRepository:
    def __init__(self, defaults: dict[str, object] | None = None) -> None:
        self._defaults = dict(defaults or {})
        self._overrides: dict[str, RuntimeSetting] = {}

    def reset(self) -> None:
        self._overrides.clear()

    async def effective_values(self) -> dict[str, object]:
        out = dict(self._defaults)
        for key, setting in self._overrides.items():
            out[key] = setting.value
        return out

    async def set_override(
        self, key: str, value: object, *, updated_by: Identity, now: datetime
    ) -> RuntimeSetting:
        setting = RuntimeSetting(key=key, value=value, updated_at=now, updated_by=updated_by)
        self._overrides[key] = setting
        return setting

    async def reset_override(self, key: str, *, updated_by: Identity, now: datetime) -> None:
        self._overrides.pop(key, None)

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
        return Ok(await self.set_override(key, new_value, updated_by=updated_by, now=now))


class InMemoryAccessRepository:
    def __init__(self, mode: AccessMode = AccessMode.ALLOW_ALL) -> None:
        self._mode = mode
        self._whitelist: set[Identity] = set()

    def reset(self) -> None:
        self._mode = AccessMode.ALLOW_ALL
        self._whitelist.clear()

    async def get_mode(self) -> AccessMode:
        return self._mode

    async def set_mode(self, mode: AccessMode, *, updated_by: Identity, now: datetime) -> None:
        self._mode = mode

    async def list_whitelist(self, *, platform=None) -> Sequence[Identity]:
        items = sorted(self._whitelist, key=lambda i: (i.platform.value, i.user_id))
        if platform is not None:
            items = [i for i in items if i.platform is platform]
        return items

    async def add_identity(self, identity: Identity, *, now: datetime) -> bool:
        if identity in self._whitelist:
            return False
        self._whitelist.add(identity)
        return True

    async def remove_identity(self, identity: Identity) -> bool:
        if identity not in self._whitelist:
            return False
        self._whitelist.remove(identity)
        return True


class InMemoryCapacityRepository:
    def __init__(self) -> None:
        self._reservations: dict[str, int] = {}
        self._heartbeats: dict[str, datetime] = {}

    def reset(self) -> None:
        self._reservations.clear()
        self._heartbeats.clear()

    async def reserve(self, job_id: JobId, bytes_: int, *, now: datetime) -> Result[int]:
        self._reservations[job_id.value] = self._reservations.get(job_id.value, 0) + bytes_
        self._heartbeats[job_id.value] = now
        return Ok(self._reservations[job_id.value])

    async def adjust(self, job_id: JobId, bytes_: int, *, now: datetime) -> Result[int]:
        current = self._reservations.get(job_id.value, 0)
        self._reservations[job_id.value] = max(0, current + bytes_)
        self._heartbeats[job_id.value] = now
        return Ok(self._reservations[job_id.value])

    async def heartbeat(self, job_id: JobId, *, now: datetime) -> None:
        if job_id.value in self._reservations:
            self._heartbeats[job_id.value] = now

    async def release(self, job_id: JobId) -> None:
        self._reservations.pop(job_id.value, None)
        self._heartbeats.pop(job_id.value, None)

    async def sum_reservations(self) -> int:
        return sum(self._reservations.values())

    async def clear_stale(self, *, older_than: datetime) -> int:
        stale = [k for k, t in self._heartbeats.items() if t < older_than]
        for key in stale:
            self._reservations.pop(key, None)
            self._heartbeats.pop(key, None)
        return len(stale)


class InMemoryAdminConfirmationRepository:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, object]] = {}

    def reset(self) -> None:
        self._items.clear()

    async def create(
        self,
        confirmation_id: str,
        *,
        action_fingerprint: str,
        owner: Identity,
        expires_at: datetime,
        projected_snapshot: str,
    ) -> None:
        self._items[confirmation_id] = {
            "action_fingerprint": action_fingerprint,
            "owner": owner,
            "expires_at": expires_at,
            "projected_snapshot": projected_snapshot,
            "consumed": False,
        }

    async def consume_if_matching(
        self,
        confirmation_id: str,
        *,
        action_fingerprint: str,
        owner: Identity,
        now: datetime,
        projected_snapshot: str,
    ) -> bool:
        item = self._items.get(confirmation_id)
        if item is None or item["consumed"]:
            return False
        if item["owner"] != owner:
            return False
        if item["action_fingerprint"] != action_fingerprint:
            return False
        if item["projected_snapshot"] != projected_snapshot:
            return False
        if item["expires_at"] < now:  # type: ignore[operator]
            return False
        item["consumed"] = True
        return True

    async def purge_expired(self, *, now: datetime) -> int:
        expired = [k for k, v in self._items.items() if v["expires_at"] < now]  # type: ignore[operator]
        for key in expired:
            del self._items[key]
        return len(expired)


class InMemoryDeliveryAttemptRepository:
    def __init__(self) -> None:
        self._rows: list[dict[str, object]] = []

    def reset(self) -> None:
        self._rows.clear()

    async def record_attempt(
        self,
        job_id: JobId,
        *,
        attempt: int,
        plan: str,
        outcome: str,
        now: datetime,
        error_code: str | None = None,
    ) -> None:
        self._rows.append(
            {
                "job_id": job_id.value,
                "attempt": attempt,
                "plan": plan,
                "outcome": outcome,
                "now": now,
                "error_code": error_code,
            }
        )

    async def list_for_job(self, job_id: JobId) -> Sequence[dict[str, object]]:
        return [r for r in self._rows if r["job_id"] == job_id.value]


class InMemoryNotificationOutboxRepository:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, object]] = {}
        self._seq = 0

    def reset(self) -> None:
        self._items.clear()
        self._seq = 0

    async def enqueue(
        self,
        *,
        job_id: JobId,
        kind: str,
        payload: dict[str, object],
        now: datetime,
    ) -> str:
        # Guard: never accept complete signed URL keys.
        for key in payload:
            if "url" in key.lower() and "signed" in key.lower():
                raise ValueError("complete signed URLs must not enter outbox payload")
        self._seq += 1
        nid = f"n{self._seq:08d}aaaaaaaaaaaa"
        self._items[nid] = {
            "id": nid,
            "job_id": job_id.value,
            "kind": kind,
            "payload": deepcopy(payload),
            "created_at": now,
            "done": False,
        }
        return nid

    async def claim_batch(self, *, limit: int, now: datetime) -> Sequence[dict[str, object]]:
        open_items = [v for v in self._items.values() if not v["done"]]
        return open_items[:limit]

    async def mark_done(self, notification_id: str) -> None:
        if notification_id in self._items:
            self._items[notification_id]["done"] = True

    async def mark_retry(
        self, notification_id: str, *, next_attempt_at: datetime, error: str
    ) -> None:
        if notification_id in self._items:
            self._items[notification_id]["next_attempt_at"] = next_attempt_at
            self._items[notification_id]["error"] = error[:256]
