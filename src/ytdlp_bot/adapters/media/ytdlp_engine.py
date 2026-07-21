"""yt-dlp options builder (no network in unit tests)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ytdlp_bot.domain.enums import MediaMode
from ytdlp_bot.domain.format_policy import FormatSelection, build_format_selection


@dataclass(frozen=True, slots=True)
class YtdlpOptions:
    """Trusted options dict for yt-dlp Python API."""

    raw: dict[str, Any]


def build_ytdlp_options(
    selection: FormatSelection,
    *,
    workspace: str,
    proxy_url: str | None,
    network_attempts: int,
    outtmpl: str,
) -> YtdlpOptions:
    opts: dict[str, Any] = {
        "format": selection.format_string,
        "outtmpl": outtmpl,
        "noplaylist": selection.mode is MediaMode.VIDEO,
        "quiet": True,
        "no_warnings": True,
        "retries": network_attempts,
        "fragment_retries": network_attempts,
        "concurrent_fragment_downloads": 1,
        "paths": {"home": workspace},
    }
    if selection.merge_output_format:
        opts["merge_output_format"] = selection.merge_output_format
    if selection.postprocessors:
        opts["postprocessors"] = list(selection.postprocessors)
    if proxy_url:
        opts["proxy"] = proxy_url
    # Never pass arbitrary user flags.
    return YtdlpOptions(raw=opts)


def options_for_request(
    *,
    mode: MediaMode,
    quality: object | None,
    bitrate: object | None,
    workspace: str,
    proxy_url: str | None,
    network_attempts: int,
) -> YtdlpOptions:
    from ytdlp_bot.domain.enums import AudioBitrate, VideoQuality

    sel = build_format_selection(
        mode,
        quality=quality if isinstance(quality, VideoQuality) else None,
        bitrate=bitrate if isinstance(bitrate, AudioBitrate) else None,
    )
    tmpl = "%(id)s.%(ext)s"
    return build_ytdlp_options(
        sel,
        workspace=workspace,
        proxy_url=proxy_url,
        network_attempts=network_attempts,
        outtmpl=tmpl,
    )
