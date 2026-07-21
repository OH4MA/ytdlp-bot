"""Coalesce progress updates onto one durable platform message."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ytdlp_bot.domain.enums import JobState, WarningCode
from ytdlp_bot.domain.identity import JobId, MessageReference
from ytdlp_bot.domain.progress import ProgressSnapshot, ProgressView


@dataclass
class ProgressReporter:
    edit_progress: object  # async callable
    interval: timedelta = timedelta(seconds=5)
    _last_sent: dict[str, datetime] = field(default_factory=dict)
    _last_seq: dict[str, int] = field(default_factory=dict)
    _terminal: set[str] = field(default_factory=set)

    async def on_progress(
        self,
        *,
        job_id: JobId,
        state: JobState,
        message_reference: MessageReference,
        progress: ProgressSnapshot,
        now: datetime,
        force: bool = False,
    ) -> bool:
        key = job_id.value
        if key in self._terminal:
            return False
        last_seq = self._last_seq.get(key, -1)
        if progress.source_sequence <= last_seq and not force:
            return False
        last = self._last_sent.get(key)
        if not force and last is not None and now - last < self.interval:
            return False
        view = ProgressView(
            job_id=job_id,
            state=state.value,
            phase=progress.phase.value if progress.phase else None,
            percent=progress.percent,
            playlist_completed=progress.playlist_completed,
            playlist_total=progress.playlist_total,
            current_entry_title=progress.current_entry_title,
            warning_codes=(
                (WarningCode.PROTOCOL_MALFORMED_PROGRESS,) if progress.malformed else ()
            ),
        )
        await self.edit_progress(message_reference, view)  # type: ignore[operator]
        self._last_sent[key] = now
        self._last_seq[key] = progress.source_sequence
        return True

    def mark_terminal(self, job_id: JobId) -> None:
        self._terminal.add(job_id.value)
