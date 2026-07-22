"""Shared platform adapter contract suite for Telegram and Discord.

Presentation policy (TG and DC stay aligned unless the platform has no
equivalent surface — e.g. Telegram ReplyKeyboard vs Discord slash menu):
- acknowledge_job: new message
- edit_progress: may update the ack/progress bubble in place
- send_final: always a new message (never edit the progress bubble)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ytdlp_bot.adapters.platform.discord import DiscordPlatformAdapter
from ytdlp_bot.adapters.platform.telegram import TelegramPlatformAdapter
from ytdlp_bot.domain.commands import HelpView
from ytdlp_bot.domain.enums import JobState, Platform
from ytdlp_bot.domain.identity import JobId, MessageContext, MessageReference
from ytdlp_bot.domain.progress import (
    ArtifactDescriptor,
    FinalOutcomeView,
    ProgressView,
)


@pytest.mark.contract
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter_cls,platform",
    [
        (TelegramPlatformAdapter, Platform.TELEGRAM),
        (DiscordPlatformAdapter, Platform.DISCORD),
    ],
)
async def test_platform_contract_suite(adapter_cls, platform: Platform) -> None:
    adapter = adapter_cls()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    if platform is Platform.TELEGRAM:
        req = adapter.normalize_text_update(
            update_id="1",
            user_id="1",
            chat_id="9",
            text="/ytdl_help",
            received_at=now,
        )
    else:
        req = adapter.normalize_text_like(
            interaction_id="1",
            user_id="1",
            channel_id="9",
            text="/ytdl_help",
            received_at=now,
        )
    assert req.identity.platform is platform
    assert req.command.value == "ytdl_help"

    ctx = MessageContext(
        platform=platform,
        chat_id="9",
        response_target="9",
        effective_upload_limit_bytes=adapter.upload_limit_bytes,
    )
    jid = JobId("J" * 22)
    ref = await adapter.acknowledge_job(ctx, jid, JobState.QUEUED)
    await adapter.edit_progress(
        ref,
        ProgressView(
            job_id=jid,
            state="downloading",
            phase="downloading",
            percent=10,
            playlist_completed=None,
            playlist_total=None,
            current_entry_title=None,
            warning_codes=(),
        ),
    )
    outcome = await adapter.upload_artifact(
        ctx,
        ArtifactDescriptor(
            artifact_id="A" * 22,
            display_name="a.mp4",
            media_type="video/mp4",
            byte_size=100,
            storage_key="S" * 22,
        ),
    )
    assert outcome.value == "uploaded"
    await adapter.send_final(
        ref,
        FinalOutcomeView(
            job_id=jid,
            outcome="completed",
            message_key="outcome.completed",
        ),
    )
    await adapter.send_command_response(ctx, HelpView())
    assert adapter.classify_error(RuntimeError("x"))[0].value


@pytest.mark.contract
@pytest.mark.asyncio
async def test_telegram_send_final_posts_new_message_not_edit() -> None:
    adapter = TelegramPlatformAdapter()
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot.edit_message_text = AsyncMock()
    adapter._bot = bot

    ref = MessageReference(platform=Platform.TELEGRAM, chat_id="42", message_id="7")
    await adapter.send_final(
        ref,
        FinalOutcomeView(
            job_id=JobId("J" * 22),
            outcome="completed",
            message_key="outcome.completed",
            download_url="https://example.invalid/a",
        ),
    )
    bot.edit_message_text.assert_not_awaited()
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert "https://example.invalid/a" in kwargs["text"]


@pytest.mark.contract
@pytest.mark.asyncio
async def test_discord_send_final_posts_new_message_not_edit() -> None:
    adapter = DiscordPlatformAdapter()
    msg = MagicMock()
    msg.edit = AsyncMock()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=msg)
    channel.send = AsyncMock(return_value=MagicMock(id=99))
    client = MagicMock()
    client.get_channel = MagicMock(return_value=channel)
    adapter._client = client

    ref = MessageReference(platform=Platform.DISCORD, chat_id="42", message_id="7")
    await adapter.send_final(
        ref,
        FinalOutcomeView(
            job_id=JobId("J" * 22),
            outcome="completed",
            message_key="outcome.completed",
            download_url="https://example.invalid/a",
        ),
    )
    channel.fetch_message.assert_not_awaited()
    msg.edit.assert_not_awaited()
    channel.send.assert_awaited_once()
    args, kwargs = channel.send.await_args
    content = args[0] if args else kwargs.get("content")
    assert content is not None
    assert "https://example.invalid/a" in content
