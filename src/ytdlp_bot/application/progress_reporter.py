"""Coalesce progress updates onto one durable platform message."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ytdlp_bot.domain.enums import JobState, WarningCode
from ytdlp_bot.domain.identity import JobId, MessageReference
from ytdlp_bot.domain.progress import ProgressSnapshot, ProgressView

EditProgress = Callable[[MessageReference, ProgressView], Awaitable[None]]
PersistProgress = Callable[[JobId, ProgressSnapshot], Awaitable[None]]


@dataclass
class ProgressReporter:
    edit_progress: EditProgress
    interval: timedelta = timedelta(seconds=5)
    persist_interval: timedelta = timedelta(seconds=30)
    persist: PersistProgress | None = None
    _last_sent: dict[str, datetime] = field(default_factory=dict)
    _last_persist: dict[str, datetime] = field(default_factory=dict)
    _last_seq: dict[str, int] = field(default_factory=dict)
    _last_phase: dict[str, str | None] = field(default_factory=dict)
    _generation: dict[str, int] = field(default_factory=dict)
    _terminal_generation: dict[str, int] = field(default_factory=dict)
    _terminal: set[str] = field(default_factory=set)
    _backoff_until: dict[str, datetime] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _latest: dict[str, ProgressSnapshot] = field(default_factory=dict)

    def _lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

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
        term_gen = self._terminal_generation.get(key)
        if term_gen is not None and self._generation.get(key, 0) <= term_gen:
            return False

        async with self._lock(key):
            last_seq = self._last_seq.get(key, -1)
            if progress.source_sequence < last_seq:
                return False
            # Keep newest snapshot even when throttled.
            prev = self._latest.get(key)
            if prev is None or progress.source_sequence >= prev.source_sequence:
                self._latest[key] = progress
            if progress.source_sequence <= last_seq and not force:
                return False

            phase_changed = progress.phase is not None and (
                progress.phase.value if progress.phase else None
            ) != self._last_phase.get(key)
            backoff = self._backoff_until.get(key)
            if backoff is not None and now < backoff and not force and not phase_changed:
                return False

            last = self._last_sent.get(key)
            if not force and not phase_changed and last is not None and now - last < self.interval:
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
            gen = self._generation.get(key, 0) + 1
            try:
                await self.edit_progress(message_reference, view)
            except Exception as exc:
                # Honor Retry-After style hints on exception attribute when present.
                hint = getattr(exc, "retry_after_seconds", None)
                if isinstance(hint, (int, float)) and hint > 0:
                    delay = min(float(hint), 60.0)
                    self._backoff_until[key] = now + timedelta(seconds=delay)
                return False

            self._generation[key] = gen
            self._last_sent[key] = now
            self._last_seq[key] = progress.source_sequence
            self._last_phase[key] = progress.phase.value if progress.phase else None
            self._backoff_until.pop(key, None)

            if self.persist is not None:
                last_p = self._last_persist.get(key)
                if (
                    phase_changed
                    or force
                    or last_p is None
                    or now - last_p >= self.persist_interval
                ):
                    await self.persist(job_id, progress)
                    self._last_persist[key] = now
            return True

    def mark_terminal(self, job_id: JobId) -> None:
        key = job_id.value
        self._terminal.add(key)
        self._terminal_generation[key] = self._generation.get(key, 0) + 1
        self._latest.pop(key, None)

    def clear_job(self, job_id: JobId) -> None:
        """Drop per-job throttle state after terminal delivery."""
        key = job_id.value
        self._last_sent.pop(key, None)
        self._last_persist.pop(key, None)
        self._last_seq.pop(key, None)
        self._last_phase.pop(key, None)
        self._generation.pop(key, None)
        self._backoff_until.pop(key, None)
        self._latest.pop(key, None)
        self._locks.pop(key, None)
        # Keep terminal flags so late progress never resurrects.
