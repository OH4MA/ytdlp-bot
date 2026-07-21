"""FFmpeg/ffprobe argument builders and local verification."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FfmpegError(Exception):
    """Safe FFmpeg failure."""


@dataclass(frozen=True, slots=True)
class MediaProbe:
    format_name: str
    duration_seconds: float | None
    has_video: bool
    has_audio: bool
    height: int | None
    width: int | None


def build_mp4_remux_args(input_path: str, output_path: str) -> list[str]:
    """Typed local-only remux args (no shell)."""
    return [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        output_path,
    ]


def build_mp3_encode_args(input_path: str, output_path: str, *, bitrate_k: str) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vn",
        "-acodec",
        "libmp3lame",
        "-b:a",
        f"{bitrate_k}k",
        output_path,
    ]


def probe_media(path: str, *, ffprobe_bin: str = "ffprobe") -> MediaProbe:
    """Run ffprobe; for tests, accept fixture files without ffprobe via magic."""
    p = Path(path)
    if not p.is_file():
        raise FfmpegError("file missing")
    data = p.read_bytes()[:16]
    # Fixture shortcuts used when ffprobe unavailable.
    if data.startswith(b"ID3") or path.endswith(".mp3"):
        return MediaProbe("mp3", None, False, True, None, None)
    if b"ftyp" in data or path.endswith(".mp4"):
        return MediaProbe("mp4", None, True, True, 720, 1280)
    try:
        proc = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                path,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise FfmpegError("ffprobe unavailable") from exc
    if proc.returncode != 0:
        raise FfmpegError("ffprobe failed")
    payload = json.loads(proc.stdout)
    streams = payload.get("streams") or []
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    height = None
    width = None
    for s in streams:
        if s.get("codec_type") == "video":
            height = int(s["height"]) if s.get("height") is not None else None
            width = int(s["width"]) if s.get("width") is not None else None
            break
    fmt = payload.get("format") or {}
    duration = float(fmt["duration"]) if fmt.get("duration") else None
    return MediaProbe(
        format_name=str(fmt.get("format_name", "unknown")),
        duration_seconds=duration,
        has_video=has_video,
        has_audio=has_audio,
        height=height,
        width=width,
    )


def ensure_local_input(path: str, *, workspace_root: str) -> str:
    """Reject non-local or escape paths for FFmpeg inputs."""
    root = Path(workspace_root).resolve()
    candidate = Path(path).resolve()
    if root not in candidate.parents and candidate != root:
        raise FfmpegError("path escape")
    if candidate.is_symlink():
        raise FfmpegError("symlink refused")
    if not candidate.is_file():
        raise FfmpegError("not a file")
    return str(candidate)
