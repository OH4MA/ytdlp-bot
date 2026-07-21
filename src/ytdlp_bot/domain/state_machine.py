"""Pure job and artifact state machines."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ytdlp_bot.domain.enums import (
    ACTIVE_JOB_STATES,
    ARTIFACT_BEARING_JOB_STATES,
    ArtifactAccessState,
    DeletionReason,
    FailureCode,
    JobState,
    PlaylistEntryState,
)
from ytdlp_bot.domain.errors import IllegalTransitionError, failure


class JobTransitionTrigger(StrEnum):
    """Logical triggers for job transitions (documentation / tests)."""

    CLAIM = "claim"
    CANCEL_REQUEST = "cancel_request"
    ACK_OR_POLICY_FAIL = "ack_or_policy_fail"
    RESTART_QUEUED = "restart_queued"
    INSPECTION_OK = "inspection_ok"
    VALIDATION_FAIL = "validation_fail"
    PROGRESS_ONLY = "progress_only"
    NEED_POST_PROCESS = "need_post_process"
    PLAYLIST_READY_ARCHIVE = "playlist_ready_archive"
    SINGLE_READY_DELIVER = "single_ready_deliver"
    DOWNLOAD_EXHAUSTED = "download_exhausted"
    NEXT_PLAYLIST_ENTRY = "next_playlist_entry"
    POST_PROCESS_OK_DELIVER = "post_process_ok_deliver"
    POST_PROCESS_FAIL = "post_process_fail"
    ARCHIVE_OK = "archive_ok"
    ARCHIVE_FAIL = "archive_fail"
    DELIVERY_OK = "delivery_ok"
    DELIVERY_PARTIAL = "delivery_partial"
    DELIVERY_FAIL = "delivery_fail"
    CANCEL_COMPLETE = "cancel_complete"
    RESTART_ACTIVE = "restart_active"
    ARTIFACT_EXPIRED = "artifact_expired"
    ARTIFACT_EVICTED = "artifact_evicted"


# Allowed directed edges including restart cancellation from active states.
_ALLOWED_EDGES: frozenset[tuple[JobState, JobState]] = frozenset(
    {
        (JobState.QUEUED, JobState.INSPECTING),
        (JobState.QUEUED, JobState.CANCELLING),
        (JobState.QUEUED, JobState.FAILED),
        (JobState.QUEUED, JobState.CANCELLED_BY_RESTART),
        (JobState.INSPECTING, JobState.DOWNLOADING),
        (JobState.INSPECTING, JobState.CANCELLING),
        (JobState.INSPECTING, JobState.FAILED),
        (JobState.DOWNLOADING, JobState.POST_PROCESSING),
        (JobState.DOWNLOADING, JobState.ARCHIVING),
        (JobState.DOWNLOADING, JobState.DELIVERING),
        (JobState.DOWNLOADING, JobState.CANCELLING),
        (JobState.DOWNLOADING, JobState.FAILED),
        (JobState.POST_PROCESSING, JobState.DOWNLOADING),
        (JobState.POST_PROCESSING, JobState.ARCHIVING),
        (JobState.POST_PROCESSING, JobState.DELIVERING),
        (JobState.POST_PROCESSING, JobState.CANCELLING),
        (JobState.POST_PROCESSING, JobState.FAILED),
        (JobState.ARCHIVING, JobState.DELIVERING),
        (JobState.ARCHIVING, JobState.CANCELLING),
        (JobState.ARCHIVING, JobState.FAILED),
        (JobState.DELIVERING, JobState.COMPLETED),
        (JobState.DELIVERING, JobState.COMPLETED_WITH_ERRORS),
        (JobState.DELIVERING, JobState.CANCELLING),
        (JobState.DELIVERING, JobState.FAILED),
        (JobState.CANCELLING, JobState.CANCELLED),
        (JobState.COMPLETED, JobState.EXPIRED),
        (JobState.COMPLETED, JobState.EVICTED),
        (JobState.COMPLETED_WITH_ERRORS, JobState.EXPIRED),
        (JobState.COMPLETED_WITH_ERRORS, JobState.EVICTED),
        (JobState.FAILED, JobState.EXPIRED),
        (JobState.FAILED, JobState.EVICTED),
        *{(state, JobState.CANCELLED_BY_RESTART) for state in ACTIVE_JOB_STATES},
    }
)


@dataclass(frozen=True, slots=True)
class TransitionResult[T]:
    """Result of evaluating a state transition."""

    ok: bool
    new_state: T | None
    reason: str = ""
    idempotent: bool = False


def is_allowed_transition(from_state: JobState, to_state: JobState) -> bool:
    """Return whether the directed edge is allowed by the design table."""
    if from_state is to_state:
        # Self-edge only for downloading progress (version does not change).
        return from_state is JobState.DOWNLOADING
    return (from_state, to_state) in _ALLOWED_EDGES


def apply_job_transition(
    current: JobState,
    target: JobState,
    *,
    cancellation_requested: bool = False,
    has_available_artifact: bool = False,
    playlist_succeeded: int = 0,
    playlist_failed: int = 0,
    late_cancel_vs_completed: bool = False,
) -> TransitionResult[JobState]:
    """Evaluate a transition with design invariants.

    Pure function: does not mutate any entity. Callers apply CAS on version.
    """
    # Late cancellation that loses to completed: keep completed (idempotent).
    if late_cancel_vs_completed and current in {
        JobState.COMPLETED,
        JobState.COMPLETED_WITH_ERRORS,
    }:
        return TransitionResult[JobState](
            ok=True,
            new_state=current,
            reason="late_cancel_keeps_completed",
            idempotent=True,
        )

    # Idempotent reapplication of the same terminal.
    if current is target and current in {
        JobState.COMPLETED,
        JobState.COMPLETED_WITH_ERRORS,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.CANCELLED_BY_RESTART,
        JobState.EXPIRED,
        JobState.EVICTED,
        JobState.CANCELLING,
    }:
        return TransitionResult[JobState](
            ok=True,
            new_state=current,
            reason="idempotent_terminal",
            idempotent=True,
        )

    # Progress-only self transition for downloading.
    if current is JobState.DOWNLOADING and target is JobState.DOWNLOADING:
        return TransitionResult[JobState](
            ok=True,
            new_state=current,
            reason="progress_only",
            idempotent=True,
        )

    if not is_allowed_transition(current, target):
        return TransitionResult[JobState](
            ok=False,
            new_state=None,
            reason=f"illegal_transition:{current.value}->{target.value}",
        )

    # Cancellation flag is monotonic; transitioning to cancelling is fine.
    if target is JobState.CANCELLING and not cancellation_requested:
        # Still allow if caller is setting the flag atomically with the transition.
        pass

    # completed requires available artifact.
    if (
        target in {JobState.COMPLETED, JobState.COMPLETED_WITH_ERRORS}
        and not has_available_artifact
    ):
        return TransitionResult(
            ok=False,
            new_state=None,
            reason="completed_requires_available_artifact",
        )

    # completed_with_errors requires at least one success and one failure.
    if target is JobState.COMPLETED_WITH_ERRORS and (playlist_succeeded < 1 or playlist_failed < 1):
        return TransitionResult(
            ok=False,
            new_state=None,
            reason="completed_with_errors_requires_partial_playlist",
        )

    # All-failed playlist must not complete with artifact.
    if (
        target is JobState.FAILED
        and playlist_succeeded == 0
        and playlist_failed > 0
        and has_available_artifact
    ):
        return TransitionResult(
            ok=False,
            new_state=None,
            reason="all_failed_playlist_must_not_have_artifact",
        )

    # expired/evicted only from artifact-bearing states.
    if (
        target in {JobState.EXPIRED, JobState.EVICTED}
        and current not in ARTIFACT_BEARING_JOB_STATES
    ):
        return TransitionResult(
            ok=False,
            new_state=None,
            reason="expire_only_from_artifact_bearing",
        )

    # failed -> expire/evict only when an artifact was retained.
    if (
        current is JobState.FAILED
        and target in {JobState.EXPIRED, JobState.EVICTED}
        and not has_available_artifact
    ):
        # Callers pass has_available_artifact=True when job still references artifact.
        return TransitionResult(
            ok=False,
            new_state=None,
            reason="failed_without_artifact_cannot_expire",
        )

    return TransitionResult(ok=True, new_state=target, reason="ok")


def require_job_transition(
    current: JobState,
    target: JobState,
    **kwargs: object,
) -> JobState:
    """Like apply_job_transition but raises IllegalTransitionError on failure."""
    # Type narrowing for kwargs used by apply_job_transition.
    result = apply_job_transition(
        current,
        target,
        cancellation_requested=bool(kwargs.get("cancellation_requested", False)),
        has_available_artifact=bool(kwargs.get("has_available_artifact", False)),
        playlist_succeeded=int(kwargs.get("playlist_succeeded", 0)),  # type: ignore[arg-type]
        playlist_failed=int(kwargs.get("playlist_failed", 0)),  # type: ignore[arg-type]
        late_cancel_vs_completed=bool(kwargs.get("late_cancel_vs_completed", False)),
    )
    if not result.ok or result.new_state is None:
        raise IllegalTransitionError(
            failure(
                FailureCode.INTERNAL_ERROR,
                diagnostic=result.reason,
                safe_context={
                    "from": current.value,
                    "to": target.value,
                },
            )
        )
    return result.new_state


# ---------------------------------------------------------------------------
# Artifact lifecycle
# ---------------------------------------------------------------------------

_ARTIFACT_EDGES: frozenset[tuple[ArtifactAccessState, ArtifactAccessState]] = frozenset(
    {
        (ArtifactAccessState.AVAILABLE, ArtifactAccessState.DELETION_PENDING),
        (ArtifactAccessState.DELETION_PENDING, ArtifactAccessState.DELETED),
        # Idempotent re-mark pending while unlink retries.
        (ArtifactAccessState.DELETION_PENDING, ArtifactAccessState.DELETION_PENDING),
        (ArtifactAccessState.DELETED, ArtifactAccessState.DELETED),
    }
)


def apply_artifact_transition(
    current: ArtifactAccessState,
    target: ArtifactAccessState,
    *,
    deletion_reason: DeletionReason | None = None,
) -> TransitionResult[ArtifactAccessState]:
    """Evaluate artifact access_state transition."""
    if current is target and current is ArtifactAccessState.AVAILABLE:
        return TransitionResult[ArtifactAccessState](
            ok=False,
            new_state=None,
            reason="no_self_transition_on_available",
        )
    if (current, target) not in _ARTIFACT_EDGES:
        return TransitionResult[ArtifactAccessState](
            ok=False,
            new_state=None,
            reason=f"illegal_artifact_transition:{current.value}->{target.value}",
        )
    if (
        target is ArtifactAccessState.DELETION_PENDING
        and deletion_reason is None
        and current is not ArtifactAccessState.DELETION_PENDING
    ):
        return TransitionResult[ArtifactAccessState](
            ok=False,
            new_state=None,
            reason="deletion_pending_requires_reason",
        )
    if current is target:
        return TransitionResult[ArtifactAccessState](
            ok=True,
            new_state=current,
            reason="idempotent",
            idempotent=True,
        )
    return TransitionResult[ArtifactAccessState](ok=True, new_state=target, reason="ok")


# ---------------------------------------------------------------------------
# Playlist entry lifecycle
# ---------------------------------------------------------------------------

_ENTRY_EDGES: frozenset[tuple[PlaylistEntryState, PlaylistEntryState]] = frozenset(
    {
        (PlaylistEntryState.PENDING, PlaylistEntryState.DOWNLOADING),
        (PlaylistEntryState.PENDING, PlaylistEntryState.FAILED),
        (PlaylistEntryState.PENDING, PlaylistEntryState.CANCELLED),
        (PlaylistEntryState.DOWNLOADING, PlaylistEntryState.POST_PROCESSING),
        (PlaylistEntryState.DOWNLOADING, PlaylistEntryState.SUCCEEDED),
        (PlaylistEntryState.DOWNLOADING, PlaylistEntryState.FAILED),
        (PlaylistEntryState.DOWNLOADING, PlaylistEntryState.CANCELLED),
        (PlaylistEntryState.POST_PROCESSING, PlaylistEntryState.SUCCEEDED),
        (PlaylistEntryState.POST_PROCESSING, PlaylistEntryState.FAILED),
        (PlaylistEntryState.POST_PROCESSING, PlaylistEntryState.CANCELLED),
    }
)

_ENTRY_TERMINAL = frozenset(
    {
        PlaylistEntryState.SUCCEEDED,
        PlaylistEntryState.FAILED,
        PlaylistEntryState.CANCELLED,
    }
)


def apply_playlist_entry_transition(
    current: PlaylistEntryState,
    target: PlaylistEntryState,
) -> TransitionResult[PlaylistEntryState]:
    """Evaluate playlist entry transition; terminals never leave."""
    if current in _ENTRY_TERMINAL:
        if current is target:
            return TransitionResult[PlaylistEntryState](
                ok=True,
                new_state=current,
                reason="idempotent_terminal",
                idempotent=True,
            )
        return TransitionResult[PlaylistEntryState](
            ok=False,
            new_state=None,
            reason="entry_terminal_immutable",
        )
    if (current, target) not in _ENTRY_EDGES:
        return TransitionResult[PlaylistEntryState](
            ok=False,
            new_state=None,
            reason=f"illegal_entry_transition:{current.value}->{target.value}",
        )
    return TransitionResult[PlaylistEntryState](ok=True, new_state=target, reason="ok")


def all_allowed_job_edges() -> frozenset[tuple[JobState, JobState]]:
    """Expose the edge set for exhaustive tests."""
    return _ALLOWED_EDGES | {(JobState.DOWNLOADING, JobState.DOWNLOADING)}
