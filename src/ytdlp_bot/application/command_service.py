"""Canonical command routing (platform-neutral)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ytdlp_bot.application.job_service import JobService
from ytdlp_bot.domain.commands import (
    AdminArgs,
    CancelArgs,
    CommandName,
    CommandRequest,
    CommandResult,
    HelpArgs,
    HelpView,
    StatusArgs,
    UserError,
    YtdlArgs,
    Ytmp3Args,
)
from ytdlp_bot.domain.enums import FailureCode
from ytdlp_bot.domain.errors import AuthorizationError
from ytdlp_bot.domain.locale import format_message, load_zh_tw_catalog


class AdminServicePort(Protocol):
    async def handle(self, request: CommandRequest, args: AdminArgs) -> CommandResult: ...


@dataclass
class CommandService:
    jobs: JobService
    admin: AdminServicePort | None = None
    admission_open: bool = True

    async def handle(self, request: CommandRequest) -> CommandResult:
        if not self.admission_open and request.command not in {
            CommandName.YTDL_HELP,
            CommandName.YTDL_STATUS,
        }:
            return UserError(
                code=FailureCode.INTERNAL_ERROR,
                message_key="failure.internal_error",
                safe_context={"reason": "not_ready"},
            )

        if request.command is CommandName.YTDL:
            assert isinstance(request.arguments, YtdlArgs)
            return await self.jobs.submit_download(
                request_id=request.request_id,
                identity=request.identity,
                context=request.context,
                args=request.arguments,
            )
        if request.command is CommandName.YTMP3:
            assert isinstance(request.arguments, Ytmp3Args)
            return await self.jobs.submit_download(
                request_id=request.request_id,
                identity=request.identity,
                context=request.context,
                args=request.arguments,
            )
        if request.command is CommandName.YTDL_STATUS:
            assert isinstance(request.arguments, StatusArgs)
            return await self.jobs.status(identity=request.identity, args=request.arguments)
        if request.command is CommandName.YTDL_CANCEL:
            assert isinstance(request.arguments, CancelArgs)
            return await self.jobs.cancel(identity=request.identity, args=request.arguments)
        if request.command is CommandName.YTDL_HELP:
            assert isinstance(request.arguments, HelpArgs)
            try:
                await self.jobs.auth.require_user_access(request.identity)
            except AuthorizationError as exc:
                return UserError(code=exc.code, message_key=exc.failure.user_message_key)
            catalog = load_zh_tw_catalog()
            _ = format_message(catalog, "help.main", legal=catalog.get("legal.use_reminder", ""))
            return HelpView()
        if request.command is CommandName.YTDL_ADMIN:
            assert isinstance(request.arguments, AdminArgs)
            if self.admin is None:
                return UserError(
                    code=FailureCode.NOT_AUTHORIZED,
                    message_key="admin.not_admin",
                )
            return await self.admin.handle(request, request.arguments)
        return UserError(
            code=FailureCode.INVALID_COMMAND,
            message_key="failure.invalid_command",
        )
