"""Shared platform adapter contract suite for Telegram and Discord."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ytdlp_bot.adapters.platform.discord import DiscordPlatformAdapter
from ytdlp_bot.adapters.platform.telegram import TelegramPlatformAdapter
from ytdlp_bot.domain.commands import HelpView
from ytdlp_bot.domain.enums import JobState, Platform
from ytdlp_bot.domain.identity import JobId, MessageContext
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
