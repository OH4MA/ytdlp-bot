"""Authorization snapshots and owner-scoped access checks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from ytdlp_bot.domain.enums import AccessMode, FailureCode
from ytdlp_bot.domain.errors import AuthorizationError, NotFoundError, failure
from ytdlp_bot.domain.identity import AccessDenial, ArtifactId, Identity, JobId
from ytdlp_bot.domain.jobs import Artifact, Job

# Keep unauthorized identities long enough for an admin to review and whitelist.
_DENIAL_TTL = timedelta(days=7)
_DENIAL_LIST_DEFAULT = 20


class AccessSnapshotPort(Protocol):
    async def get_mode(self) -> AccessMode: ...

    async def list_whitelist(self, *, platform=None) -> Sequence[Identity]: ...


class AccessDenialPort(Protocol):
    async def record(
        self,
        identity: Identity,
        *,
        now: datetime,
        command: str | None = None,
    ) -> None: ...

    async def list_recent(self, *, limit: int = 20) -> Sequence[AccessDenial]: ...

    async def clear(self, identity: Identity) -> bool: ...

    async def purge_older_than(self, *, cutoff: datetime) -> int: ...


class JobLookupPort(Protocol):
    async def get_owned(self, job_id: JobId, owner: Identity) -> Job | None: ...

    async def get(self, job_id: JobId) -> Job | None: ...


class ArtifactLookupPort(Protocol):
    async def get(self, artifact_id: ArtifactId) -> Artifact | None: ...

    async def get_for_job(self, job_id: JobId) -> Artifact | None: ...


@dataclass(frozen=True, slots=True)
class AuthSnapshot:
    mode: AccessMode
    whitelist: frozenset[Identity]
    administrators: frozenset[Identity]


class AuthorizationService:
    def __init__(
        self,
        *,
        access: AccessSnapshotPort,
        jobs: JobLookupPort,
        artifacts: ArtifactLookupPort,
        administrators: frozenset[Identity],
        denials: AccessDenialPort | None = None,
    ) -> None:
        self._access = access
        self._jobs = jobs
        self._artifacts = artifacts
        self._administrators = administrators
        self._denials = denials

    async def snapshot(self) -> AuthSnapshot:
        mode = await self._access.get_mode()
        wl = await self._access.list_whitelist()
        return AuthSnapshot(
            mode=mode,
            whitelist=frozenset(wl),
            administrators=self._administrators,
        )

    def is_administrator(self, identity: Identity, snap: AuthSnapshot) -> bool:
        return identity in snap.administrators

    def identity_context(self, identity: Identity) -> dict[str, str]:
        """Safe fields for user-facing unauthorized messages."""
        return {
            "identity": identity.display(),
            "platform": identity.platform.value,
            "user_id": identity.user_id,
        }

    async def require_user_access(
        self,
        identity: Identity,
        *,
        now: datetime | None = None,
        command: str | None = None,
    ) -> AuthSnapshot:
        snap = await self.snapshot()
        if self.is_administrator(identity, snap):
            return snap
        if snap.mode is AccessMode.ALLOW_ALL:
            return snap
        if identity in snap.whitelist:
            return snap
        if self._denials is not None and now is not None:
            await self._denials.purge_older_than(cutoff=now - _DENIAL_TTL)
            await self._denials.record(identity, now=now, command=command)
        raise AuthorizationError(failure(FailureCode.NOT_AUTHORIZED, diagnostic="user not allowed"))

    async def list_access_denials(
        self, *, now: datetime, limit: int = _DENIAL_LIST_DEFAULT
    ) -> Sequence[AccessDenial]:
        if self._denials is None:
            return ()
        await self._denials.purge_older_than(cutoff=now - _DENIAL_TTL)
        return await self._denials.list_recent(limit=limit)

    async def clear_access_denial(self, identity: Identity) -> bool:
        if self._denials is None:
            return False
        return await self._denials.clear(identity)

    async def require_administrator(self, identity: Identity) -> AuthSnapshot:
        snap = await self.snapshot()
        if not self.is_administrator(identity, snap):
            raise AuthorizationError(
                failure(FailureCode.NOT_AUTHORIZED, diagnostic="not administrator")
            )
        return snap

    async def require_job_owner(
        self, job_id: JobId, identity: Identity, *, now: datetime | None = None
    ) -> tuple[Job, AuthSnapshot]:
        snap = await self.require_user_access(identity, now=now)
        if self.is_administrator(identity, snap):
            job = await self._jobs.get(job_id)
        else:
            job = await self._jobs.get_owned(job_id, identity)
        if job is None:
            # Generic: foreign and missing look the same.
            raise NotFoundError(failure(FailureCode.NOT_AUTHORIZED, diagnostic="job not available"))
        return job, snap

    async def may_manage_job(self, job_id: JobId, identity: Identity) -> bool:
        try:
            await self.require_job_owner(job_id, identity)
            return True
        except (AuthorizationError, NotFoundError):
            return False

    async def require_artifact_for_owner(
        self, job_id: JobId, identity: Identity, *, now: datetime | None = None
    ) -> tuple[Job, Artifact | None, AuthSnapshot]:
        job, snap = await self.require_job_owner(job_id, identity, now=now)
        art = await self._artifacts.get_for_job(job_id)
        return job, art, snap
