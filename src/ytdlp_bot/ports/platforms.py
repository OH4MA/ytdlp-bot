"""Platform delivery port."""

from __future__ import annotations

from typing import Protocol

from ytdlp_bot.domain.commands import CommandResult
from ytdlp_bot.domain.enums import JobState, PlatformErrorCode, UploadOutcome
from ytdlp_bot.domain.identity import JobId, MessageContext, MessageReference
from ytdlp_bot.domain.progress import ArtifactDescriptor, FinalOutcomeView, ProgressView


class PlatformPort(Protocol):
    """Platform-neutral messaging and upload operations."""

    async def acknowledge_job(
        self,
        context: MessageContext,
        job_id: JobId,
        initial_state: JobState,
    ) -> MessageReference: ...

    async def edit_progress(
        self,
        message_reference: MessageReference,
        view: ProgressView,
    ) -> None: ...

    async def upload_artifact(
        self,
        context: MessageContext,
        descriptor: ArtifactDescriptor,
    ) -> UploadOutcome: ...

    async def send_final(
        self,
        message_reference: MessageReference,
        view: FinalOutcomeView,
    ) -> None: ...

    async def send_command_response(
        self,
        context: MessageContext,
        result: CommandResult,
    ) -> None: ...

    def classify_error(self, exception: BaseException) -> tuple[PlatformErrorCode, bool]:
        """Map SDK failure to code and retryability."""
        ...
