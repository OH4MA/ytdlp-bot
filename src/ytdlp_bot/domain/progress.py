"""Progress and delivery presentation value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ytdlp_bot.domain.enums import (
    DeliveryPlan,
    FailureCode,
    Platform,
    UploadOutcome,
    WarningCode,
    WorkerPhase,
)
from ytdlp_bot.domain.errors import ValidationError, failure
from ytdlp_bot.domain.identity import JobId, MessageReference


def _err(msg: str) -> ValidationError:
    return ValidationError(failure(FailureCode.WORKER_PROTOCOL_ERROR, diagnostic=msg))


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    """Coalesced progress snapshot stored on a job."""

    phase: WorkerPhase | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed_bytes_per_second: int | None = None
    eta_seconds: int | None = None
    playlist_completed: int | None = None
    playlist_total: int | None = None
    current_entry_index: int | None = None
    current_entry_title: str | None = None
    updated_at: datetime | None = None
    source_sequence: int = 0
    malformed: bool = False

    def __post_init__(self) -> None:
        # Clamp display-only; mark malformed if originals were negative/overflow.
        object.__setattr__(
            self,
            "downloaded_bytes",
            _nonneg_or_none(self.downloaded_bytes, allow_none=True),
        )
        object.__setattr__(self, "total_bytes", _nonneg_or_none(self.total_bytes, allow_none=True))
        object.__setattr__(
            self,
            "speed_bytes_per_second",
            _nonneg_or_none(self.speed_bytes_per_second, allow_none=True),
        )
        object.__setattr__(self, "eta_seconds", _nonneg_or_none(self.eta_seconds, allow_none=True))
        if self.current_entry_title is not None and len(self.current_entry_title) > 200:
            object.__setattr__(self, "current_entry_title", self.current_entry_title[:200])

    @property
    def percent(self) -> int | None:
        """Safe integer percent, or None when total is unknown/zero."""
        total = self.total_bytes
        done = self.downloaded_bytes
        if total is None or done is None or total <= 0:
            return None
        raw = int((done * 100) // total)
        return max(0, min(100, raw))

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase.value if self.phase else None,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "speed_bytes_per_second": self.speed_bytes_per_second,
            "eta_seconds": self.eta_seconds,
            "playlist_completed": self.playlist_completed,
            "playlist_total": self.playlist_total,
            "current_entry_index": self.current_entry_index,
            "current_entry_title": self.current_entry_title,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "source_sequence": self.source_sequence,
            "percent": self.percent,
            "malformed": self.malformed,
        }


def progress_from_worker_values(
    *,
    phase: WorkerPhase | None,
    downloaded_bytes: int | None,
    total_bytes: int | None,
    speed_bytes_per_second: int | None,
    eta_seconds: int | None,
    playlist_completed: int | None,
    playlist_total: int | None,
    current_entry_index: int | None,
    current_entry_title: str | None,
    updated_at: datetime,
    source_sequence: int,
) -> ProgressSnapshot:
    """Build a snapshot, clamping display values and flagging malformed inputs."""
    malformed = False
    for value in (
        downloaded_bytes,
        total_bytes,
        speed_bytes_per_second,
        eta_seconds,
        playlist_completed,
        playlist_total,
        current_entry_index,
    ):
        if value is not None and (value < 0 or value > 2**63 - 1):
            malformed = True
            break
    if source_sequence < 0:
        raise _err("source_sequence must be non-negative")
    return ProgressSnapshot(
        phase=phase,
        downloaded_bytes=downloaded_bytes
        if downloaded_bytes is not None and downloaded_bytes >= 0
        else None,
        total_bytes=total_bytes if total_bytes is not None and total_bytes >= 0 else None,
        speed_bytes_per_second=(
            speed_bytes_per_second
            if speed_bytes_per_second is not None and speed_bytes_per_second >= 0
            else None
        ),
        eta_seconds=eta_seconds if eta_seconds is not None and eta_seconds >= 0 else None,
        playlist_completed=(
            playlist_completed
            if playlist_completed is not None and playlist_completed >= 0
            else None
        ),
        playlist_total=playlist_total
        if playlist_total is not None and playlist_total >= 0
        else None,
        current_entry_index=(
            current_entry_index
            if current_entry_index is not None and current_entry_index >= 0
            else None
        ),
        current_entry_title=current_entry_title,
        updated_at=updated_at,
        source_sequence=source_sequence,
        malformed=malformed,
    )


def _nonneg_or_none(value: int | None, *, allow_none: bool) -> int | None:
    if value is None:
        return None if allow_none else 0
    if value < 0:
        return 0
    return value


@dataclass(frozen=True, slots=True)
class ProgressView:
    """Locale-ready progress view for platform edit_progress."""

    job_id: JobId
    state: str
    phase: str | None
    percent: int | None
    playlist_completed: int | None
    playlist_total: int | None
    current_entry_title: str | None
    warning_codes: tuple[WarningCode, ...]
    message_key: str = "progress.update"


@dataclass(frozen=True, slots=True)
class DeliveryCapabilities:
    """Effective upload limit for a platform context."""

    platform: Platform
    effective_upload_limit_bytes: int

    def __post_init__(self) -> None:
        if self.effective_upload_limit_bytes < 0:
            raise ValidationError(
                failure(
                    FailureCode.INTERNAL_ERROR,
                    diagnostic="effective_upload_limit_bytes must be non-negative",
                )
            )


@dataclass(frozen=True, slots=True)
class DeliveryPlanDecision:
    """Chosen plan for an artifact of a given size."""

    plan: DeliveryPlan
    byte_size: int
    limit_bytes: int

    @classmethod
    def decide(cls, byte_size: int, limit_bytes: int) -> DeliveryPlanDecision:
        if byte_size < 0 or limit_bytes < 0:
            raise ValidationError(
                failure(
                    FailureCode.INTERNAL_ERROR,
                    diagnostic="sizes must be non-negative",
                )
            )
        plan = DeliveryPlan.DIRECT_UPLOAD if byte_size <= limit_bytes else DeliveryPlan.SIGNED_LINK
        return cls(plan=plan, byte_size=byte_size, limit_bytes=limit_bytes)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Outcome of a delivery attempt (no complete signed URL persisted)."""

    plan: DeliveryPlan
    attempt_count: int
    upload_outcome: UploadOutcome | None = None
    platform_message: MessageReference | None = None
    link_expires_at: datetime | None = None
    error_code: FailureCode | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "plan": self.plan.value,
            "attempt_count": self.attempt_count,
            "upload_outcome": self.upload_outcome.value if self.upload_outcome else None,
            "platform_message": (
                self.platform_message.to_dict() if self.platform_message else None
            ),
            "link_expires_at": (self.link_expires_at.isoformat() if self.link_expires_at else None),
            "error_code": self.error_code.value if self.error_code else None,
        }


@dataclass(frozen=True, slots=True)
class FinalOutcomeView:
    """Safe final outcome for send_final."""

    job_id: JobId
    outcome: Literal[
        "completed",
        "completed_with_errors",
        "failed",
        "cancelled",
        "cancelled_by_restart",
        "expired",
        "evicted",
    ]
    message_key: str
    warning_codes: tuple[WarningCode, ...] = ()
    error_code: FailureCode | None = None
    has_signed_link_hint: bool = False
    delivery_plan: DeliveryPlan | None = None


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    """Descriptor for platform upload (no absolute storage path)."""

    artifact_id: str
    display_name: str
    media_type: str
    byte_size: int
    storage_key: str
