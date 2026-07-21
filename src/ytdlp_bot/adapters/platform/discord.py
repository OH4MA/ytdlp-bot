"""Discord adapter using discord.py 2.x (gateway)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

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

log = logging.getLogger("ytdlp_bot.discord")

CommandHandler = Callable[[CommandRequest], Awaitable[CommandResult]]


@dataclass
class DiscordPlatformAdapter:
    upload_limit_bytes: int = 10_485_760
    bot_token: str = ""
    command_handler: CommandHandler | None = None
    calls: list[tuple[str, object]] = field(default_factory=list)
    _msg_seq: int = 0
    upload_outcome: UploadOutcome = UploadOutcome.UPLOADED
    _client: object | None = None

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

    async def start_gateway(self) -> None:
        if not self.bot_token or self.command_handler is None:
            log.warning("discord gateway not started: missing token or handler")
            return
        import discord
        from discord import app_commands

        intents = discord.Intents.default()
        client = discord.Client(intents=intents)
        tree = app_commands.CommandTree(client)
        handler = self.command_handler
        adapter = self

        @tree.command(name="ytdl", description="Download video as MP4")
        @app_commands.describe(url="Media URL", quality="Resolution ceiling")
        async def ytdl(interaction: discord.Interaction, url: str, quality: str = "best") -> None:
            await interaction.response.defer(thinking=True)
            req = adapter.normalize_slash(
                interaction_id=str(interaction.id),
                user_id=str(interaction.user.id),
                channel_id=str(interaction.channel_id),
                command=CommandName.YTDL,
                url=url,
                quality=quality,
                received_at=datetime.now(UTC),
            )
            result = await handler(req)
            await interaction.followup.send(f"{result.kind}")

        @client.event
        async def on_ready() -> None:
            await tree.sync()
            log.info("discord ready")

        self._client = client
        await client.start(self.bot_token)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.close()  # type: ignore[union-attr]

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
        if self._client is not None:
            channel = self._client.get_channel(int(context.chat_id))  # type: ignore[union-attr]
            if channel is not None:
                msg = await channel.send(  # type: ignore[union-attr]
                    f"accepted job {job_id.value} state={initial_state.value}"
                )
                ref = MessageReference(
                    platform=Platform.DISCORD,
                    chat_id=context.chat_id,
                    message_id=str(msg.id),
                )
        self.calls.append(("acknowledge_job", (job_id, initial_state)))
        return ref

    async def edit_progress(self, message_reference: MessageReference, view: ProgressView) -> None:
        self.calls.append(("edit_progress", (message_reference, view)))
        if self._client is None:
            return
        channel = self._client.get_channel(int(message_reference.chat_id))  # type: ignore[union-attr]
        if channel is None:
            return
        try:
            msg = await channel.fetch_message(int(message_reference.message_id))  # type: ignore[union-attr]
            await msg.edit(content=f"job {view.job_id.value} {view.state} {view.percent or ''}%")
        except Exception as exc:
            log.debug("discord edit failed: %s", type(exc).__name__)

    async def upload_artifact(
        self, context: MessageContext, descriptor: ArtifactDescriptor
    ) -> UploadOutcome:
        self.calls.append(("upload_artifact", descriptor))
        if descriptor.byte_size > self.upload_limit_bytes:
            return UploadOutcome.TOO_LARGE
        if self._client is None:
            return self.upload_outcome
        import discord

        channel = self._client.get_channel(int(context.chat_id))  # type: ignore[union-attr]
        path = Path(descriptor.storage_key)
        if channel is None or not path.is_file():
            return UploadOutcome.FAILED
        try:
            await channel.send(  # type: ignore[union-attr]
                file=discord.File(path, filename=descriptor.display_name)
            )
            return UploadOutcome.UPLOADED
        except Exception as exc:
            name = type(exc).__name__.lower()
            if "payload" in name or "large" in name:
                return UploadOutcome.TOO_LARGE
            return UploadOutcome.FAILED

    async def send_final(self, message_reference: MessageReference, view: FinalOutcomeView) -> None:
        self.calls.append(("send_final", (message_reference, view)))
        if self._client is None:
            return
        channel = self._client.get_channel(int(message_reference.chat_id))  # type: ignore[union-attr]
        if channel is None:
            return
        try:
            msg = await channel.fetch_message(int(message_reference.message_id))  # type: ignore[union-attr]
            await msg.edit(content=f"job {view.job_id.value} {view.outcome}")
        except Exception:
            await channel.send(f"job {view.job_id.value} {view.outcome}")  # type: ignore[union-attr]

    async def send_command_response(self, context: MessageContext, result: CommandResult) -> None:
        self.calls.append(("send_command_response", (context, result)))
        if self._client is None:
            return
        channel = self._client.get_channel(int(context.chat_id))  # type: ignore[union-attr]
        if channel is not None:
            await channel.send(f"{result.kind}")  # type: ignore[union-attr]

    def classify_error(self, exception: BaseException) -> tuple[PlatformErrorCode, bool]:
        return PlatformErrorCode.UNKNOWN, False
