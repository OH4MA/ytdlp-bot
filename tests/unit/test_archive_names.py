"""PLY filename sanitization for archive members."""

from __future__ import annotations

from pathlib import Path

import pytest

from ytdlp_bot.adapters.media.archive import (
    ArchiveEntry,
    build_archive_member_name,
    build_artifact_display_name,
    sanitize_filename_title,
    trusted_extension,
    write_playlist_zip,
)


@pytest.mark.unit
def test_sanitize_and_unique_archive_names(tmp_path: Path) -> None:
    assert sanitize_filename_title(
        "../etc/passwd"
    ) == ".._etc_passwd" or "_" in sanitize_filename_title("a/b\\c")
    assert "/" not in sanitize_filename_title("a/b")
    assert trusted_extension("MP4") == "mp4"
    assert trusted_extension("exe") == "bin"
    name = build_archive_member_name(1, total=12, title="你好 世界!", extension="mp4")
    assert name.endswith(".mp4")
    assert "你好" in name
    assert "/" not in name
    display = build_artifact_display_name("原影片名稱 / 測試:標題", "mp4")
    assert display.endswith(".mp4")
    assert "原影片名稱" in display
    assert "/" not in display
    assert ":" not in display
    assert len(display) <= 200
    long_title = "あ" * 300
    long_name = build_artifact_display_name(long_title, "mp3")
    assert long_name.endswith(".mp3")
    assert len(long_name) <= 200
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    out = tmp_path / "pl.zip"
    write_playlist_zip(
        out,
        [
            ArchiveEntry(1, f, "same", "mp4"),
            ArchiveEntry(2, f, "same", "mp4"),
        ],
        failures=[(3, "id", "DOWNLOAD_FAILED")],
        total=3,
    )
    import zipfile

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "FAILURES.txt" in names
        assert len([n for n in names if n.endswith(".mp4")]) == 2
