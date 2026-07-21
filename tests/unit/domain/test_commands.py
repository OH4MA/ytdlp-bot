"""FND-04: command DTOs and normalization grammar."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ytdlp_bot.domain.commands import (
    CommandRequest,
    YtdlArgs,
    Ytmp3Args,
    build_command_arguments,
    parse_text_command,
)
from ytdlp_bot.domain.enums import AudioBitrate, CommandName, Platform, VideoQuality
from ytdlp_bot.domain.errors import ValidationError
from ytdlp_bot.domain.identity import Identity, MessageContext


def _ctx() -> MessageContext:
    return MessageContext(
        platform=Platform.TELEGRAM,
        chat_id="1",
        response_target="1",
        effective_upload_limit_bytes=50_000_000,
    )


@pytest.mark.unit
def test_parse_ytdl_defaults_and_quality() -> None:
    cmd, args = parse_text_command("/ytdl https://example.com/v")
    assert cmd is CommandName.YTDL
    assert isinstance(args, YtdlArgs)
    assert args.quality is VideoQuality.BEST

    cmd2, args2 = parse_text_command("/ytdl@MyBot https://example.com/v 720p")
    assert cmd2 is CommandName.YTDL
    assert isinstance(args2, YtdlArgs)
    assert args2.quality is VideoQuality.P720


@pytest.mark.unit
def test_parse_ytmp3_default_bitrate() -> None:
    cmd, args = parse_text_command("/ytmp3 https://example.com/a")
    assert cmd is CommandName.YTMP3
    assert isinstance(args, Ytmp3Args)
    assert args.bitrate is AudioBitrate.K320
    _, args2 = parse_text_command("/ytmp3 https://example.com/a 192k")
    assert isinstance(args2, Ytmp3Args)
    assert args2.bitrate is AudioBitrate.K192


@pytest.mark.unit
def test_telegram_and_discord_equivalent_ytdl() -> None:
    _, tg = parse_text_command("/ytdl https://ex.test/x 1080p")
    dc = build_command_arguments(CommandName.YTDL, url="https://ex.test/x", quality="1080p")
    assert isinstance(tg, YtdlArgs) and isinstance(dc, YtdlArgs)
    assert tg.url == dc.url
    assert tg.quality == dc.quality


@pytest.mark.unit
def test_reject_extra_args_and_flags() -> None:
    with pytest.raises(ValidationError):
        parse_text_command("/ytdl https://x.test --cookies file")
    with pytest.raises(ValidationError):
        parse_text_command("/ytdl_help please")
    with pytest.raises(ValidationError):
        parse_text_command("/ytmp3")


@pytest.mark.unit
def test_status_renew_requires_job_id() -> None:
    with pytest.raises(ValidationError):
        parse_text_command("/ytdl_status renew")
    jid = "B" * 22
    cmd, args = parse_text_command(f"/ytdl_status {jid} renew")
    assert cmd is CommandName.YTDL_STATUS
    assert args.renew is True  # type: ignore[union-attr]
    assert args.job_id is not None  # type: ignore[union-attr]


@pytest.mark.unit
def test_command_request_validates_match() -> None:
    req = CommandRequest(
        request_id="upd-1",
        identity=Identity(platform=Platform.TELEGRAM, user_id="1"),
        context=_ctx(),
        command=CommandName.YTDL_HELP,
        arguments=parse_text_command("/ytdl_help")[1],
        received_at=datetime.now(UTC),
    )
    assert req.command is CommandName.YTDL_HELP
    with pytest.raises(ValidationError):
        CommandRequest(
            request_id="upd-2",
            identity=Identity(platform=Platform.TELEGRAM, user_id="1"),
            context=_ctx(),
            command=CommandName.YTDL,
            arguments=parse_text_command("/ytdl_help")[1],
            received_at=datetime.now(UTC),
        )


@pytest.mark.unit
def test_url_length_bound() -> None:
    with pytest.raises(ValidationError):
        parse_text_command("/ytdl " + "https://x.test/" + ("a" * 5000))


@pytest.mark.unit
def test_admin_actions_parse() -> None:
    cmd, _args = parse_text_command("/ytdl_admin status")
    assert cmd is CommandName.YTDL_ADMIN
    cmd2, args2 = parse_text_command("/ytdl_admin retention set 12h")
    assert cmd2 is CommandName.YTDL_ADMIN
    assert args2.action.duration_seconds == 12 * 3600  # type: ignore[union-attr]
