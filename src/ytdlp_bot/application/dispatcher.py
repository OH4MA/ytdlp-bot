"""Database-backed FIFO job dispatcher."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from ytdlp_bot.domain.enums import JobState
from ytdlp_bot.domain.jobs import Job

ClaimFn = Callable[..., Awaitable[Job | None]]
LaunchFn = Callable[[Job], Awaitable[None]]
NowFn = Callable[[], datetime]


@dataclass
class Dispatcher:
    claim_next: ClaimFn
    launch: LaunchFn
    controller_id: str
    concurrency: int = 2
    scan_interval_seconds: float = 0.05
    _wake: asyncio.Event = field(default_factory=asyncio.Event)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _active: int = 0
    _sem: asyncio.Semaphore | None = None
    admission_open: bool = True

    def __post_init__(self) -> None:
        self._sem = asyncio.Semaphore(self.concurrency)

    def wake(self) -> None:
        self._wake.set()

    def close_admission(self) -> None:
        self.admission_open = False

    async def run_once(self) -> bool:
        """Claim and launch at most one job. Returns True if work was launched."""
        if not self.admission_open:
            return False
        assert self._sem is not None
        if self._sem.locked() and self._active >= self.concurrency:
            return False
        await self._sem.acquire()
        try:
            job = await self.claim_next(
                controller_id=self.controller_id,
                now=datetime.now().astimezone(),  # callers should inject clock via claim
                expected_states=[JobState.QUEUED],
            )
            # Prefer claim with injected now via partial; fallback above is last resort.
            if job is None:
                self._sem.release()
                return False
            self._active += 1
            try:
                await self.launch(job)
            finally:
                self._active -= 1
                self._sem.release()
            return True
        except Exception:
            self._sem.release()
            raise

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            did = await self.run_once()
            if not did:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._wake.wait(), timeout=self.scan_interval_seconds
                    )
                self._wake.clear()

    def stop(self) -> None:
        self._stop.set()
        self.wake()


@dataclass
class ClockAwareDispatcher(Dispatcher):
    now_fn: NowFn | None = None

    async def run_once(self) -> bool:
        if not self.admission_open:
            return False
        assert self._sem is not None
        await self._sem.acquire()
        try:
            now = self.now_fn() if self.now_fn else datetime.now().astimezone()
            job = await self.claim_next(
                controller_id=self.controller_id,
                now=now,
                expected_states=[JobState.QUEUED],
            )
            if job is None:
                self._sem.release()
                return False
            self._active += 1
            try:
                await self.launch(job)
            finally:
                self._active -= 1
                self._sem.release()
            return True
        except Exception:
            self._sem.release()
            raise
