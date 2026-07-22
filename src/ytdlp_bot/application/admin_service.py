"""Administrator mutations with two-step capacity confirmation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from ytdlp_bot.application.authorization import AuthorizationService
from ytdlp_bot.domain.commands import (
    AdminAccessModeSet,
    AdminArgs,
    AdminArtifactDelete,
    AdminCancel,
    AdminCapacitySet,
    AdminRetentionSet,
    AdminSettingReset,
    AdminStatus,
    AdminView,
    AdminWhitelistAdd,
    AdminWhitelistList,
    AdminWhitelistPending,
    AdminWhitelistRemove,
    CommandRequest,
    CommandResult,
    StatusView,
    UserError,
)
from ytdlp_bot.domain.enums import DeletionReason, FailureCode, JobState
from ytdlp_bot.domain.errors import AuthorizationError
from ytdlp_bot.domain.identity import Identity
from ytdlp_bot.domain.settings import validate_setting_value
from ytdlp_bot.ports.results import Ok


class SettingsPort(Protocol):
    async def effective_values(self) -> dict[str, object]: ...
    async def set_override(
        self, key: str, value: object, *, updated_by: Identity, now: datetime
    ): ...


class AccessPort(Protocol):
    async def set_mode(self, mode, *, updated_by: Identity, now: datetime) -> None: ...
    async def list_whitelist(self, *, platform=None): ...
    async def add_identity(self, identity: Identity, *, now: datetime) -> bool: ...
    async def remove_identity(self, identity: Identity) -> bool: ...


class ConfirmationPort(Protocol):
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


class JobAdminPort(Protocol):
    async def get(self, job_id: object) -> object | None: ...
    async def request_cancellation(self, job_id: object, *, expected_version: int) -> object: ...
    async def transition(
        self, job_id: object, *, expected_version: int, new_state: JobState, error_code=None
    ) -> object: ...


class ArtifactAdminPort(Protocol):
    async def get(self, artifact_id: object) -> object | None: ...
    async def get_for_job(self, job_id: object) -> object | None: ...
    async def mark_deletion_pending(
        self,
        artifact_id: object,
        *,
        expected_version: int,
        reason: DeletionReason,
        now: datetime,
    ) -> object: ...
    async def finish_deletion(
        self, artifact_id: object, *, expected_version: int, now: datetime
    ) -> object: ...


class FileAdminPort(Protocol):
    async def delete(self, storage_key: str) -> None: ...


@dataclass
class AdminService:
    auth: AuthorizationService
    settings: SettingsPort
    access: AccessPort
    confirmations: ConfirmationPort
    id_confirmation: Any  # IdGenerator-like
    confirmation_ttl: timedelta = timedelta(seconds=60)
    jobs: JobAdminPort | None = None
    artifacts: ArtifactAdminPort | None = None
    files: FileAdminPort | None = None

    async def handle(self, request: CommandRequest, args: AdminArgs) -> CommandResult:
        try:
            await self.auth.require_administrator(request.identity)
        except AuthorizationError:
            return UserError(code=FailureCode.NOT_AUTHORIZED, message_key="admin.not_admin")
        action = args.action
        now = request.received_at

        if isinstance(action, AdminStatus):
            values = await self.settings.effective_values()
            return AdminView(
                message_key="admin.status",
                safe_fields={
                    "capacity": int(values.get("capacity_bytes", 0)),
                    "queued": 0,
                    "active": 0,
                    "used": 0,
                    "cleanup": "ok",
                },
            )
        if isinstance(action, AdminRetentionSet):
            validate_setting_value("retention_seconds", action.duration_seconds)
            old = (await self.settings.effective_values()).get("retention_seconds")
            await self.settings.set_override(
                "retention_seconds",
                action.duration_seconds,
                updated_by=request.identity,
                now=now,
            )
            return AdminView(
                message_key="admin.setting_updated",
                safe_fields={
                    "key": "retention_seconds",
                    "old": int(old or 0),
                    "new": action.duration_seconds,
                },
            )
        if isinstance(action, AdminAccessModeSet):
            await self.access.set_mode(action.mode, updated_by=request.identity, now=now)
            return AdminView(
                message_key="admin.setting_updated",
                safe_fields={"key": "access_mode", "old": "", "new": action.mode.value},
            )
        if isinstance(action, AdminWhitelistAdd):
            await self.access.add_identity(action.identity, now=now)
            await self.auth.clear_access_denial(action.identity)
            return AdminView(message_key="admin.view", safe_fields={"op": "whitelist_add"})
        if isinstance(action, AdminWhitelistRemove):
            await self.access.remove_identity(action.identity)
            return AdminView(message_key="admin.view", safe_fields={"op": "whitelist_remove"})
        if isinstance(action, AdminWhitelistList):
            items = list(await self.access.list_whitelist(platform=action.platform))
            return AdminView(
                message_key="admin.view",
                safe_fields={"count": len(items)},
            )
        if isinstance(action, AdminWhitelistPending):
            denials = await self.auth.list_access_denials(now=now, limit=20)
            if not denials:
                return AdminView(message_key="admin.whitelist_pending_empty")
            lines: list[str] = []
            for d in denials:
                ident = d.identity
                cmd = d.last_command or "-"
                lines.append(
                    f"• {ident.display()} — {d.attempt_count}x, cmd={cmd}\n"
                    f"  /ytdl_admin whitelist add {ident.platform.value} {ident.user_id}"
                )
            return AdminView(
                message_key="admin.whitelist_pending",
                safe_fields={"count": len(denials), "body": "\n".join(lines)},
            )
        if isinstance(action, AdminCapacitySet):
            validate_setting_value("capacity_bytes", action.capacity_bytes)
            fingerprint = hashlib.sha256(
                f"capacity_set:{action.capacity_bytes}".encode()
            ).hexdigest()
            snapshot = f"cap={action.capacity_bytes}"
            if action.confirmation_id is None:
                cid = self.id_confirmation.confirmation_id()
                await self.confirmations.create(
                    cid,
                    action_fingerprint=fingerprint,
                    owner=request.identity,
                    expires_at=now + self.confirmation_ttl,
                    projected_snapshot=snapshot,
                )
                return AdminView(
                    message_key="admin.confirmation_required",
                    safe_fields={"confirmation_id": cid},
                )
            ok = await self.confirmations.consume_if_matching(
                action.confirmation_id,
                action_fingerprint=fingerprint,
                owner=request.identity,
                now=now,
                projected_snapshot=snapshot,
            )
            if not ok:
                return UserError(
                    code=FailureCode.INVALID_COMMAND,
                    message_key="failure.invalid_command",
                )
            await self.settings.set_override(
                "capacity_bytes",
                action.capacity_bytes,
                updated_by=request.identity,
                now=now,
            )
            return AdminView(
                message_key="admin.setting_updated",
                safe_fields={
                    "key": "capacity_bytes",
                    "old": 0,
                    "new": action.capacity_bytes,
                },
            )
        if isinstance(action, AdminSettingReset):
            key = action.setting_key
            if not key:
                return UserError(
                    code=FailureCode.INVALID_COMMAND, message_key="failure.invalid_command"
                )
            # Reset means re-applying the documented default for known keys.
            defaults = {
                "retention_seconds": 43200,
                "link_expiry_seconds": 3600,
            }
            if key not in defaults:
                return UserError(
                    code=FailureCode.INVALID_COMMAND, message_key="failure.invalid_command"
                )
            value = defaults[key]
            validate_setting_value(key, value)
            await self.settings.set_override(
                key,
                value,
                updated_by=request.identity,
                now=now,
            )
            return AdminView(
                message_key="admin.setting_updated",
                safe_fields={"key": key, "old": "", "new": value},
            )
        if isinstance(action, AdminCancel):
            if self.jobs is None:
                return UserError(
                    code=FailureCode.INTERNAL_ERROR, message_key="failure.internal_error"
                )
            job = await self.jobs.get(action.job_id)
            if job is None:
                return UserError(
                    code=FailureCode.NOT_AUTHORIZED, message_key="failure.not_authorized"
                )
            version = getattr(job, "version", 0)
            state = getattr(job, "state", None)
            if state in {JobState.COMPLETED, JobState.CANCELLED, JobState.FAILED}:
                return StatusView(
                    job_id=action.job_id,
                    state=state.value if state else "unknown",
                    message_key="status.view",
                )
            result = await self.jobs.request_cancellation(
                action.job_id, expected_version=int(version)
            )
            if isinstance(result, Ok):
                current = result.value
                fin = await self.jobs.transition(
                    action.job_id,
                    expected_version=getattr(current, "version", version),
                    new_state=JobState.CANCELLED,
                )
                if isinstance(fin, Ok):
                    return StatusView(
                        job_id=action.job_id,
                        state=JobState.CANCELLED.value,
                        message_key="outcome.cancelled",
                    )
            return UserError(code=FailureCode.INTERNAL_ERROR, message_key="failure.internal_error")
        if isinstance(action, AdminArtifactDelete):
            if self.artifacts is None or self.files is None:
                return UserError(
                    code=FailureCode.INTERNAL_ERROR, message_key="failure.internal_error"
                )
            art = await self.artifacts.get(action.artifact_id)
            if art is None:
                return UserError(
                    code=FailureCode.NOT_AUTHORIZED, message_key="failure.not_authorized"
                )
            pending = await self.artifacts.mark_deletion_pending(
                action.artifact_id,
                expected_version=int(getattr(art, "version", 0)),
                reason=DeletionReason.ADMINISTRATOR,
                now=now,
            )
            if not isinstance(pending, Ok):
                return UserError(
                    code=FailureCode.INTERNAL_ERROR, message_key="failure.internal_error"
                )
            try:
                await self.files.delete(str(getattr(art, "storage_key", "")))
            except Exception:
                return UserError(
                    code=FailureCode.INTERNAL_ERROR, message_key="failure.internal_error"
                )
            fin = await self.artifacts.finish_deletion(
                action.artifact_id,
                expected_version=getattr(pending.value, "version", 0),
                now=now,
            )
            if isinstance(fin, Ok):
                return AdminView(
                    message_key="admin.view",
                    safe_fields={"op": "artifact_delete", "id": action.artifact_id.value},
                )
            return UserError(code=FailureCode.INTERNAL_ERROR, message_key="failure.internal_error")
        return UserError(code=FailureCode.INVALID_COMMAND, message_key="failure.invalid_command")
