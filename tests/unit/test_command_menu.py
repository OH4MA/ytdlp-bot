"""Shared Telegram/Discord command menu catalog."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ytdlp_bot.adapters.platform.command_menu import (
    command_description_map,
    command_menu_entries,
    command_menu_names,
    telegram_bot_command_dicts,
    telegram_reply_keyboard_rows,
)
from ytdlp_bot.adapters.platform.discord import registered_command_names
from ytdlp_bot.adapters.platform.telegram import TelegramPlatformAdapter
from ytdlp_bot.domain.commands import CommandName


@pytest.mark.unit
def test_canonical_menu_has_six_commands() -> None:
    names = command_menu_names()
    assert names == (
        "ytdl",
        "ytmp3",
        "ytdl_status",
        "ytdl_cancel",
        "ytdl_help",
        "ytdl_admin",
    )
    entries = command_menu_entries()
    assert len(entries) == 6
    for entry in entries:
        assert 1 <= len(entry.description) <= 256
        assert entry.name.islower() or "_" in entry.name


@pytest.mark.unit
def test_menu_aligned_with_discord_registration() -> None:
    assert command_menu_names() == registered_command_names()
    desc = command_description_map()
    assert desc[CommandName.YTDL] == "下載影片為 MP4"
    assert desc[CommandName.YTDL_HELP]


@pytest.mark.unit
def test_telegram_menu_snapshot() -> None:
    adapter = TelegramPlatformAdapter()
    snap = adapter.command_menu_snapshot()
    assert snap == telegram_bot_command_dicts()
    assert [row["command"] for row in snap] == list(command_menu_names())
    assert all(row["description"] for row in snap)


@pytest.mark.unit
def test_telegram_reply_keyboard_two_column_grid() -> None:
    rows = telegram_reply_keyboard_rows()
    assert rows == (
        ("/ytdl", "/ytmp3"),
        ("/ytdl_status", "/ytdl_cancel"),
        ("/ytdl_help", "/ytdl_admin"),
    )
    adapter = TelegramPlatformAdapter()
    assert adapter.command_reply_keyboard_snapshot() == [list(r) for r in rows]
    flat = [label for row in rows for label in row]
    assert flat == [f"/{n}" for n in command_menu_names()]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_telegram_register_bot_commands_calls_api() -> None:
    adapter = TelegramPlatformAdapter()
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()
    bot.set_chat_menu_button = AsyncMock()

    # Provide lightweight BotCommand stand-ins via the real import path when available.
    names = await adapter.register_bot_commands(bot)
    assert names == command_menu_names()
    assert adapter.registered_commands == list(command_menu_names())
    bot.set_my_commands.assert_awaited_once()
    args, _kwargs = bot.set_my_commands.await_args
    commands = args[0]
    assert len(commands) == 6
    assert {c.command for c in commands} == set(command_menu_names())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_command_response_attaches_reply_keyboard() -> None:
    from ytdlp_bot.domain.commands import HelpView
    from ytdlp_bot.domain.identity import MessageContext
    from ytdlp_bot.domain.enums import Platform

    adapter = TelegramPlatformAdapter()
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    adapter._bot = bot

    ctx = MessageContext(
        platform=Platform.TELEGRAM,
        chat_id="1",
        response_target="1",
        effective_upload_limit_bytes=50_000_000,
    )
    await adapter.send_command_response(ctx, HelpView())
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    markup = kwargs.get("reply_markup")
    assert markup is not None
    assert markup.resize_keyboard is True
    assert markup.is_persistent is True
    labels = [btn.text for row in markup.keyboard for btn in row]
    assert labels == [f"/{n}" for n in command_menu_names()]
