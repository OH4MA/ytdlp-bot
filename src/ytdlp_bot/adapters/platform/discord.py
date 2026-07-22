"""Discord adapter using discord.py 2.x (gateway)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ytdlp_bot.adapters.platform.base import build_text_command_request
from ytdlp_bot.adapters.platform.command_menu import command_description_map, command_menu_names
from ytdlp_bot.adapters.platform.messages import (
    render_command_result,
    render_final,
    render_job_accepted,
    render_progress,
)
from ytdlp_bot.domain.commands import (
    AdminArgs,
    AdminStatus,
    CommandName,
    CommandRequest,
    CommandResult,
    build_command_arguments,
)
from ytdlp_bot.domain.enums import (
    AudioBitrate,
    JobState,
    Platform,
    PlatformErrorCode,
    UploadOutcome,
    VideoQuality,
)
from ytdlp_bot.domain.identity import Identity, JobId, MessageContext, MessageReference
from ytdlp_bot.domain.progress import ArtifactDescriptor, FinalOutcomeView, ProgressView

log = logging.getLogger("ytdlp_bot.discord")

CommandHandler = Callable[[CommandRequest], Awaitable[CommandResult]]

# zh-TW descriptions shared with Telegram command menu.
_DESC = command_description_map()


@dataclass(frozen=True, slots=True)
class SlashOptionSpec:
    name: str
    description: str
    required: bool = False
    choices: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SlashCommandSpec:
    name: str
    description: str
    options: tuple[SlashOptionSpec, ...] = ()


def canonical_slash_command_specs() -> tuple[SlashCommandSpec, ...]:
    """Exact six canonical Discord application commands (schema snapshot source)."""
    qualities = tuple(q.value for q in VideoQuality)
    bitrates = tuple(b.value for b in AudioBitrate)
    return (
        SlashCommandSpec(
            name=CommandName.YTDL.value,
            description=_DESC[CommandName.YTDL],
            options=(
                SlashOptionSpec("url", "媒體網址", required=True),
                SlashOptionSpec("quality", "解析度上限", choices=qualities),
            ),
        ),
        SlashCommandSpec(
            name=CommandName.YTMP3.value,
            description=_DESC[CommandName.YTMP3],
            options=(
                SlashOptionSpec("url", "媒體網址", required=True),
                SlashOptionSpec("bitrate", "MP3 位元率", choices=bitrates),
            ),
        ),
        SlashCommandSpec(
            name=CommandName.YTDL_STATUS.value,
            description=_DESC[CommandName.YTDL_STATUS],
            options=(
                SlashOptionSpec("job_id", "工作 ID, 可省略以列出最近工作"),
                SlashOptionSpec("renew", "重新簽發下載連結", choices=("true", "false")),
            ),
        ),
        SlashCommandSpec(
            name=CommandName.YTDL_CANCEL.value,
            description=_DESC[CommandName.YTDL_CANCEL],
            options=(SlashOptionSpec("job_id", "工作 ID", required=True),),
        ),
        SlashCommandSpec(
            name=CommandName.YTDL_HELP.value,
            description=_DESC[CommandName.YTDL_HELP],
        ),
        SlashCommandSpec(
            name=CommandName.YTDL_ADMIN.value,
            description=_DESC[CommandName.YTDL_ADMIN],
            options=(
                SlashOptionSpec(
                    "action",
                    "管理員動作",
                    required=True,
                    choices=(
                        "status",
                        "retention_set",
                        "capacity_set",
                        "access_mode_set",
                        "whitelist_add",
                        "whitelist_remove",
                        "whitelist_list",
                        "cancel",
                        "artifact_delete",
                        "setting_reset",
                    ),
                ),
                SlashOptionSpec("value", "動作參數: 秒數、位元組、模式、ID 等"),
                SlashOptionSpec("confirmation_id", "容量確認 ID, 若需要"),
            ),
        ),
    )


def registered_command_names() -> tuple[str, ...]:
    return command_menu_names()


@dataclass
class DiscordPlatformAdapter:
    upload_limit_bytes: int = 10_485_760
    bot_token: str = ""
    command_handler: CommandHandler | None = None
    calls: list[tuple[str, object]] = field(default_factory=list)
    _msg_seq: int = 0
    upload_outcome: UploadOutcome = UploadOutcome.UPLOADED
    _client: object | None = None
    _tree: object | None = None
    registered_commands: list[str] = field(default_factory=list)

    def command_schema_snapshot(self) -> list[dict[str, Any]]:
        """Deterministic schema for tests (no Discord SDK required)."""
        out: list[dict[str, Any]] = []
        for spec in canonical_slash_command_specs():
            out.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "options": [
                        {
                            "name": opt.name,
                            "description": opt.description,
                            "required": opt.required,
                            "choices": list(opt.choices),
                        }
                        for opt in spec.options
                    ],
                }
            )
        return out

    def normalize_text_like(
        self,
        *,
        interaction_id: str,
        user_id: str,
        channel_id: str,
        text: str,
        received_at: datetime,
        upload_limit_bytes: int | None = None,
    ) -> CommandRequest:
        limit = upload_limit_bytes if upload_limit_bytes is not None else self.upload_limit_bytes
        return build_text_command_request(
            platform=Platform.DISCORD,
            request_id=interaction_id,
            user_id=user_id,
            chat_id=channel_id,
            text=text,
            upload_limit_bytes=limit,
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
        admin_action_name: str | None = None,
        admin_value: str | None = None,
        confirmation_id: str | None = None,
        received_at: datetime,
        upload_limit_bytes: int | None = None,
    ) -> CommandRequest:
        limit = upload_limit_bytes if upload_limit_bytes is not None else self.upload_limit_bytes
        admin_action = None
        if command is CommandName.YTDL_ADMIN:
            admin_action = self._parse_admin_action(
                admin_action_name or "status",
                value=admin_value,
                confirmation_id=confirmation_id,
            )
        args = build_command_arguments(
            command,
            url=url,
            quality=quality,
            bitrate=bitrate,
            job_id=job_id,
            renew=renew,
            admin_action=admin_action,
        )
        return CommandRequest(
            request_id=interaction_id,
            identity=Identity(platform=Platform.DISCORD, user_id=user_id),
            context=MessageContext(
                platform=Platform.DISCORD,
                chat_id=channel_id,
                response_target=channel_id,
                effective_upload_limit_bytes=limit,
            ),
            command=command,
            arguments=args,
            received_at=received_at,
        )

    def _parse_admin_action(
        self,
        action_name: str,
        *,
        value: str | None,
        confirmation_id: str | None,
    ) -> object:
        """Map Discord admin action choice into AdminAction DTOs via shared grammar."""
        from ytdlp_bot.domain.commands import parse_admin_args

        tokens = (value or "").split()
        if action_name == "status":
            parts = ["status"]
        elif action_name == "retention_set":
            parts = ["retention", "set", tokens[0] if tokens else "12h"]
        elif action_name == "link_expiry_set":
            parts = ["link_expiry", "set", tokens[0] if tokens else "1h"]
        elif action_name == "capacity_set":
            parts = ["capacity", "set", tokens[0] if tokens else "1GiB"]
            if confirmation_id:
                parts.append(confirmation_id)
        elif action_name == "access_mode_set":
            parts = ["access_mode", "set", tokens[0] if tokens else "allow_all"]
        elif action_name == "whitelist_add":
            parts = ["whitelist", "add", *tokens]
        elif action_name == "whitelist_remove":
            parts = ["whitelist", "remove", *tokens]
        elif action_name == "whitelist_list":
            parts = ["whitelist", "list", *tokens]
        elif action_name == "cancel":
            parts = ["cancel", tokens[0] if tokens else ""]
        elif action_name == "artifact_delete":
            parts = ["artifact", "delete", tokens[0] if tokens else ""]
        elif action_name == "setting_reset":
            parts = ["setting", "reset", tokens[0] if tokens else "retention_seconds"]
            if confirmation_id:
                parts.append(confirmation_id)
        else:
            parts = ["status"]
        try:
            admin_args = parse_admin_args(parts)
            assert isinstance(admin_args, AdminArgs)
            return admin_args.action
        except Exception:
            return AdminStatus()

    def register_commands_on_tree(self, tree: Any) -> Sequence[str]:
        """Register all six canonical slash commands on a discord.app_commands.CommandTree."""
        from discord import app_commands

        handler = self.command_handler
        adapter = self
        names: list[str] = []

        def _limit_from_interaction(interaction: Any) -> int:
            # Prefer dynamic attachment_size_limit when Discord provides it.
            raw = getattr(interaction, "attachment_size_limit", None)
            if isinstance(raw, int) and raw > 0:
                return raw
            return adapter.upload_limit_bytes

        async def _dispatch(
            interaction: Any,
            command: CommandName,
            **kwargs: Any,
        ) -> None:
            if handler is None:
                return
            await interaction.response.defer(thinking=True)
            channel_id = str(interaction.channel_id or 0)
            req = adapter.normalize_slash(
                interaction_id=str(interaction.id),
                user_id=str(interaction.user.id),
                channel_id=channel_id,
                command=command,
                received_at=datetime.now(UTC),
                upload_limit_bytes=_limit_from_interaction(interaction),
                **kwargs,
            )
            log.info(
                "discord command received",
                extra={
                    "event": "platform.command",
                    "platform": "discord",
                    "command": command.value,
                    "request_id": req.request_id,
                },
            )
            try:
                result = await handler(req)
            except Exception:
                log.exception(
                    "discord command handler failed",
                    extra={
                        "event": "platform.command_error",
                        "platform": "discord",
                        "command": command.value,
                        "request_id": req.request_id,
                    },
                )
                raise
            log.info(
                "discord command result",
                extra={
                    "event": "platform.command_result",
                    "platform": "discord",
                    "command": command.value,
                    "kind": result.kind,
                    "request_id": req.request_id,
                },
            )
            # Never persist interaction token; followup is transport only.
            # AcceptedJob already posted via acknowledge_job; close thinking ephemerally.
            if result.kind == "accepted_job":
                await interaction.followup.send("已開始處理。", ephemeral=True)
                return
            text = render_command_result(result)
            if text is not None:
                await interaction.followup.send(text)
            else:
                await interaction.followup.send("已處理。", ephemeral=True)

        @tree.command(name="ytdl", description=_DESC[CommandName.YTDL])
        @app_commands.describe(url="媒體網址", quality="解析度上限")
        @app_commands.choices(
            quality=[app_commands.Choice(name=q.value, value=q.value) for q in VideoQuality]
        )
        async def ytdl(interaction: Any, url: str, quality: str = VideoQuality.BEST.value) -> None:
            await _dispatch(interaction, CommandName.YTDL, url=url, quality=quality)

        names.append("ytdl")

        @tree.command(name="ytmp3", description=_DESC[CommandName.YTMP3])
        @app_commands.describe(url="媒體網址", bitrate="MP3 位元率")
        @app_commands.choices(
            bitrate=[app_commands.Choice(name=b.value, value=b.value) for b in AudioBitrate]
        )
        async def ytmp3(interaction: Any, url: str, bitrate: str = AudioBitrate.K320.value) -> None:
            await _dispatch(interaction, CommandName.YTMP3, url=url, bitrate=bitrate)

        names.append("ytmp3")

        @tree.command(name="ytdl_status", description=_DESC[CommandName.YTDL_STATUS])
        @app_commands.describe(job_id="工作 ID", renew="重新簽發下載連結")
        async def ytdl_status(
            interaction: Any, job_id: str | None = None, renew: bool = False
        ) -> None:
            await _dispatch(
                interaction,
                CommandName.YTDL_STATUS,
                job_id=job_id,
                renew=renew,
            )

        names.append("ytdl_status")

        @tree.command(name="ytdl_cancel", description=_DESC[CommandName.YTDL_CANCEL])
        @app_commands.describe(job_id="工作 ID")
        async def ytdl_cancel(interaction: Any, job_id: str) -> None:
            await _dispatch(interaction, CommandName.YTDL_CANCEL, job_id=job_id)

        names.append("ytdl_cancel")

        @tree.command(name="ytdl_help", description=_DESC[CommandName.YTDL_HELP])
        async def ytdl_help(interaction: Any) -> None:
            await _dispatch(interaction, CommandName.YTDL_HELP)

        names.append("ytdl_help")

        @tree.command(name="ytdl_admin", description=_DESC[CommandName.YTDL_ADMIN])
        @app_commands.describe(
            action="管理員動作",
            value="動作參數",
            confirmation_id="容量確認 ID",
        )
        @app_commands.choices(
            action=[
                app_commands.Choice(name=n, value=n)
                for n in (
                    "status",
                    "retention_set",
                    "capacity_set",
                    "access_mode_set",
                    "whitelist_add",
                    "whitelist_remove",
                    "whitelist_list",
                    "cancel",
                    "artifact_delete",
                    "setting_reset",
                )
            ]
        )
        async def ytdl_admin(
            interaction: Any,
            action: str,
            value: str | None = None,
            confirmation_id: str | None = None,
        ) -> None:
            await _dispatch(
                interaction,
                CommandName.YTDL_ADMIN,
                admin_action_name=action,
                admin_value=value,
                confirmation_id=confirmation_id,
            )

        names.append("ytdl_admin")

        self.registered_commands = list(names)
        self._tree = tree
        return tuple(names)

    async def start_gateway(self) -> None:
        if not self.bot_token or self.command_handler is None:
            log.warning("discord gateway not started: missing token or handler")
            return
        import discord

        intents = discord.Intents.default()
        client = discord.Client(intents=intents)
        tree = discord.app_commands.CommandTree(client)
        self.register_commands_on_tree(tree)

        @client.event
        async def on_ready() -> None:
            # Global slash sync so the command menu appears for users (may take a short while).
            synced = await tree.sync()
            log.info(
                "discord slash commands synced count=%s names=%s",
                len(synced) if synced is not None else 0,
                ",".join(self.registered_commands),
                extra={
                    "event": "platform.commands_registered",
                    "platform": "discord",
                    "command": ",".join(self.registered_commands),
                },
            )

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
        text = render_job_accepted(job_id, initial_state)
        ref = MessageReference(
            platform=Platform.DISCORD,
            chat_id=context.chat_id,
            message_id=str(self._msg_seq),
        )
        if self._client is not None:
            channel = self._client.get_channel(int(context.chat_id))  # type: ignore[union-attr]
            if channel is not None:
                msg = await channel.send(text)  # type: ignore[union-attr]
                ref = MessageReference(
                    platform=Platform.DISCORD,
                    chat_id=context.chat_id,
                    message_id=str(msg.id),
                )
        log.info(
            "discord job acknowledged",
            extra={
                "event": "platform.ack",
                "platform": "discord",
                "job_id": job_id.value,
                "state": initial_state.value,
            },
        )
        self.calls.append(("acknowledge_job", (job_id, initial_state, ref)))
        return ref

    async def edit_progress(self, message_reference: MessageReference, view: ProgressView) -> None:
        self.calls.append(("edit_progress", (message_reference, view)))
        if self._client is None:
            return
        channel = self._client.get_channel(int(message_reference.chat_id))  # type: ignore[union-attr]
        if channel is None:
            return
        text = render_progress(view)
        try:
            msg = await channel.fetch_message(int(message_reference.message_id))  # type: ignore[union-attr]
            await msg.edit(content=text)
        except Exception as exc:
            log.debug(
                "discord edit failed: %s",
                type(exc).__name__,
                extra={
                    "event": "platform.edit_progress_failed",
                    "platform": "discord",
                    "job_id": view.job_id.value,
                },
            )

    async def upload_artifact(
        self, context: MessageContext, descriptor: ArtifactDescriptor
    ) -> UploadOutcome:
        self.calls.append(("upload_artifact", descriptor))
        limit = context.effective_upload_limit_bytes or self.upload_limit_bytes
        if descriptor.byte_size > limit:
            return UploadOutcome.TOO_LARGE
        if self._client is None:
            return self.upload_outcome
        import discord

        channel = self._client.get_channel(int(context.chat_id))  # type: ignore[union-attr]
        if channel is None or not descriptor.local_path:
            return UploadOutcome.FAILED
        path = Path(descriptor.local_path)
        if not path.is_file():
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
            if "rate" in name or "429" in name:
                return UploadOutcome.RATE_LIMITED
            return UploadOutcome.FAILED

    async def send_final(self, message_reference: MessageReference, view: FinalOutcomeView) -> None:
        """Post completion as a new message (do not edit the progress bubble)."""
        self.calls.append(("send_final", (message_reference, view)))
        content = render_final(view)
        log.info(
            "discord final outcome",
            extra={
                "event": "platform.final",
                "platform": "discord",
                "job_id": view.job_id.value,
                "outcome": view.outcome,
                "error_code": view.error_code.value if view.error_code else None,
            },
        )
        if self._client is None:
            return
        channel = self._client.get_channel(int(message_reference.chat_id))  # type: ignore[union-attr]
        if channel is None:
            return
        await channel.send(content)  # type: ignore[union-attr]

    async def send_command_response(self, context: MessageContext, result: CommandResult) -> None:
        self.calls.append(("send_command_response", (context, result)))
        text = render_command_result(result)
        if text is None or self._client is None:
            return
        channel = self._client.get_channel(int(context.chat_id))  # type: ignore[union-attr]
        if channel is not None:
            await channel.send(text)  # type: ignore[union-attr]

    def classify_error(self, exception: BaseException) -> tuple[PlatformErrorCode, bool]:
        name = type(exception).__name__.lower()
        msg = str(exception).lower()
        if "429" in msg or "rate" in name or "rate" in msg:
            return PlatformErrorCode.RATE_LIMITED, True
        if "forbidden" in name or "403" in msg:
            return PlatformErrorCode.FORBIDDEN, False
        if "payload" in name or "too large" in msg or "entity too large" in msg:
            return PlatformErrorCode.PAYLOAD_TOO_LARGE, False
        if "timeout" in name or "temporary" in msg or "503" in msg:
            return PlatformErrorCode.TEMPORARILY_UNAVAILABLE, True
        return PlatformErrorCode.UNKNOWN, False
