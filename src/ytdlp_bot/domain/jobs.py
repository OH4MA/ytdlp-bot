"""Job, playlist, artifact, and runtime-setting domain models."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from ytdlp_bot.domain.enums import (
    ArtifactAccessState,
    AudioBitrate,
    DeletionReason,
    FailureCode,
    JobKind,
    JobState,
    MediaMode,
    MediaType,
    PlaylistEntryState,
    VideoQuality,
    WarningCode,
)
from ytdlp_bot.domain.errors import ValidationError, failure
from ytdlp_bot.domain.identity import (
    ArtifactId,
    Identity,
    JobId,
    MessageContext,
    MessageReference,
    validate_storage_key,
)
from ytdlp_bot.domain.progress import ProgressSnapshot

_DISPLAY_HOST_MAX = 253
_DISPLAY_NAME_MAX = 200
_TITLE_MAX = 200


def _err(msg: str) -> ValidationError:
    return ValidationError(failure(FailureCode.INTERNAL_ERROR, diagnostic=msg))


@dataclass(frozen=True, slots=True)
class DownloadRequest:
    """Normalized download request (URL retained only in operational payload)."""

    source_url: str
    mode: MediaMode
    video_quality: VideoQuality | None = None
    audio_bitrate: AudioBitrate | None = None

    def __post_init__(self) -> None:
        if not self.source_url:
            raise _err("source_url is required")
        if len(self.source_url) > 4096:
            raise _err("source_url exceeds maximum length")
        if self.mode is MediaMode.VIDEO:
            if self.video_quality is None:
                raise _err("video mode requires video_quality")
            if self.audio_bitrate is not None:
                raise _err("video mode must not carry audio_bitrate")
        elif self.mode is MediaMode.AUDIO:
            if self.audio_bitrate is None:
                raise _err("audio mode requires audio_bitrate")
            if self.video_quality is not None:
                raise _err("audio mode must not carry video_quality")
        else:
            raise _err("unknown media mode")


@dataclass(frozen=True, slots=True)
class WorkerLease:
    """Controller lease metadata for an active job."""

    controller_id: str
    heartbeat_at: datetime

    def __post_init__(self) -> None:
        if not self.controller_id or len(self.controller_id) > 128:
            raise _err("invalid controller_id")


@dataclass(frozen=True, slots=True)
class Job:
    """Authoritative application lifecycle record (no complete source URL)."""

    job_id: JobId
    idempotency_key: str
    owner: Identity
    message_context: MessageContext
    request_mode: MediaMode
    selected_preset: str
    source_display: str
    state: JobState
    kind: JobKind = JobKind.UNKNOWN
    progress: ProgressSnapshot | None = None
    warning_codes: tuple[WarningCode, ...] = ()
    error_code: FailureCode | None = None
    cancellation_requested: bool = False
    dispatchable: bool = False
    message_reference: MessageReference | None = None
    acknowledged_at: datetime | None = None
    worker_lease: WorkerLease | None = None
    version: int = 1
    created_at: datetime | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    ready_at: datetime | None = None
    terminal_at: datetime | None = None
    last_event_sequence: int = 0

    def __post_init__(self) -> None:
        if not self.idempotency_key or len(self.idempotency_key) > 512:
            raise _err("invalid idempotency_key")
        if not self.selected_preset or len(self.selected_preset) > 32:
            raise _err("invalid selected_preset")
        if not self.source_display or len(self.source_display) > _DISPLAY_HOST_MAX + 16:
            raise _err("invalid source_display")
        # Forbid embedding raw URL path/query in source_display markers.
        if "://" in self.source_display and self.source_display.count("/") > 2:
            raise _err("source_display must be sanitized scheme+host only")
        if self.version < 1:
            raise _err("version must be >= 1")

    def with_updates(self, **kwargs: Any) -> Job:
        """Return a copy with updates (controlled immutability)."""
        return replace(self, **kwargs)

    def to_dict(self) -> dict[str, object]:
        """Secret-free serialization (never includes source URL)."""
        return {
            "job_id": self.job_id.value,
            "idempotency_key": self.idempotency_key,
            "owner": self.owner.to_dict(),
            "request_mode": self.request_mode.value,
            "selected_preset": self.selected_preset,
            "source_display": self.source_display,
            "state": self.state.value,
            "kind": self.kind.value,
            "progress": self.progress.to_dict() if self.progress else None,
            "warning_codes": [w.value for w in self.warning_codes],
            "error_code": self.error_code.value if self.error_code else None,
            "cancellation_requested": self.cancellation_requested,
            "dispatchable": self.dispatchable,
            "version": self.version,
            "last_event_sequence": self.last_event_sequence,
        }


@dataclass(frozen=True, slots=True)
class PlaylistEntry:
    """One playlist entry metadata row (no complete entry URL)."""

    job_id: JobId
    playlist_index: int
    extractor_source_id: str
    sanitized_title: str
    state: PlaylistEntryState
    generated_output_name: str | None = None
    byte_size: int | None = None
    failure_code: FailureCode | None = None

    def __post_init__(self) -> None:
        if self.playlist_index < 1:
            raise _err("playlist_index is one-based")
        if not self.extractor_source_id or len(self.extractor_source_id) > 256:
            raise _err("invalid extractor_source_id")
        title = self.sanitized_title[:_TITLE_MAX]
        object.__setattr__(self, "sanitized_title", title)
        if self.byte_size is not None and self.byte_size < 0:
            raise _err("byte_size must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id.value,
            "playlist_index": self.playlist_index,
            "extractor_source_id": self.extractor_source_id,
            "sanitized_title": self.sanitized_title,
            "state": self.state.value,
            "generated_output_name": self.generated_output_name,
            "byte_size": self.byte_size,
            "failure_code": self.failure_code.value if self.failure_code else None,
        }


@dataclass(frozen=True, slots=True)
class Artifact:
    """Exactly one final deliverable for one job."""

    artifact_id: ArtifactId
    job_id: JobId
    storage_key: str
    display_name: str
    media_type: MediaType
    byte_size: int
    ready_at: datetime
    expires_at: datetime
    access_state: ArtifactAccessState = ArtifactAccessState.AVAILABLE
    deletion_reason: DeletionReason | None = None
    token_version: int = 1
    deletion_retry_count: int = 0
    deletion_next_attempt_at: datetime | None = None
    deletion_last_error: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        validate_storage_key(self.storage_key)
        if not self.display_name or len(self.display_name) > _DISPLAY_NAME_MAX:
            raise _err("invalid display_name")
        if self.byte_size < 0:
            raise _err("byte_size must be non-negative")
        if self.token_version < 1:
            raise _err("token_version must be >= 1")
        if self.version < 1:
            raise _err("version must be >= 1")
        if self.expires_at < self.ready_at:
            raise _err("expires_at must be >= ready_at")
        if self.deletion_last_error and len(self.deletion_last_error) > 256:
            object.__setattr__(self, "deletion_last_error", self.deletion_last_error[:256])

    def with_updates(self, **kwargs: Any) -> Artifact:
        return replace(self, **kwargs)

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id.value,
            "job_id": self.job_id.value,
            "storage_key": self.storage_key,
            "display_name": self.display_name,
            "media_type": self.media_type.value,
            "byte_size": self.byte_size,
            "ready_at": self.ready_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "access_state": self.access_state.value,
            "deletion_reason": (self.deletion_reason.value if self.deletion_reason else None),
            "token_version": self.token_version,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class RuntimeSetting:
    """One runtime setting override row (non-secret values only)."""

    key: str
    value: object
    updated_at: datetime
    updated_by: Identity | None = None

    def __post_init__(self) -> None:
        if not self.key or len(self.key) > 64:
            raise _err("invalid setting key")


@dataclass(frozen=True, slots=True)
class JobPayload:
    """Operational payload holding the complete source URL while required."""

    job_id: JobId
    source_url: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.source_url or len(self.source_url) > 4096:
            raise _err("invalid source_url")


def sanitize_source_display(scheme: str, host: str) -> str:
    """Build a safe scheme+host display string for status and diagnostics."""
    scheme_l = scheme.lower().strip()
    host_l = host.strip().lower()
    if scheme_l not in {"http", "https"}:
        raise ValidationError(
            failure(FailureCode.INVALID_URL, diagnostic="unsupported scheme for display")
        )
    if not host_l or len(host_l) > _DISPLAY_HOST_MAX or "/" in host_l or "?" in host_l:
        raise ValidationError(
            failure(FailureCode.INVALID_URL, diagnostic="invalid host for display")
        )
    return f"{scheme_l}://{host_l}"


def archive_name_padding(playlist_total: int | None) -> int:
    """Padding width for zero-padded playlist archive entry names."""
    if playlist_total is None or playlist_total <= 0:
        return 6
    digits = len(str(playlist_total))
    return max(3, digits)
