"""Canonical user-facing command menu for Telegram and Discord.

Telegram surfaces this via Bot API setMyCommands (the `/` autocomplete list)
and a persistent ReplyKeyboardMarkup grid of `/command` buttons.
Discord surfaces the same names/descriptions as application slash commands.
"""

from __future__ import annotations

from dataclasses import dataclass

from ytdlp_bot.domain.commands import CommandName


@dataclass(frozen=True, slots=True)
class CommandMenuEntry:
    """One selectable command with a short zh-TW description."""

    name: str
    description: str

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 32:
            raise ValueError("command name must be 1..32 characters")
        # Telegram BotCommand description limit is 256.
        if not self.description or len(self.description) > 256:
            raise ValueError("command description must be 1..256 characters")


def command_menu_entries() -> tuple[CommandMenuEntry, ...]:
    """Six canonical commands in stable order (menu and slash schema)."""
    return (
        CommandMenuEntry(CommandName.YTDL.value, "下載影片為 MP4"),
        CommandMenuEntry(CommandName.YTMP3.value, "下載音訊為 MP3"),
        CommandMenuEntry(CommandName.YTDL_STATUS.value, "查詢工作狀態"),
        CommandMenuEntry(CommandName.YTDL_CANCEL.value, "取消工作"),
        CommandMenuEntry(CommandName.YTDL_HELP.value, "顯示使用說明"),
        CommandMenuEntry(CommandName.YTDL_ADMIN.value, "管理員操作"),
    )


def command_menu_names() -> tuple[str, ...]:
    return tuple(entry.name for entry in command_menu_entries())


def command_description_map() -> dict[CommandName, str]:
    """Map CommandName -> zh-TW description for Discord slash registration."""
    return {CommandName(entry.name): entry.description for entry in command_menu_entries()}


def telegram_bot_command_dicts() -> list[dict[str, str]]:
    """Plain dict form for tests and set_my_commands payload inspection."""
    return [{"command": e.name, "description": e.description} for e in command_menu_entries()]


def telegram_reply_keyboard_rows() -> tuple[tuple[str, ...], ...]:
    """Two-column grid of slash command button labels for ReplyKeyboardMarkup.

    Matches the Twitch Watchdog pattern: persistent buttons that send `/name`
    as plain text when tapped (handled by the existing command parser).
    """
    names = [f"/{name}" for name in command_menu_names()]
    rows: list[tuple[str, ...]] = []
    for i in range(0, len(names), 2):
        chunk = names[i : i + 2]
        rows.append(tuple(chunk))
    return tuple(rows)
