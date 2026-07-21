"""Shared normalization helpers for platform adapters."""

from __future__ import annotations

from datetime import datetime

from ytdlp_bot.domain.commands import CommandRequest, parse_text_command
from ytdlp_bot.domain.enums import Platform
from ytdlp_bot.domain.identity import Identity, MessageContext


def build_text_command_request(
    *,
    platform: Platform,
    request_id: str,
    user_id: str,
    chat_id: str,
    text: str,
    upload_limit_bytes: int,
    received_at: datetime,
) -> CommandRequest:
    command, arguments = parse_text_command(text)
    return CommandRequest(
        request_id=request_id,
        identity=Identity(platform=platform, user_id=user_id),
        context=MessageContext(
            platform=platform,
            chat_id=chat_id,
            response_target=chat_id,
            effective_upload_limit_bytes=upload_limit_bytes,
        ),
        command=command,
        arguments=arguments,
        received_at=received_at,
    )
