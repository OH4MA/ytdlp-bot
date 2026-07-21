"""Sequential playlist entry processing and partial-failure bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass, field

from ytdlp_bot.domain.enums import PlaylistEntryState
from ytdlp_bot.domain.identity import JobId


@dataclass
class PlaylistEntryRecord:
    index: int
    source_id: str
    title: str
    state: PlaylistEntryState = PlaylistEntryState.PENDING
    local_path: str | None = None
    extension: str = "mp4"
    error_code: str | None = None


@dataclass
class PlaylistOrchestrator:
    """In-process sequential playlist coordinator (worker-side friendly)."""

    job_id: JobId
    entries: list[PlaylistEntryRecord] = field(default_factory=list)
    cancelled: bool = False

    def mark_downloading(self, index: int) -> None:
        entry = self._get(index)
        if self.cancelled:
            entry.state = PlaylistEntryState.CANCELLED
            return
        entry.state = PlaylistEntryState.DOWNLOADING

    def mark_succeeded(
        self, index: int, *, path: str, title: str | None = None, extension: str = "mp4"
    ) -> None:
        entry = self._get(index)
        entry.state = PlaylistEntryState.SUCCEEDED
        entry.local_path = path
        entry.extension = extension
        if title:
            entry.title = title

    def mark_failed(self, index: int, *, error_code: str) -> None:
        entry = self._get(index)
        entry.state = PlaylistEntryState.FAILED
        entry.error_code = error_code

    def request_cancel(self) -> None:
        self.cancelled = True
        for entry in self.entries:
            if entry.state in {
                PlaylistEntryState.PENDING,
                PlaylistEntryState.DOWNLOADING,
                PlaylistEntryState.POST_PROCESSING,
            }:
                entry.state = PlaylistEntryState.CANCELLED

    def summary(self) -> dict[str, int]:
        counts = {
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
            "pending": 0,
            "total": len(self.entries),
        }
        for entry in self.entries:
            if entry.state is PlaylistEntryState.SUCCEEDED:
                counts["succeeded"] += 1
            elif entry.state is PlaylistEntryState.FAILED:
                counts["failed"] += 1
            elif entry.state is PlaylistEntryState.CANCELLED:
                counts["cancelled"] += 1
            else:
                counts["pending"] += 1
        return counts

    def outcome_kind(self) -> str:
        s = self.summary()
        if s["succeeded"] == 0 and s["failed"] > 0:
            return "all_failed"
        if s["succeeded"] > 0 and s["failed"] > 0:
            return "partial"
        if s["succeeded"] > 0 and s["failed"] == 0:
            return "success"
        return "empty"

    def successful_entries(self) -> list[PlaylistEntryRecord]:
        return [e for e in self.entries if e.state is PlaylistEntryState.SUCCEEDED and e.local_path]

    def failure_rows(self) -> list[tuple[int, str, str]]:
        return [
            (e.index, e.source_id, e.error_code or "FAILED")
            for e in self.entries
            if e.state is PlaylistEntryState.FAILED
        ]

    def _get(self, index: int) -> PlaylistEntryRecord:
        for entry in self.entries:
            if entry.index == index:
                return entry
        raise KeyError(index)
