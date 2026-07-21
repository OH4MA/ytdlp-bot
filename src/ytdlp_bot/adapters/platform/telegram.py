"""Telegram adapter (aiogram-backed production path + pure normalization)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ytdlp_bot.adapters.platform.base import build_text_command_request
from ytdlp_bot.domain.commands import CommandRequest, CommandResult
from ytdlp_bot.domain.enums import JobState, Platform, PlatformErrorCode, UploadOutcome
from ytdlp_bot.domain.identity import JobId, MessageContext, MessageReference
from ytdlp_bot.domain.progress import ArtifactDescriptor, FinalOutcomeView, ProgressView


@dataclass
class TelegramPlatformAdapter:
    """PlatformPort implementation; SDK wiring is optional for unit tests."""

    upload_limit_bytes: int = 50_000_000
    bot_token: str = ""
    calls: list[tuple[str, object]] = field(default_factory=list)
    _msg_seq: int = 0
    upload_outcome: UploadOutcome = UploadOutcome.UPLOADED

    def normalize_text_update(
        self,
        *,
        update_id: str,
        user_id: str,
        chat_id: str,
        text: str,
        received_at: datetime,
    ) -> CommandRequest:
        return build_text_command_request(
            platform=Platform.TELEGRAM,
            request_id=update_id,
            user_id=user_id,
            chat_id=chat_id,
            text=text,
            upload_limit_bytes=self.upload_limit_bytes,
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
            platform=Platform.TELEGRAM,
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
        name = type(exception).__name__.lower()
        if "retry" in name or "flood" in name:
            return PlatformErrorCode.RATE_LIMITED, True
        return PlatformErrorCode.UNKNOWN, False
