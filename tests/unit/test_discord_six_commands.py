"""DSC-01: Discord registers exactly six canonical slash commands."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from ytdlp_bot.adapters.platform.discord import (
    DiscordPlatformAdapter,
    canonical_slash_command_specs,
    registered_command_names,
)
from ytdlp_bot.domain.commands import CommandName, HelpArgs, YtdlArgs, Ytmp3Args
from ytdlp_bot.domain.enums import AudioBitrate, VideoQuality


@pytest.mark.unit
def test_canonical_six_command_schema_snapshot() -> None:
    names = registered_command_names()
    assert names == (
        "ytdl",
        "ytmp3",
        "ytdl_status",
        "ytdl_cancel",
        "ytdl_help",
        "ytdl_admin",
    )
    specs = canonical_slash_command_specs()
    assert len(specs) == 6
    by_name = {s.name: s for s in specs}
    assert any(o.name == "quality" for o in by_name["ytdl"].options)
    assert any(o.required for o in by_name["ytdl"].options if o.name == "url")
    quality_opt = next(o for o in by_name["ytdl"].options if o.name == "quality")
    assert VideoQuality.BEST.value in quality_opt.choices
    assert VideoQuality.P720.value in quality_opt.choices
    bitrate_opt = next(o for o in by_name["ytmp3"].options if o.name == "bitrate")
    assert AudioBitrate.K320.value in bitrate_opt.choices
    assert any(o.required for o in by_name["ytdl_cancel"].options if o.name == "job_id")
    admin = by_name["ytdl_admin"]
    action = next(o for o in admin.options if o.name == "action")
    assert "status" in action.choices and "capacity_set" in action.choices


@pytest.mark.unit
def test_adapter_schema_snapshot_matches_canonical() -> None:
    adapter = DiscordPlatformAdapter()
    snap = adapter.command_schema_snapshot()
    assert [row["name"] for row in snap] == list(registered_command_names())
    assert all(row["description"] for row in snap)


@pytest.mark.unit
def test_register_commands_on_tree_registers_six() -> None:
    adapter = DiscordPlatformAdapter(command_handler=MagicMock())
    # Minimal stand-in for CommandTree.command decorator factory.
    registered: list[str] = []

    class FakeTree:
        def command(self, *, name: str, description: str):
            def deco(fn):
                registered.append(name)
                return fn

            return deco

    # discord.app_commands is imported inside register_commands_on_tree;
    # patch only the decorator helpers used at decoration time by injecting a stub module path.
    import sys
    from types import ModuleType

    fake_discord = ModuleType("discord")
    fake_app = ModuleType("discord.app_commands")

    def describe(**kwargs):
        def deco(fn):
            return fn

        return deco

    def choices(**kwargs):
        def deco(fn):
            return fn

        return deco

    class Choice:
        def __init__(self, name: str, value: str) -> None:
            self.name = name
            self.value = value

    fake_app.describe = describe  # type: ignore[attr-defined]
    fake_app.choices = choices  # type: ignore[attr-defined]
    fake_app.Choice = Choice  # type: ignore[attr-defined]
    fake_discord.app_commands = fake_app  # type: ignore[attr-defined]
    sys.modules["discord"] = fake_discord
    sys.modules["discord.app_commands"] = fake_app
    try:
        names = adapter.register_commands_on_tree(FakeTree())
    finally:
        # Leave modules if real discord is installed; do not delete if already present.
        pass
    assert tuple(names) == registered_command_names()
    assert set(registered) == set(registered_command_names())
    assert adapter.registered_commands == list(registered_command_names())


@pytest.mark.unit
def test_normalize_slash_all_six_commands() -> None:
    adapter = DiscordPlatformAdapter(upload_limit_bytes=5_000_000)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    ytdl = adapter.normalize_slash(
        interaction_id="1",
        user_id="9",
        channel_id="3",
        command=CommandName.YTDL,
        url="https://example.com/v",
        quality="720p",
        received_at=now,
        upload_limit_bytes=20_000_000,
    )
    assert ytdl.command is CommandName.YTDL
    assert isinstance(ytdl.arguments, YtdlArgs)
    assert ytdl.arguments.quality is VideoQuality.P720
    assert ytdl.context.effective_upload_limit_bytes == 20_000_000

    ytmp3 = adapter.normalize_slash(
        interaction_id="2",
        user_id="9",
        channel_id="3",
        command=CommandName.YTMP3,
        url="https://example.com/a",
        bitrate="192k",
        received_at=now,
    )
    assert isinstance(ytmp3.arguments, Ytmp3Args)
    assert ytmp3.arguments.bitrate is AudioBitrate.K192

    status = adapter.normalize_slash(
        interaction_id="3",
        user_id="9",
        channel_id="3",
        command=CommandName.YTDL_STATUS,
        job_id="J" * 22,
        renew=True,
        received_at=now,
    )
    assert status.command is CommandName.YTDL_STATUS

    cancel = adapter.normalize_slash(
        interaction_id="4",
        user_id="9",
        channel_id="3",
        command=CommandName.YTDL_CANCEL,
        job_id="J" * 22,
        received_at=now,
    )
    assert cancel.command is CommandName.YTDL_CANCEL

    help_req = adapter.normalize_slash(
        interaction_id="5",
        user_id="9",
        channel_id="3",
        command=CommandName.YTDL_HELP,
        received_at=now,
    )
    assert isinstance(help_req.arguments, HelpArgs)

    admin = adapter.normalize_slash(
        interaction_id="6",
        user_id="9",
        channel_id="3",
        command=CommandName.YTDL_ADMIN,
        admin_action_name="status",
        received_at=now,
    )
    assert admin.command is CommandName.YTDL_ADMIN


@pytest.mark.unit
@pytest.mark.asyncio
async def test_start_gateway_without_token_is_noop() -> None:
    adapter = DiscordPlatformAdapter(bot_token="", command_handler=MagicMock())
    await adapter.start_gateway()
    assert adapter._client is None
