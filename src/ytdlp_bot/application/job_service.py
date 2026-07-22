"""Job submission, status, and cancellation coordination."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from ytdlp_bot.application.authorization import AuthorizationService
from ytdlp_bot.application.url_safety import UrlSafetyService, ValidatedUrl
from ytdlp_bot.domain.commands import (
    AcceptedJob,
    CancelArgs,
    CommandResult,
    StatusArgs,
    StatusView,
    UserError,
    YtdlArgs,
    Ytmp3Args,
)
from ytdlp_bot.domain.enums import (
    USER_CANCELLABLE_STATES,
    FailureCode,
    JobState,
    MediaMode,
)
from ytdlp_bot.domain.errors import AuthorizationError, DomainError, NotFoundError
from ytdlp_bot.domain.identity import Identity, JobId, MessageContext, MessageReference
from ytdlp_bot.domain.jobs import DownloadRequest, Job, JobPayload
from ytdlp_bot.domain.progress import ProgressSnapshot
from ytdlp_bot.ports.results import Ok
from ytdlp_bot.ports.system import Clock, IdGenerator

log = logging.getLogger("ytdlp_bot.jobs")


class JobRepo(Protocol):
    async def create(self, job: Job) -> Job: ...
    async def get(self, job_id: JobId) -> Job | None: ...
    async def get_owned(self, job_id: JobId, owner: Identity) -> Job | None: ...
    async def list_owned_recent(self, owner: Identity, *, limit: int) -> Any: ...
    async def request_cancellation(self, job_id: JobId, *, expected_version: int) -> Any: ...
    async def transition(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        new_state: JobState,
        error_code: FailureCode | None = None,
    ) -> Any: ...
    async def attach_message_reference(
        self,
        job_id: JobId,
        *,
        expected_version: int,
        message_reference: MessageReference,
        acknowledged_at: datetime,
    ) -> Any: ...


class PayloadRepo(Protocol):
    async def put(self, payload: JobPayload) -> None: ...
    async def get(self, job_id: JobId) -> JobPayload | None: ...
    async def delete(self, job_id: JobId) -> None: ...


class PlatformAck(Protocol):
    async def acknowledge_job(
        self, context: MessageContext, job_id: JobId, initial_state: JobState
    ) -> MessageReference: ...


@dataclass
class JobService:
    auth: AuthorizationService
    url_safety: UrlSafetyService
    jobs: JobRepo
    payloads: PayloadRepo
    platform: PlatformAck
    clock: Clock
    ids: IdGenerator
    recent_limit: int = 10
    _ack_locks: dict[str, asyncio.Lock] | None = None

    def __post_init__(self) -> None:
        if self._ack_locks is None:
            self._ack_locks = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        assert self._ack_locks is not None
        if key not in self._ack_locks:
            self._ack_locks[key] = asyncio.Lock()
        return self._ack_locks[key]

    async def submit_download(
        self,
        *,
        request_id: str,
        identity: Identity,
        context: MessageContext,
        args: YtdlArgs | Ytmp3Args,
    ) -> CommandResult:
        try:
            await self.auth.require_user_access(
                identity, now=self.clock.now(), command="ytdl" if isinstance(args, YtdlArgs) else "ytmp3"
            )
        except AuthorizationError as exc:
            return UserError(
                code=exc.code,
                message_key=exc.failure.user_message_key,
                safe_context=self.auth.identity_context(identity),
            )

        if isinstance(args, YtdlArgs):
            mode = MediaMode.VIDEO
            preset = args.quality.value
            url = args.url
            quality = args.quality
            bitrate = None
        else:
            mode = MediaMode.AUDIO
            preset = args.bitrate.value
            url = args.url
            quality = None
            bitrate = args.bitrate

        try:
            validated: ValidatedUrl = await self.url_safety.validate(url, now=self.clock.now())
            _ = DownloadRequest(
                source_url=validated.normalized_url,
                mode=mode,
                video_quality=quality,
                audio_bitrate=bitrate,
            )
        except DomainError as exc:
            return UserError(code=exc.code, message_key=exc.failure.user_message_key)

        job_id = JobId(self.ids.job_id())
        now = self.clock.now()
        idem = f"{identity.platform.value}:{request_id}"
        job = Job(
            job_id=job_id,
            idempotency_key=idem,
            owner=identity,
            message_context=context,
            request_mode=mode,
            selected_preset=preset,
            source_display=validated.source_display,
            state=JobState.QUEUED,
            dispatchable=False,
            created_at=now,
            updated_at=now,
        )
        await self.jobs.create(job)
        await self.payloads.put(
            JobPayload(job_id=job_id, source_url=validated.normalized_url, created_at=now)
        )

        lock = self._lock_for(idem)
        async with lock:
            try:
                ref = await self.platform.acknowledge_job(context, job_id, JobState.QUEUED)
            except Exception:
                await self.payloads.delete(job_id)
                await self.jobs.transition(
                    job_id,
                    expected_version=job.version,
                    new_state=JobState.FAILED,
                    error_code=FailureCode.ACKNOWLEDGEMENT_FAILED,
                )
                return UserError(
                    code=FailureCode.ACKNOWLEDGEMENT_FAILED,
                    message_key="failure.acknowledgement_failed",
                )
            result = await self.jobs.attach_message_reference(
                job_id,
                expected_version=job.version,
                message_reference=ref,
                acknowledged_at=self.clock.now(),
            )
            if not isinstance(result, Ok):
                return UserError(
                    code=FailureCode.INTERNAL_ERROR,
                    message_key="failure.internal_error",
                )
        log.info(
            "job accepted",
            extra={
                "event": "job.accepted",
                "job_id": job_id.value,
                "state": JobState.QUEUED.value,
                "platform": identity.platform.value,
                "source_display": validated.source_display,
            },
        )
        return AcceptedJob(job_id=job_id, state=JobState.QUEUED.value)

    async def status(self, *, identity: Identity, args: StatusArgs) -> CommandResult:
        try:
            await self.auth.require_user_access(
                identity, now=self.clock.now(), command="ytdl_status"
            )
        except AuthorizationError as exc:
            return UserError(
                code=exc.code,
                message_key=exc.failure.user_message_key,
                safe_context=self.auth.identity_context(identity),
            )

        if args.job_id is None:
            jobs = await self.jobs.list_owned_recent(identity, limit=self.recent_limit)
            views = tuple(
                StatusView(
                    job_id=j.job_id,
                    state=j.state.value,
                    phase=j.progress.phase.value if j.progress and j.progress.phase else None,
                    percent=j.progress.percent if j.progress else None,
                    warning_codes=tuple(w.value for w in j.warning_codes),
                    error_code=j.error_code.value if j.error_code else None,
                )
                for j in jobs  # type: ignore[attr-defined]
            )
            return StatusView(jobs=views, message_key="status.list_header")

        try:
            job, _snap = await self.auth.require_job_owner(
                args.job_id, identity, now=self.clock.now()
            )
        except (AuthorizationError, NotFoundError):
            return UserError(
                code=FailureCode.NOT_AUTHORIZED,
                message_key="failure.not_authorized",
                safe_context=self.auth.identity_context(identity),
            )
        return StatusView(
            job_id=job.job_id,
            state=job.state.value,
            phase=job.progress.phase.value if job.progress and job.progress.phase else None,
            percent=job.progress.percent if job.progress else None,
            warning_codes=tuple(w.value for w in job.warning_codes),
            error_code=job.error_code.value if job.error_code else None,
            artifact_available=False,
        )

    async def cancel(self, *, identity: Identity, args: CancelArgs) -> CommandResult:
        try:
            job, _ = await self.auth.require_job_owner(
                args.job_id, identity, now=self.clock.now()
            )
        except (AuthorizationError, NotFoundError):
            return UserError(
                code=FailureCode.NOT_AUTHORIZED,
                message_key="failure.not_authorized",
                safe_context=self.auth.identity_context(identity),
            )
        if job.state not in USER_CANCELLABLE_STATES and job.state is not JobState.CANCELLING:
            return StatusView(
                job_id=job.job_id,
                state=job.state.value,
                message_key="status.view",
            )
        result = await self.jobs.request_cancellation(job.job_id, expected_version=job.version)
        if isinstance(result, Ok):
            if result.value.state is JobState.CANCELLING:
                fin = await self.jobs.transition(
                    job.job_id,
                    expected_version=result.value.version,
                    new_state=JobState.CANCELLED,
                )
                if isinstance(fin, Ok):
                    await self.payloads.delete(job.job_id)
                    return StatusView(
                        job_id=job.job_id,
                        state=JobState.CANCELLED.value,
                        message_key="outcome.cancelled",
                    )
            return StatusView(
                job_id=job.job_id,
                state=result.value.state.value,
                message_key="status.view",
            )
        return UserError(
            code=FailureCode.INTERNAL_ERROR,
            message_key="failure.internal_error",
        )

    async def apply_progress(
        self, job_id: JobId, *, expected_version: int, progress: ProgressSnapshot
    ) -> None:
        update = getattr(self.jobs, "update_progress_snapshot", None)
        if update is not None:
            await update(job_id, expected_version=expected_version, progress=progress)
