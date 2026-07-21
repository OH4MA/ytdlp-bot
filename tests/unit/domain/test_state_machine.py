"""FND-07: job/artifact/playlist state machines."""

from __future__ import annotations

import pytest

from ytdlp_bot.domain.enums import (
    ArtifactAccessState,
    DeletionReason,
    JobState,
    PlaylistEntryState,
)
from ytdlp_bot.domain.errors import IllegalTransitionError
from ytdlp_bot.domain.state_machine import (
    all_allowed_job_edges,
    apply_artifact_transition,
    apply_job_transition,
    apply_playlist_entry_transition,
    is_allowed_transition,
    require_job_transition,
)


@pytest.mark.unit
def test_every_allowed_edge_succeeds() -> None:
    for frm, to in all_allowed_job_edges():
        if frm is to:
            result = apply_job_transition(frm, to)
            assert result.ok
            continue
        kwargs: dict = {}
        if to in {JobState.COMPLETED, JobState.COMPLETED_WITH_ERRORS}:
            kwargs["has_available_artifact"] = True
        if to is JobState.COMPLETED_WITH_ERRORS:
            kwargs["playlist_succeeded"] = 1
            kwargs["playlist_failed"] = 1
        if frm is JobState.FAILED and to in {JobState.EXPIRED, JobState.EVICTED}:
            kwargs["has_available_artifact"] = True
        if to in {JobState.EXPIRED, JobState.EVICTED} and frm in {
            JobState.COMPLETED,
            JobState.COMPLETED_WITH_ERRORS,
        }:
            kwargs["has_available_artifact"] = True
        result = apply_job_transition(frm, to, **kwargs)
        assert result.ok, (frm, to, result.reason)


@pytest.mark.unit
def test_illegal_edges_rejected() -> None:
    assert is_allowed_transition(JobState.COMPLETED, JobState.QUEUED) is False
    result = apply_job_transition(JobState.COMPLETED, JobState.QUEUED)
    assert result.ok is False
    with pytest.raises(IllegalTransitionError):
        require_job_transition(JobState.CANCELLED, JobState.DOWNLOADING)


@pytest.mark.unit
def test_completed_requires_artifact() -> None:
    r = apply_job_transition(
        JobState.DELIVERING,
        JobState.COMPLETED,
        has_available_artifact=False,
    )
    assert r.ok is False


@pytest.mark.unit
def test_completed_with_errors_requires_partial() -> None:
    r = apply_job_transition(
        JobState.DELIVERING,
        JobState.COMPLETED_WITH_ERRORS,
        has_available_artifact=True,
        playlist_succeeded=2,
        playlist_failed=0,
    )
    assert r.ok is False


@pytest.mark.unit
def test_late_cancel_idempotent_on_completed() -> None:
    r = apply_job_transition(
        JobState.COMPLETED,
        JobState.CANCELLING,
        late_cancel_vs_completed=True,
    )
    assert r.ok and r.idempotent and r.new_state is JobState.COMPLETED


@pytest.mark.unit
def test_idempotent_terminal_reapply() -> None:
    r = apply_job_transition(JobState.CANCELLED, JobState.CANCELLED)
    assert r.ok and r.idempotent


@pytest.mark.unit
def test_artifact_lifecycle() -> None:
    r = apply_artifact_transition(
        ArtifactAccessState.AVAILABLE,
        ArtifactAccessState.DELETION_PENDING,
        deletion_reason=DeletionReason.EXPIRED,
    )
    assert r.ok
    r2 = apply_artifact_transition(
        ArtifactAccessState.DELETION_PENDING,
        ArtifactAccessState.DELETED,
    )
    assert r2.ok
    bad = apply_artifact_transition(
        ArtifactAccessState.DELETED,
        ArtifactAccessState.AVAILABLE,
    )
    assert bad.ok is False


@pytest.mark.unit
def test_playlist_entry_terminal_immutable() -> None:
    r = apply_playlist_entry_transition(PlaylistEntryState.SUCCEEDED, PlaylistEntryState.FAILED)
    assert r.ok is False
    ok = apply_playlist_entry_transition(PlaylistEntryState.PENDING, PlaylistEntryState.DOWNLOADING)
    assert ok.ok
