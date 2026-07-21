"""PLY sequential orchestration and finalize guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from ytdlp_bot.adapters.media.archive import ArchiveEntry, write_playlist_zip
from ytdlp_bot.application.playlist import PlaylistEntryRecord, PlaylistOrchestrator
from ytdlp_bot.domain.enums import PlaylistEntryState
from ytdlp_bot.domain.identity import JobId


@pytest.mark.unit
def test_playlist_success_partial_all_failed_and_no_empty_zip(tmp_path: Path) -> None:
    jid = JobId("J" * 22)
    f1 = tmp_path / "1.mp4"
    f1.write_bytes(b"a")
    f2 = tmp_path / "2.mp4"
    f2.write_bytes(b"b")

    # Success
    ok = PlaylistOrchestrator(
        job_id=jid,
        entries=[
            PlaylistEntryRecord(1, "a", "A"),
            PlaylistEntryRecord(2, "b", "B"),
        ],
    )
    ok.mark_succeeded(1, path=str(f1))
    ok.mark_succeeded(2, path=str(f2))
    assert ok.outcome_kind() == "success"
    z = write_playlist_zip(
        tmp_path / "ok.zip",
        [
            ArchiveEntry(e.index, Path(e.local_path or ""), e.title, e.extension)
            for e in ok.successful_entries()
        ],
        failures=[],
        total=2,
    )
    assert z.is_file() and z.stat().st_size > 0

    # Partial
    part = PlaylistOrchestrator(
        job_id=jid,
        entries=[
            PlaylistEntryRecord(1, "a", "A"),
            PlaylistEntryRecord(2, "b", "B"),
        ],
    )
    part.mark_succeeded(1, path=str(f1))
    part.mark_failed(2, error_code="DOWNLOAD_FAILED")
    assert part.outcome_kind() == "partial"

    # All failed → no ZIP candidate
    bad = PlaylistOrchestrator(
        job_id=jid,
        entries=[
            PlaylistEntryRecord(1, "a", "A"),
            PlaylistEntryRecord(2, "b", "B"),
        ],
    )
    bad.mark_failed(1, error_code="X")
    bad.mark_failed(2, error_code="Y")
    assert bad.outcome_kind() == "all_failed"
    assert bad.successful_entries() == []

    # Cancel mid-flight
    mid = PlaylistOrchestrator(
        job_id=jid,
        entries=[
            PlaylistEntryRecord(1, "a", "A"),
            PlaylistEntryRecord(2, "b", "B"),
        ],
    )
    mid.mark_downloading(1)
    mid.request_cancel()
    assert mid.entries[0].state is PlaylistEntryState.CANCELLED
    assert mid.entries[1].state is PlaylistEntryState.CANCELLED
