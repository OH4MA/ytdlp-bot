"""Dispatcher FIFO claim/launch unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.fakes import FakeClock, InMemoryJobRepository
from ytdlp_bot.application.dispatcher import ClockAwareDispatcher
from ytdlp_bot.domain.enums import JobState, MediaMode, Platform
from ytdlp_bot.domain.identity import Identity, JobId, MessageContext
from ytdlp_bot.domain.jobs import Job


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatcher_claims_and_launches() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    jobs = InMemoryJobRepository()
    launched: list[JobId] = []

    job = await jobs.create(
        Job(
            job_id=JobId("J" * 22),
            idempotency_key="telegram:1",
            owner=Identity(platform=Platform.TELEGRAM, user_id="1"),
            message_context=MessageContext(
                platform=Platform.TELEGRAM,
                chat_id="1",
                response_target="1",
                effective_upload_limit_bytes=1,
            ),
            request_mode=MediaMode.VIDEO,
            selected_preset="best",
            source_display="https://example.com",
            state=JobState.QUEUED,
            dispatchable=True,
            acknowledged_at=clock.now(),
            created_at=clock.now(),
            updated_at=clock.now(),
        )
    )

    async def claim_next(*, controller_id: str, now, expected_states):
        return await jobs.claim_next(
            controller_id=controller_id, now=now, expected_states=expected_states
        )

    async def launch(j: Job) -> None:
        launched.append(j.job_id)

    d = ClockAwareDispatcher(
        claim_next=claim_next,
        launch=launch,
        controller_id="c1",
        concurrency=1,
        now_fn=clock.now,
    )
    assert await d.run_once() is True
    assert launched == [job.job_id]
    assert await d.run_once() is False
