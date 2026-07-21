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
    AdminCapacitySet,
    AdminRetentionSet,
    AdminStatus,
    AdminView,
    AdminWhitelistAdd,
    AdminWhitelistList,
    AdminWhitelistRemove,
    CommandRequest,
    CommandResult,
    UserError,
)
from ytdlp_bot.domain.enums import FailureCode
from ytdlp_bot.domain.errors import AuthorizationError
from ytdlp_bot.domain.identity import Identity
from ytdlp_bot.domain.settings import validate_setting_value


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


@dataclass
class AdminService:
    auth: AuthorizationService
    settings: SettingsPort
    access: AccessPort
    confirmations: ConfirmationPort
    id_confirmation: Any  # IdGenerator-like
    confirmation_ttl: timedelta = timedelta(seconds=60)

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
        return UserError(code=FailureCode.INVALID_COMMAND, message_key="failure.invalid_command")
