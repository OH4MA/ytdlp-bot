"""Recording fake platform port."""

from __future__ import annotations

from dataclasses import dataclass, field

from ytdlp_bot.domain.commands import CommandResult
from ytdlp_bot.domain.enums import JobState, PlatformErrorCode, UploadOutcome
from ytdlp_bot.domain.identity import JobId, MessageContext, MessageReference
from ytdlp_bot.domain.progress import ArtifactDescriptor, FinalOutcomeView, ProgressView


@dataclass
class FakePlatformPort:
    """Records all platform interactions for assertions."""

    upload_outcome: UploadOutcome = UploadOutcome.UPLOADED
    calls: list[tuple[str, object]] = field(default_factory=list)
    _msg_seq: int = 0

    def reset(self) -> None:
        self.calls.clear()
        self._msg_seq = 0
        self.upload_outcome = UploadOutcome.UPLOADED

    async def acknowledge_job(
        self,
        context: MessageContext,
        job_id: JobId,
        initial_state: JobState,
    ) -> MessageReference:
        self._msg_seq += 1
        ref = MessageReference(
            platform=context.platform,
            chat_id=context.chat_id,
            message_id=str(self._msg_seq),
        )
        self.calls.append(("acknowledge_job", (context, job_id, initial_state, ref)))
        return ref

    async def edit_progress(
        self,
        message_reference: MessageReference,
        view: ProgressView,
    ) -> None:
        self.calls.append(("edit_progress", (message_reference, view)))

    async def upload_artifact(
        self,
        context: MessageContext,
        descriptor: ArtifactDescriptor,
    ) -> UploadOutcome:
        self.calls.append(("upload_artifact", (context, descriptor)))
        return self.upload_outcome

    async def send_final(
        self,
        message_reference: MessageReference,
        view: FinalOutcomeView,
    ) -> None:
        self.calls.append(("send_final", (message_reference, view)))

    async def send_command_response(
        self,
        context: MessageContext,
        result: CommandResult,
    ) -> None:
        self.calls.append(("send_command_response", (context, result)))

    def classify_error(self, exception: BaseException) -> tuple[PlatformErrorCode, bool]:
        self.calls.append(("classify_error", exception))
        return PlatformErrorCode.UNKNOWN, False
