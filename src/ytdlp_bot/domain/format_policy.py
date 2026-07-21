"""yt-dlp format selection policy (pure)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ytdlp_bot.domain.enums import AudioBitrate, FailureCode, MediaMode, VideoQuality
from ytdlp_bot.domain.errors import ValidationError, failure

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

_ACCEPTED_BITRATES: frozenset[AudioBitrate] = frozenset(AudioBitrate)


class ProcessingIntent(StrEnum):
    """Trusted post-download processing intent (never user-supplied)."""

    NONE = "none"
    REMUX = "remux"
    MERGE = "merge"
    COMPAT_TRANSCODE = "compat_transcode"
    MP3_ENCODE = "mp3_encode"


@dataclass(frozen=True, slots=True)
class FormatRecord:
    """Sanitized extractor format metadata (no URLs/headers/cookies)."""

    format_id: str
    height: int | None
    width: int | None
    vcodec: str | None
    acodec: str | None
    abr: float | None
    ext: str | None
    has_video: bool
    has_audio: bool


@dataclass(frozen=True, slots=True)
class FormatSelection:
    mode: MediaMode
    format_string: str
    postprocessors: tuple[dict[str, object], ...]
    merge_output_format: str | None
    processing_intent: ProcessingIntent = ProcessingIntent.NONE
    target_bitrate: AudioBitrate | None = None
    height_ceiling: int | None = None


@dataclass(frozen=True, slots=True)
class SourceShapeDecision:
    """Outcome of inspecting source streams for a requested mode."""

    ok: bool
    error_code: str | None
    warning_codes: tuple[str, ...]
    processing_intent: ProcessingIntent
    suggest_ytmp3: bool = False


def height_ceiling(quality: VideoQuality) -> int | None:
    return _HEIGHT[quality]


def sanitize_format_metadata(raw: dict[str, Any]) -> FormatRecord:
    """Convert reviewed yt-dlp fields only; drop URLs and secrets."""
    forbidden = ("url", "manifest_url", "http_headers", "cookies", "fragment_base_url")
    for key in forbidden:
        if key in raw and isinstance(raw.get(key), (str, dict, list)):
            # Do not raise — callers must never see these fields on the record.
            pass
    fmt_id = str(raw.get("format_id") or raw.get("format") or "unknown")[:64]
    height = _optional_nonneg_int(raw.get("height"))
    width = _optional_nonneg_int(raw.get("width"))
    vcodec = _optional_codec(raw.get("vcodec"))
    acodec = _optional_codec(raw.get("acodec"))
    abr = _optional_float(raw.get("abr"))
    ext = str(raw["ext"])[:16] if raw.get("ext") else None
    has_video = vcodec is not None and vcodec not in {"none", "null"}
    has_audio = acodec is not None and acodec not in {"none", "null"}
    if not has_video and not has_audio:
        # Infer from presence of height / abr when codecs missing.
        has_video = height is not None
        has_audio = abr is not None
    return FormatRecord(
        format_id=fmt_id,
        height=height,
        width=width,
        vcodec=vcodec if has_video else None,
        acodec=acodec if has_audio else None,
        abr=abr,
        ext=ext,
        has_video=has_video,
        has_audio=has_audio,
    )


def select_video_format(
    formats: list[FormatRecord],
    *,
    quality: VideoQuality,
) -> FormatRecord | None:
    """Pick best video (and prefer audio-bearing) under height ceiling."""
    ceiling = _HEIGHT[quality]
    candidates = [f for f in formats if f.has_video]
    if ceiling is not None:
        candidates = [f for f in candidates if f.height is not None and f.height <= ceiling]
    if not candidates:
        return None
    # Prefer higher height, then with audio, then higher abr.
    candidates.sort(
        key=lambda f: (
            f.height or 0,
            1 if f.has_audio else 0,
            f.abr or 0.0,
            f.format_id,
        ),
        reverse=True,
    )
    return candidates[0]


def select_audio_format(formats: list[FormatRecord]) -> FormatRecord | None:
    candidates = [f for f in formats if f.has_audio]
    if not candidates:
        return None
    candidates.sort(key=lambda f: (f.abr or 0.0, f.format_id), reverse=True)
    return candidates[0]


def decide_source_shape(
    formats: list[FormatRecord],
    *,
    mode: MediaMode,
    quality: VideoQuality | None = None,
) -> SourceShapeDecision:
    """Apply source-shape policy for video/audio requests."""
    has_video = any(f.has_video for f in formats)
    has_audio = any(f.has_audio for f in formats)
    if mode is MediaMode.AUDIO:
        if not has_audio:
            return SourceShapeDecision(
                ok=False,
                error_code="NO_AUDIO_STREAM",
                warning_codes=(),
                processing_intent=ProcessingIntent.NONE,
            )
        return SourceShapeDecision(
            ok=True,
            error_code=None,
            warning_codes=(),
            processing_intent=ProcessingIntent.MP3_ENCODE,
        )
    # Video mode
    if not has_video and has_audio:
        return SourceShapeDecision(
            ok=False,
            error_code="AUDIO_ONLY_SOURCE",
            warning_codes=(),
            processing_intent=ProcessingIntent.NONE,
            suggest_ytmp3=True,
        )
    if not has_video:
        return SourceShapeDecision(
            ok=False,
            error_code="NO_VIDEO_STREAM",
            warning_codes=(),
            processing_intent=ProcessingIntent.NONE,
        )
    q = quality or VideoQuality.BEST
    chosen = select_video_format(formats, quality=q)
    if chosen is None:
        return SourceShapeDecision(
            ok=False,
            error_code="NO_QUALIFYING_VIDEO",
            warning_codes=(),
            processing_intent=ProcessingIntent.NONE,
        )
    warnings: list[str] = []
    if not any(f.has_audio for f in formats):
        warnings.append("source_has_no_audio")
    # Prefer merge when separate streams likely; remux when already mp4-friendly.
    intent = ProcessingIntent.MERGE
    if chosen.ext == "mp4" and chosen.has_audio:
        intent = ProcessingIntent.REMUX
    elif chosen.vcodec and chosen.vcodec.startswith("av01"):
        intent = ProcessingIntent.COMPAT_TRANSCODE
    return SourceShapeDecision(
        ok=True,
        error_code=None,
        warning_codes=tuple(warnings),
        processing_intent=intent,
    )


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
            processing_intent=ProcessingIntent.MERGE,
            height_ceiling=height,
        )
    # Audio MP3
    br = bitrate or AudioBitrate.K320
    if br not in _ACCEPTED_BITRATES:
        raise ValidationError(
            failure(FailureCode.INVALID_COMMAND, diagnostic="invalid audio bitrate")
        )
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
        processing_intent=ProcessingIntent.MP3_ENCODE,
        target_bitrate=br,
    )


def height_within_ceiling(actual_height: int | None, quality: VideoQuality) -> bool:
    ceiling = _HEIGHT[quality]
    if ceiling is None or actual_height is None:
        return True
    return actual_height <= ceiling


def _optional_nonneg_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if n < 0 or n > 100_000:
        return None
    return n


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        n = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if n < 0 or n > 1e9:
        return None
    return n


def _optional_codec(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip().lower()[:64]
    return s or None
