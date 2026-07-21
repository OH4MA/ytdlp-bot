"""Stable domain enumerations.

All values are part of the public contract surface for locale keys, persistence,
and platform-neutral command handling.
"""

from __future__ import annotations

from enum import StrEnum


class Platform(StrEnum):
    """Supported chat platforms."""

    TELEGRAM = "telegram"
    DISCORD = "discord"


class CommandName(StrEnum):
    """Canonical command names after adapter normalization."""

    YTDL = "ytdl"
    YTMP3 = "ytmp3"
    YTDL_STATUS = "ytdl_status"
    YTDL_CANCEL = "ytdl_cancel"
    YTDL_HELP = "ytdl_help"
    YTDL_ADMIN = "ytdl_admin"


class MediaMode(StrEnum):
    """Requested media processing mode."""

    VIDEO = "video"
    AUDIO = "audio"


class VideoQuality(StrEnum):
    """Video resolution ceilings (inclusive)."""

    BEST = "best"
    P2160 = "2160p"
    P1440 = "1440p"
    P1080 = "1080p"
    P720 = "720p"
    P480 = "480p"
    P360 = "360p"


class AudioBitrate(StrEnum):
    """Accepted MP3 bitrates."""

    K128 = "128k"
    K192 = "192k"
    K256 = "256k"
    K320 = "320k"


class JobState(StrEnum):
    """Authoritative job lifecycle states."""

    QUEUED = "queued"
    INSPECTING = "inspecting"
    DOWNLOADING = "downloading"
    POST_PROCESSING = "post_processing"
    ARCHIVING = "archiving"
    DELIVERING = "delivering"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CANCELLED_BY_RESTART = "cancelled_by_restart"
    EXPIRED = "expired"
    EVICTED = "evicted"


class JobKind(StrEnum):
    """Discovered job media shape."""

    UNKNOWN = "unknown"
    SINGLE = "single"
    PLAYLIST = "playlist"


class ArtifactAccessState(StrEnum):
    """Artifact availability for delivery and download."""

    AVAILABLE = "available"
    DELETION_PENDING = "deletion_pending"
    DELETED = "deleted"


class DeletionReason(StrEnum):
    """Why an artifact entered deletion."""

    EXPIRED = "expired"
    EVICTED = "evicted"
    ADMINISTRATOR = "administrator"
    JOB_CANCELLED = "job_cancelled"
    RECONCILIATION = "reconciliation"


class MediaType(StrEnum):
    """MIME type of a published artifact."""

    VIDEO_MP4 = "video/mp4"
    AUDIO_MPEG = "audio/mpeg"
    APPLICATION_ZIP = "application/zip"


class DeliveryPlan(StrEnum):
    """Chosen delivery strategy for a ready artifact."""

    DIRECT_UPLOAD = "direct_upload"
    SIGNED_LINK = "signed_link"


class UploadOutcome(StrEnum):
    """Result of a platform upload attempt."""

    UPLOADED = "uploaded"
    TOO_LARGE = "too_large"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    RATE_LIMITED = "rate_limited"
    FORBIDDEN = "forbidden"
    FAILED = "failed"


class PlatformErrorCode(StrEnum):
    """Normalized platform adapter failures."""

    RATE_LIMITED = "rate_limited"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_FAILED = "authentication_failed"
    UNKNOWN = "unknown"


class WorkerPhase(StrEnum):
    """Worker progress phase reported to the controller."""

    INSPECTING = "inspecting"
    DOWNLOADING = "downloading"
    POST_PROCESSING = "post_processing"
    ARCHIVING = "archiving"
    FINALIZING = "finalizing"


class WarningCode(StrEnum):
    """User-safe warning codes attached to a job."""

    NO_AUDIO = "no_audio"
    FORMAT_FALLBACK = "format_fallback"
    PLAYLIST_PARTIAL = "playlist_partial"
    DELIVERY_FALLBACK_LINK = "delivery_fallback_link"
    PROTOCOL_MALFORMED_PROGRESS = "protocol_malformed_progress"


class FailureCode(StrEnum):
    """Stable machine-readable failure codes."""

    INVALID_COMMAND = "INVALID_COMMAND"
    ACKNOWLEDGEMENT_FAILED = "ACKNOWLEDGEMENT_FAILED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    INVALID_URL = "INVALID_URL"
    BLOCKED_DESTINATION = "BLOCKED_DESTINATION"
    UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    DRM_UNSUPPORTED = "DRM_UNSUPPORTED"
    NO_MATCHING_FORMAT = "NO_MATCHING_FORMAT"
    AUDIO_ONLY_SOURCE = "AUDIO_ONLY_SOURCE"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    POST_PROCESSING_FAILED = "POST_PROCESSING_FAILED"
    PLAYLIST_ALL_FAILED = "PLAYLIST_ALL_FAILED"
    PLAYLIST_PARTIAL_FAILURE = "PLAYLIST_PARTIAL_FAILURE"
    INSUFFICIENT_CAPACITY = "INSUFFICIENT_CAPACITY"
    WORKER_PROTOCOL_ERROR = "WORKER_PROTOCOL_ERROR"
    PLATFORM_RATE_LIMITED = "PLATFORM_RATE_LIMITED"
    PLATFORM_UNAVAILABLE = "PLATFORM_UNAVAILABLE"
    DELIVERY_UNAVAILABLE = "DELIVERY_UNAVAILABLE"
    ARTIFACT_MISSING = "ARTIFACT_MISSING"
    LINK_UNAVAILABLE = "LINK_UNAVAILABLE"
    RESTART_INTERRUPTED = "RESTART_INTERRUPTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class PlaylistEntryState(StrEnum):
    """Lifecycle of one playlist entry inside a parent job."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    POST_PROCESSING = "post_processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AccessMode(StrEnum):
    """Runtime access control mode."""

    ALLOW_ALL = "allow_all"
    WHITELIST = "whitelist"


class LeaseKind(StrEnum):
    """In-memory artifact access lease kinds."""

    HTTP_STREAM = "http_stream"
    PLATFORM_UPLOAD = "platform_upload"


# Active media-processing states (restart and cancellation semantics).
ACTIVE_JOB_STATES: frozenset[JobState] = frozenset(
    {
        JobState.INSPECTING,
        JobState.DOWNLOADING,
        JobState.POST_PROCESSING,
        JobState.ARCHIVING,
        JobState.DELIVERING,
        JobState.CANCELLING,
    }
)

# States that may still reference a published artifact before physical deletion.
ARTIFACT_BEARING_JOB_STATES: frozenset[JobState] = frozenset(
    {
        JobState.COMPLETED,
        JobState.COMPLETED_WITH_ERRORS,
        JobState.FAILED,
    }
)

# Fully terminal job states with no further product transitions.
TERMINAL_JOB_STATES: frozenset[JobState] = frozenset(
    {
        JobState.CANCELLED,
        JobState.CANCELLED_BY_RESTART,
        JobState.EXPIRED,
        JobState.EVICTED,
    }
)

USER_CANCELLABLE_STATES: frozenset[JobState] = frozenset(
    {
        JobState.QUEUED,
        JobState.INSPECTING,
        JobState.DOWNLOADING,
        JobState.POST_PROCESSING,
        JobState.ARCHIVING,
        JobState.DELIVERING,
    }
)
