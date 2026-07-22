"""Telegram adapter using aiogram 3.x (long polling)."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ytdlp_bot.adapters.platform.base import build_text_command_request
from ytdlp_bot.adapters.platform.command_menu import (
    command_menu_entries,
    command_menu_names,
    telegram_bot_command_dicts,
    telegram_reply_keyboard_rows,
)
from ytdlp_bot.adapters.platform.messages import (
    render_command_result,
    render_final,
    render_job_accepted,
    render_progress,
)
from ytdlp_bot.domain.commands import CommandRequest, CommandResult
from ytdlp_bot.domain.enums import JobState, Platform, PlatformErrorCode, UploadOutcome
from ytdlp_bot.domain.identity import JobId, MessageContext, MessageReference
from ytdlp_bot.domain.progress import ArtifactDescriptor, FinalOutcomeView, ProgressView

log = logging.getLogger("ytdlp_bot.telegram")

CommandHandler = Callable[[CommandRequest], Awaitable[CommandResult]]


@dataclass
class TelegramPlatformAdapter:
    """Production Telegram PlatformPort with optional recording for tests."""

    upload_limit_bytes: int = 50_000_000
    bot_token: str = ""
    command_handler: CommandHandler | None = None
    calls: list[tuple[str, object]] = field(default_factory=list)
    _msg_seq: int = 0
    upload_outcome: UploadOutcome = UploadOutcome.UPLOADED
    _bot: object | None = None
    _dp: object | None = None
    _running: bool = False
    registered_commands: list[str] = field(default_factory=list)

    def command_menu_snapshot(self) -> list[dict[str, str]]:
        """Deterministic Telegram BotCommand menu (no Telegram API required)."""
        return telegram_bot_command_dicts()

    def command_reply_keyboard_snapshot(self) -> list[list[str]]:
        """Deterministic reply-keyboard rows (button labels only)."""
        return [list(row) for row in telegram_reply_keyboard_rows()]

    def _command_reply_markup(self) -> object:
        """Persistent two-column command grid (ReplyKeyboardMarkup)."""
        from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text=label) for label in row]
                for row in telegram_reply_keyboard_rows()
            ],
            resize_keyboard=True,
            is_persistent=True,
        )

    async def register_bot_commands(self, bot: object) -> tuple[str, ...]:
        """Publish the `/` command menu via setMyCommands."""
        from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonCommands

        entries = command_menu_entries()
        commands = [BotCommand(command=e.name, description=e.description) for e in entries]
        await bot.set_my_commands(commands, scope=BotCommandScopeDefault())  # type: ignore[union-attr]
        # Prefer the native commands button next to the attachment controls when available.
        with contextlib.suppress(Exception):
            await bot.set_chat_menu_button(menu_button=MenuButtonCommands())  # type: ignore[union-attr]
        names = tuple(e.name for e in entries)
        self.registered_commands = list(names)
        log.info(
            "telegram command menu registered",
            extra={
                "event": "platform.commands_registered",
                "platform": "telegram",
                "command": ",".join(names),
            },
        )
        return names

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

    async def start_polling(self) -> None:
        """Start aiogram long polling when a token and handler are configured."""
        if not self.bot_token or self.command_handler is None:
            log.warning("telegram polling not started: missing token or handler")
            return
        from aiogram import Bot, Dispatcher, F
        from aiogram.filters import Command
        from aiogram.types import Message

        bot = Bot(token=self.bot_token)
        dp = Dispatcher()
        self._bot = bot
        self._dp = dp
        handler = self.command_handler
        menu_names = command_menu_names()
        await self.register_bot_commands(bot)

        @dp.message(Command(*menu_names))
        async def on_command(message: Message) -> None:
            if message.from_user is None or message.text is None:
                return
            req = self.normalize_text_update(
                update_id=str(message.message_id),
                user_id=str(message.from_user.id),
                chat_id=str(message.chat.id),
                text=message.text,
                received_at=datetime.now(UTC),
            )
            log.info(
                "telegram command received",
                extra={
                    "event": "platform.command",
                    "platform": "telegram",
                    "command": req.command.value,
                    "request_id": req.request_id,
                },
            )
            try:
                result = await handler(req)
            except Exception:
                log.exception(
                    "telegram command handler failed",
                    extra={
                        "event": "platform.command_error",
                        "platform": "telegram",
                        "command": req.command.value,
                        "request_id": req.request_id,
                    },
                )
                raise
            log.info(
                "telegram command result",
                extra={
                    "event": "platform.command_result",
                    "platform": "telegram",
                    "command": req.command.value,
                    "kind": result.kind,
                    "request_id": req.request_id,
                },
            )
            await self.send_command_response(
                MessageContext(
                    platform=Platform.TELEGRAM,
                    chat_id=str(message.chat.id),
                    response_target=str(message.chat.id),
                    effective_upload_limit_bytes=self.upload_limit_bytes,
                ),
                result,
            )

        @dp.message(F.text)
        async def on_text(message: Message) -> None:
            # Ignore non-command noise.
            return

        self._running = True
        log.info(
            "telegram polling starting", extra={"event": "platform.start", "platform": "telegram"}
        )
        await dp.start_polling(bot)

    async def stop(self) -> None:
        self._running = False
        if self._dp is not None:
            await self._dp.stop_polling()  # type: ignore[union-attr]
        if self._bot is not None:
            await self._bot.session.close()  # type: ignore[union-attr]
        log.info("telegram stopped", extra={"event": "platform.stop", "platform": "telegram"})

    async def acknowledge_job(
        self,
        context: MessageContext,
        job_id: JobId,
        initial_state: JobState,
    ) -> MessageReference:
        self._msg_seq += 1
        text = render_job_accepted(job_id, initial_state)
        if self._bot is not None:
            from aiogram import Bot

            bot: Bot = self._bot  # type: ignore[assignment]
            msg = await bot.send_message(
                chat_id=int(context.chat_id),
                text=text,
                reply_markup=self._command_reply_markup(),  # type: ignore[arg-type]
            )
            ref = MessageReference(
                platform=Platform.TELEGRAM,
                chat_id=context.chat_id,
                message_id=str(msg.message_id),
            )
        else:
            ref = MessageReference(
                platform=Platform.TELEGRAM,
                chat_id=context.chat_id,
                message_id=str(self._msg_seq),
            )
        log.info(
            "telegram job acknowledged",
            extra={
                "event": "platform.ack",
                "platform": "telegram",
                "job_id": job_id.value,
                "state": initial_state.value,
            },
        )
        self.calls.append(("acknowledge_job", (job_id, initial_state, ref)))
        return ref

    async def edit_progress(self, message_reference: MessageReference, view: ProgressView) -> None:
        text = render_progress(view)
        if self._bot is not None:
            from aiogram import Bot

            bot: Bot = self._bot  # type: ignore[assignment]
            try:
                await bot.edit_message_text(
                    chat_id=int(message_reference.chat_id),
                    message_id=int(message_reference.message_id),
                    text=text,
                )
            except Exception as exc:
                log.debug(
                    "edit_progress failed: %s",
                    type(exc).__name__,
                    extra={
                        "event": "platform.edit_progress_failed",
                        "platform": "telegram",
                        "job_id": view.job_id.value,
                    },
                )
        self.calls.append(("edit_progress", (message_reference, view)))

    async def upload_artifact(
        self, context: MessageContext, descriptor: ArtifactDescriptor
    ) -> UploadOutcome:
        self.calls.append(("upload_artifact", descriptor))
        if descriptor.byte_size > self.upload_limit_bytes:
            log.info(
                "telegram upload too large",
                extra={
                    "event": "platform.upload_too_large",
                    "platform": "telegram",
                    "artifact_id": descriptor.artifact_id,
                    "byte_size": descriptor.byte_size,
                },
            )
            return UploadOutcome.TOO_LARGE
        if self._bot is None:
            return self.upload_outcome
        from aiogram import Bot
        from aiogram.types import FSInputFile

        bot: Bot = self._bot  # type: ignore[assignment]
        # Prefer delivery-resolved local_path; never treat opaque storage_key as a path.
        if not descriptor.local_path:
            return UploadOutcome.FAILED
        path = Path(descriptor.local_path)
        if not path.is_file():
            log.warning(
                "telegram upload missing file",
                extra={
                    "event": "platform.upload_missing",
                    "platform": "telegram",
                    "artifact_id": descriptor.artifact_id,
                },
            )
            return UploadOutcome.FAILED
        try:
            await bot.send_document(
                chat_id=int(context.chat_id),
                document=FSInputFile(path, filename=descriptor.display_name),
            )
            log.info(
                "telegram upload ok",
                extra={
                    "event": "platform.upload_ok",
                    "platform": "telegram",
                    "artifact_id": descriptor.artifact_id,
                    "byte_size": descriptor.byte_size,
                },
            )
            return UploadOutcome.UPLOADED
        except Exception as exc:
            name = type(exc).__name__.lower()
            log.warning(
                "telegram upload failed: %s",
                type(exc).__name__,
                extra={
                    "event": "platform.upload_failed",
                    "platform": "telegram",
                    "artifact_id": descriptor.artifact_id,
                },
            )
            if "too" in name and "large" in name:
                return UploadOutcome.TOO_LARGE
            if "retry" in name or "flood" in name:
                return UploadOutcome.RATE_LIMITED
            return UploadOutcome.FAILED

    async def send_final(self, message_reference: MessageReference, view: FinalOutcomeView) -> None:
        text = render_final(view)
        if self._bot is not None:
            from aiogram import Bot

            bot: Bot = self._bot  # type: ignore[assignment]
            try:
                await bot.edit_message_text(
                    chat_id=int(message_reference.chat_id),
                    message_id=int(message_reference.message_id),
                    text=text,
                )
            except Exception:
                await bot.send_message(
                    chat_id=int(message_reference.chat_id),
                    text=text,
                    reply_markup=self._command_reply_markup(),  # type: ignore[arg-type]
                )
        log.info(
            "telegram final outcome",
            extra={
                "event": "platform.final",
                "platform": "telegram",
                "job_id": view.job_id.value,
                "outcome": view.outcome,
                "error_code": view.error_code.value if view.error_code else None,
            },
        )
        self.calls.append(("send_final", (message_reference, view)))

    async def send_command_response(self, context: MessageContext, result: CommandResult) -> None:
        text = render_command_result(result)
        if text is not None and self._bot is not None:
            from aiogram import Bot

            bot: Bot = self._bot  # type: ignore[assignment]
            # Attach persistent command grid so chats match Twitch-style buttons.
            await bot.send_message(
                chat_id=int(context.chat_id),
                text=text,
                reply_markup=self._command_reply_markup(),  # type: ignore[arg-type]
            )
        self.calls.append(("send_command_response", (context, result)))

    def classify_error(self, exception: BaseException) -> tuple[PlatformErrorCode, bool]:
        name = type(exception).__name__.lower()
        if "retry" in name or "flood" in name:
            return PlatformErrorCode.RATE_LIMITED, True
        if "forbidden" in name:
            return PlatformErrorCode.FORBIDDEN, False
        return PlatformErrorCode.UNKNOWN, False
