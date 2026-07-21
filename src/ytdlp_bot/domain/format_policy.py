"""yt-dlp format selection policy (pure)."""

from __future__ import annotations

from dataclasses import dataclass

from ytdlp_bot.domain.enums import AudioBitrate, MediaMode, VideoQuality

# Height ceilings for non-best qualities.
_HEIGHT: dict[VideoQuality, int | None] = {
    VideoQuality.BEST: None,
    VideoQuality.P2160: 2160,
    VideoQuality.P1440: 1440,
    VideoQuality.P1080: 1080,
    VideoQuality.P720: 720,
    VideoQuality.P480: 480,
    VideoQuality.P360: 360,
}


@dataclass(frozen=True, slots=True)
class FormatSelection:
    mode: MediaMode
    format_string: str
    postprocessors: tuple[dict[str, object], ...]
    merge_output_format: str | None


def build_format_selection(
    mode: MediaMode,
    *,
    quality: VideoQuality | None = None,
    bitrate: AudioBitrate | None = None,
) -> FormatSelection:
    if mode is MediaMode.VIDEO:
        q = quality or VideoQuality.BEST
        height = _HEIGHT[q]
        if height is None:
            # Best MP4-compatible progressive/adaptive merge.
            fmt = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"
        else:
            # Never silently exceed ceiling.
            fmt = (
                f"bv*[height<=?{height}][ext=mp4]+ba[ext=m4a]/"
                f"b[height<=?{height}][ext=mp4]/"
                f"bv*[height<=?{height}]+ba/b[height<=?{height}]"
            )
        return FormatSelection(
            mode=mode,
            format_string=fmt,
            postprocessors=(),
            merge_output_format="mp4",
        )
    # Audio MP3
    br = bitrate or AudioBitrate.K320
    kbps = br.value.replace("k", "")
    return FormatSelection(
        mode=mode,
        format_string="bestaudio/best",
        postprocessors=(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": kbps,
            },
        ),
        merge_output_format=None,
    )


def height_within_ceiling(actual_height: int | None, quality: VideoQuality) -> bool:
    ceiling = _HEIGHT[quality]
    if ceiling is None or actual_height is None:
        return True
    return actual_height <= ceiling
