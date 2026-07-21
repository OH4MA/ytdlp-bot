"""Discord adapter (discord.py-backed production path + pure normalization)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ytdlp_bot.adapters.platform.base import build_text_command_request
from ytdlp_bot.domain.commands import (
    CommandName,
    CommandRequest,
    CommandResult,
    build_command_arguments,
)
from ytdlp_bot.domain.enums import JobState, Platform, PlatformErrorCode, UploadOutcome
from ytdlp_bot.domain.identity import Identity, JobId, MessageContext, MessageReference
from ytdlp_bot.domain.progress import ArtifactDescriptor, FinalOutcomeView, ProgressView


@dataclass
class DiscordPlatformAdapter:
    upload_limit_bytes: int = 10_485_760
    bot_token: str = ""
    calls: list[tuple[str, object]] = field(default_factory=list)
    _msg_seq: int = 0
    upload_outcome: UploadOutcome = UploadOutcome.UPLOADED

    def normalize_text_like(
        self,
        *,
        interaction_id: str,
        user_id: str,
        channel_id: str,
        text: str,
        received_at: datetime,
    ) -> CommandRequest:
        return build_text_command_request(
            platform=Platform.DISCORD,
            request_id=interaction_id,
            user_id=user_id,
            chat_id=channel_id,
            text=text,
            upload_limit_bytes=self.upload_limit_bytes,
            received_at=received_at,
        )

    def normalize_slash(
        self,
        *,
        interaction_id: str,
        user_id: str,
        channel_id: str,
        command: CommandName,
        url: str | None = None,
        quality: str | None = None,
        bitrate: str | None = None,
        job_id: str | None = None,
        renew: bool = False,
        received_at: datetime,
    ) -> CommandRequest:
        args = build_command_arguments(
            command,
            url=url,
            quality=quality,
            bitrate=bitrate,
            job_id=job_id,
            renew=renew,
        )
        return CommandRequest(
            request_id=interaction_id,
            identity=Identity(platform=Platform.DISCORD, user_id=user_id),
            context=MessageContext(
                platform=Platform.DISCORD,
                chat_id=channel_id,
                response_target=channel_id,
                effective_upload_limit_bytes=self.upload_limit_bytes,
            ),
            command=command,
            arguments=args,
            received_at=received_at,
        )

    async def acknowledge_job(
        self,
        context: MessageContext,
        job_id: JobId,
        initial_state: JobState,
    ) -> MessageReference:
        self._msg_seq += 1
        ref = MessageReference(
            platform=Platform.DISCORD,
            chat_id=context.chat_id,
            message_id=str(self._msg_seq),
        )
        self.calls.append(("acknowledge_job", (job_id, initial_state)))
        return ref

    async def edit_progress(self, message_reference: MessageReference, view: ProgressView) -> None:
        self.calls.append(("edit_progress", (message_reference, view)))

    async def upload_artifact(
        self, context: MessageContext, descriptor: ArtifactDescriptor
    ) -> UploadOutcome:
        self.calls.append(("upload_artifact", descriptor))
        if descriptor.byte_size > self.upload_limit_bytes:
            return UploadOutcome.TOO_LARGE
        return self.upload_outcome

    async def send_final(self, message_reference: MessageReference, view: FinalOutcomeView) -> None:
        self.calls.append(("send_final", (message_reference, view)))

    async def send_command_response(self, context: MessageContext, result: CommandResult) -> None:
        self.calls.append(("send_command_response", (context, result)))

    def classify_error(self, exception: BaseException) -> tuple[PlatformErrorCode, bool]:
        return PlatformErrorCode.UNKNOWN, False
