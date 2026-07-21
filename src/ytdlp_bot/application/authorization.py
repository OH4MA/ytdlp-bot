"""Authorization snapshots and owner-scoped access checks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ytdlp_bot.domain.enums import AccessMode, FailureCode
from ytdlp_bot.domain.errors import AuthorizationError, NotFoundError, failure
from ytdlp_bot.domain.identity import ArtifactId, Identity, JobId
from ytdlp_bot.domain.jobs import Artifact, Job


class AccessSnapshotPort(Protocol):
    async def get_mode(self) -> AccessMode: ...

    async def list_whitelist(self, *, platform=None) -> Sequence[Identity]: ...


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
    ) -> None:
        self._access = access
        self._jobs = jobs
        self._artifacts = artifacts
        self._administrators = administrators

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

    async def require_user_access(self, identity: Identity) -> AuthSnapshot:
        snap = await self.snapshot()
        if self.is_administrator(identity, snap):
            return snap
        if snap.mode is AccessMode.ALLOW_ALL:
            return snap
        if identity in snap.whitelist:
            return snap
        raise AuthorizationError(failure(FailureCode.NOT_AUTHORIZED, diagnostic="user not allowed"))

    async def require_administrator(self, identity: Identity) -> AuthSnapshot:
        snap = await self.snapshot()
        if not self.is_administrator(identity, snap):
            raise AuthorizationError(
                failure(FailureCode.NOT_AUTHORIZED, diagnostic="not administrator")
            )
        return snap

    async def require_job_owner(
        self, job_id: JobId, identity: Identity
    ) -> tuple[Job, AuthSnapshot]:
        snap = await self.require_user_access(identity)
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
        self, job_id: JobId, identity: Identity
    ) -> tuple[Job, Artifact | None, AuthSnapshot]:
        job, snap = await self.require_job_owner(job_id, identity)
        art = await self._artifacts.get_for_job(job_id)
        return job, art, snap
