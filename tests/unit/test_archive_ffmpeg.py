"""Playlist archive and ffmpeg helper tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ytdlp_bot.adapters.media.archive import ArchiveEntry, write_playlist_zip
from ytdlp_bot.adapters.media.ffmpeg_engine import (
    build_mp3_encode_args,
    build_mp4_remux_args,
    ensure_local_input,
    probe_media,
)


@pytest.mark.unit
def test_archive_partial_manifest(tmp_path: Path) -> None:
    f1 = tmp_path / "a.mp4"
    f1.write_bytes(b"x" * 10)
    out = tmp_path / "pl.zip"
    write_playlist_zip(
        out,
        [ArchiveEntry(1, f1, "Title One", "mp4")],
        failures=[(2, "id2", "DOWNLOAD_FAILED")],
        total=2,
    )
    assert out.is_file()
    import zipfile

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert any(n.endswith(".mp4") for n in names)
        assert "FAILURES.txt" in names


@pytest.mark.unit
def test_ffmpeg_args_and_probe(tmp_path: Path) -> None:
    args = build_mp4_remux_args("/ws/in", "/ws/out.mp4")
    assert args[0] == "ffmpeg"
    assert "-i" in args
    a2 = build_mp3_encode_args("/ws/in", "/ws/out.mp3", bitrate_k="320")
    assert "libmp3lame" in a2
    f = tmp_path / "video.mp4"
    f.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8)
    probe = probe_media(str(f))
    assert probe.has_video
    ensure_local_input(str(f), workspace_root=str(tmp_path))
    from ytdlp_bot.adapters.media.ffmpeg_engine import FfmpegError

    with pytest.raises(FfmpegError):
        ensure_local_input("/etc/passwd", workspace_root=str(tmp_path))
