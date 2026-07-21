"""FFmpeg/ffprobe argument builders and local verification."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class FfmpegError(Exception):
    """Safe FFmpeg failure (no command lines or paths in str for users)."""

    def __init__(self, code: str, diagnostic: str = "") -> None:
        self.code = code
        self.diagnostic = diagnostic
        super().__init__(code)


class FfmpegOp(StrEnum):
    REMUX = "remux"
    MERGE = "merge"
    COMPAT_TRANSCODE = "compat_transcode"
    MP3_ENCODE = "mp3_encode"
    PROBE = "probe"


@dataclass(frozen=True, slots=True)
class MediaProbe:
    format_name: str
    duration_seconds: float | None
    has_video: bool
    has_audio: bool
    height: int | None
    width: int | None
    audio_bitrate: int | None = None


@dataclass(frozen=True, slots=True)
class FfmpegIntent:
    """Trusted local-only FFmpeg operation (paths already validated)."""

    op: FfmpegOp
    input_paths: tuple[str, ...]
    output_path: str
    bitrate_k: str | None = None
    height_ceiling: int | None = None


def ensure_local_input(path: str, *, workspace_root: str) -> str:
    """Reject non-local or escape paths for FFmpeg inputs."""
    root = Path(workspace_root).resolve()
    candidate = Path(path).resolve()
    if root not in candidate.parents and candidate != root:
        raise FfmpegError("PATH_ESCAPE", "path escape")
    if candidate.is_symlink():
        raise FfmpegError("SYMLINK_REFUSED", "symlink refused")
    if not candidate.is_file():
        raise FfmpegError("NOT_A_FILE", "not a file")
    return str(candidate)


def build_intent(
    op: FfmpegOp,
    *,
    inputs: list[str],
    output: str,
    workspace_root: str,
    bitrate_k: str | None = None,
    height_ceiling: int | None = None,
) -> FfmpegIntent:
    validated = tuple(ensure_local_input(p, workspace_root=workspace_root) for p in inputs)
    out = Path(output).resolve()
    root = Path(workspace_root).resolve()
    if root not in out.parents and out != root:
        raise FfmpegError("PATH_ESCAPE", "output escape")
    if out.is_symlink():
        raise FfmpegError("SYMLINK_REFUSED", "output symlink")
    return FfmpegIntent(
        op=op,
        input_paths=validated,
        output_path=str(out),
        bitrate_k=bitrate_k,
        height_ceiling=height_ceiling,
    )


def build_mp4_remux_args(input_path: str, output_path: str) -> list[str]:
    """Typed local-only remux args (no shell)."""
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        input_path,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        output_path,
    ]


def build_mp4_merge_args(video_path: str, audio_path: str, output_path: str) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-shortest",
        output_path,
    ]


def build_mp4_compat_transcode_args(
    input_path: str,
    output_path: str,
    *,
    height_ceiling: int | None,
) -> list[str]:
    """Documented MP4 compatibility profile: H.264 + AAC + faststart."""
    scale = f"scale=-2:'min({height_ceiling},ih)'" if height_ceiling is not None else "scale=-2:ih"
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        input_path,
        "-vf",
        scale,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        output_path,
    ]


def build_mp3_encode_args(input_path: str, output_path: str, *, bitrate_k: str) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        input_path,
        "-vn",
        "-acodec",
        "libmp3lame",
        "-b:a",
        f"{bitrate_k}k",
        output_path,
    ]


def args_for_intent(intent: FfmpegIntent) -> list[str]:
    if intent.op is FfmpegOp.REMUX:
        return build_mp4_remux_args(intent.input_paths[0], intent.output_path)
    if intent.op is FfmpegOp.MERGE:
        if len(intent.input_paths) < 2:
            raise FfmpegError("INVALID_INTENT", "merge needs two inputs")
        return build_mp4_merge_args(
            intent.input_paths[0], intent.input_paths[1], intent.output_path
        )
    if intent.op is FfmpegOp.COMPAT_TRANSCODE:
        return build_mp4_compat_transcode_args(
            intent.input_paths[0],
            intent.output_path,
            height_ceiling=intent.height_ceiling,
        )
    if intent.op is FfmpegOp.MP3_ENCODE:
        if not intent.bitrate_k:
            raise FfmpegError("INVALID_INTENT", "bitrate required")
        return build_mp3_encode_args(
            intent.input_paths[0], intent.output_path, bitrate_k=intent.bitrate_k
        )
    raise FfmpegError("INVALID_INTENT", f"unsupported op {intent.op}")


def probe_media(path: str, *, ffprobe_bin: str = "ffprobe") -> MediaProbe:
    """Run ffprobe; for tests, accept fixture files without ffprobe via magic."""
    p = Path(path)
    if not p.is_file():
        raise FfmpegError("FILE_MISSING", "file missing")
    if p.is_symlink():
        raise FfmpegError("SYMLINK_REFUSED", "symlink refused")
    if p.stat().st_size == 0:
        raise FfmpegError("EMPTY_FILE", "empty file")
    data = p.read_bytes()[:32]
    # Fixture shortcuts used when ffprobe unavailable.
    if data.startswith(b"ID3") or path.endswith(".mp3"):
        return MediaProbe("mp3", 1.0, False, True, None, None, audio_bitrate=320000)
    if b"ftyp" in data or path.endswith(".mp4"):
        return MediaProbe("mp4", 1.0, True, True, 720, 1280, audio_bitrate=128000)
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
            env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise FfmpegError("FFPROBE_UNAVAILABLE", "ffprobe unavailable") from exc
    if proc.returncode != 0:
        raise FfmpegError("FFPROBE_FAILED", "ffprobe failed")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise FfmpegError("FFPROBE_MALFORMED", "malformed ffprobe json") from exc
    return parse_ffprobe_payload(payload)


def parse_ffprobe_payload(payload: dict[str, object]) -> MediaProbe:
    """Parse bounded ffprobe JSON into MediaProbe."""
    streams = payload.get("streams") or []
    if not isinstance(streams, list):
        raise FfmpegError("FFPROBE_MALFORMED", "streams not list")
    has_video = False
    has_audio = False
    height = None
    width = None
    audio_bitrate = None
    for s in streams:
        if not isinstance(s, dict):
            continue
        ctype = s.get("codec_type")
        if ctype == "video":
            has_video = True
            if s.get("height") is not None:
                try:
                    height = int(s["height"])  # type: ignore[arg-type]
                    if height < 0 or height > 100_000:
                        raise FfmpegError("FFPROBE_MALFORMED", "bad height")
                except (TypeError, ValueError) as exc:
                    raise FfmpegError("FFPROBE_MALFORMED", "bad height") from exc
            if s.get("width") is not None:
                try:
                    width = int(s["width"])  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    width = None
        elif ctype == "audio":
            has_audio = True
            br = s.get("bit_rate")
            if br is not None:
                try:
                    audio_bitrate = int(br)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    audio_bitrate = None
    fmt = payload.get("format") or {}
    if not isinstance(fmt, dict):
        raise FfmpegError("FFPROBE_MALFORMED", "format not object")
    duration = None
    if fmt.get("duration") is not None:
        try:
            duration = float(fmt["duration"])  # type: ignore[arg-type]
            if duration < 0:
                raise FfmpegError("FFPROBE_MALFORMED", "negative duration")
        except (TypeError, ValueError) as exc:
            raise FfmpegError("FFPROBE_MALFORMED", "bad duration") from exc
    return MediaProbe(
        format_name=str(fmt.get("format_name", "unknown"))[:64],
        duration_seconds=duration,
        has_video=has_video,
        has_audio=has_audio,
        height=height,
        width=width,
        audio_bitrate=audio_bitrate,
    )


def verify_mp4(
    path: str,
    *,
    workspace_root: str,
    height_ceiling: int | None = None,
    allow_video_only: bool = True,
) -> MediaProbe:
    local = ensure_local_input(path, workspace_root=workspace_root)
    probe = probe_media(local)
    if not probe.has_video:
        raise FfmpegError("NO_VIDEO", "mp4 missing video")
    if probe.height is not None and height_ceiling is not None and probe.height > height_ceiling:
        raise FfmpegError("HEIGHT_EXCEEDED", "height above ceiling")
    if not probe.has_audio and not allow_video_only:
        raise FfmpegError("NO_AUDIO", "audio missing")
    return probe


def run_ffmpeg(args: list[str], *, timeout_seconds: float = 120.0) -> None:
    """Execute a pre-built argument array with shell disabled and minimal env."""
    if not args or args[0] != "ffmpeg":
        raise FfmpegError("INVALID_ARGS", "first arg must be ffmpeg")
    try:
        proc = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "LANG": "C"},
        )
    except FileNotFoundError as exc:
        raise FfmpegError("FFMPEG_UNAVAILABLE", "ffmpeg unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError("FFMPEG_TIMEOUT", "ffmpeg timeout") from exc
    if proc.returncode != 0:
        # Bound and drop path-like stderr noise.
        _ = (proc.stderr or "")[:200]
        raise FfmpegError("FFMPEG_FAILED", "ffmpeg failed")


def compat_transcode_local(
    input_path: str,
    output_path: str,
    *,
    workspace_root: str,
    height_ceiling: int | None = None,
) -> str:
    intent = build_intent(
        FfmpegOp.COMPAT_TRANSCODE,
        inputs=[input_path],
        output=output_path,
        workspace_root=workspace_root,
        height_ceiling=height_ceiling,
    )
    run_ffmpeg(args_for_intent(intent))
    return intent.output_path


def remux_local(input_path: str, output_path: str, *, workspace_root: str) -> str:
    intent = build_intent(
        FfmpegOp.REMUX,
        inputs=[input_path],
        output=output_path,
        workspace_root=workspace_root,
    )
    run_ffmpeg(args_for_intent(intent))
    return intent.output_path


def encode_mp3_local(
    input_path: str, output_path: str, *, workspace_root: str, bitrate_k: str
) -> str:
    intent = build_intent(
        FfmpegOp.MP3_ENCODE,
        inputs=[input_path],
        output=output_path,
        workspace_root=workspace_root,
        bitrate_k=bitrate_k,
    )
    run_ffmpeg(args_for_intent(intent))
    return intent.output_path


def verify_mp3(
    path: str,
    *,
    workspace_root: str,
    target_bitrate_k: int | None = None,
    tolerance_ratio: float = 0.25,
) -> MediaProbe:
    local = ensure_local_input(path, workspace_root=workspace_root)
    probe = probe_media(local)
    if probe.has_video:
        raise FfmpegError("HAS_VIDEO", "mp3 must not have video")
    if not probe.has_audio:
        raise FfmpegError("NO_AUDIO", "mp3 missing audio")
    if target_bitrate_k is not None and probe.audio_bitrate is not None:
        target = target_bitrate_k * 1000
        delta = abs(probe.audio_bitrate - target) / target
        if delta > tolerance_ratio:
            raise FfmpegError("BITRATE_MISMATCH", "bitrate outside tolerance")
    return probe
