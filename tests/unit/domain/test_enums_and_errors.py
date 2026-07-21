"""FND-02: enums and safe failure contracts."""

from __future__ import annotations

import json
from enum import StrEnum

import pytest

from ytdlp_bot.domain.enums import (
    AccessMode,
    ArtifactAccessState,
    AudioBitrate,
    CommandName,
    DeletionReason,
    DeliveryPlan,
    FailureCode,
    JobKind,
    JobState,
    LeaseKind,
    MediaMode,
    MediaType,
    Platform,
    PlatformErrorCode,
    PlaylistEntryState,
    UploadOutcome,
    VideoQuality,
    WarningCode,
    WorkerPhase,
)
from ytdlp_bot.domain.errors import (
    DomainError,
    DomainFailure,
    default_retryable,
    failure,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "enum_cls",
    [
        Platform,
        CommandName,
        MediaMode,
        VideoQuality,
        AudioBitrate,
        JobState,
        JobKind,
        ArtifactAccessState,
        DeletionReason,
        MediaType,
        DeliveryPlan,
        UploadOutcome,
        PlatformErrorCode,
        WorkerPhase,
        WarningCode,
        FailureCode,
        PlaylistEntryState,
        AccessMode,
        LeaseKind,
    ],
)
def test_enum_values_are_unique_strings(enum_cls: type[StrEnum]) -> None:
    values = [m.value for m in enum_cls]
    assert values
    assert len(values) == len(set(values))
    assert all(isinstance(v, str) and v for v in values)


@pytest.mark.unit
def test_failure_codes_match_design_set() -> None:
    expected = {
        "INVALID_COMMAND",
        "ACKNOWLEDGEMENT_FAILED",
        "NOT_AUTHORIZED",
        "INVALID_URL",
        "BLOCKED_DESTINATION",
        "UNSUPPORTED_SOURCE",
        "AUTHENTICATION_REQUIRED",
        "DRM_UNSUPPORTED",
        "NO_MATCHING_FORMAT",
        "AUDIO_ONLY_SOURCE",
        "DOWNLOAD_FAILED",
        "POST_PROCESSING_FAILED",
        "PLAYLIST_ALL_FAILED",
        "PLAYLIST_PARTIAL_FAILURE",
        "INSUFFICIENT_CAPACITY",
        "WORKER_PROTOCOL_ERROR",
        "PLATFORM_RATE_LIMITED",
        "PLATFORM_UNAVAILABLE",
        "DELIVERY_UNAVAILABLE",
        "ARTIFACT_MISSING",
        "LINK_UNAVAILABLE",
        "RESTART_INTERRUPTED",
        "INTERNAL_ERROR",
    }
    assert {c.value for c in FailureCode} == expected


@pytest.mark.unit
def test_domain_failure_sanitizes_context_and_serializes() -> None:
    f = failure(
        FailureCode.INVALID_URL,
        safe_context={
            "field": "url",
            "obj": object(),  # dropped
            "long": "x" * 500,
            "n": 3,
        },
        diagnostic="raw " + "d" * 1000,
    )
    assert "obj" not in f.safe_context
    assert len(str(f.safe_context["long"])) <= 128
    assert len(f.diagnostic) <= 512
    payload = f.to_dict()
    json.dumps(payload)  # must be JSON-safe
    assert payload["code"] == "INVALID_URL"
    assert "token=" not in json.dumps(payload)


@pytest.mark.unit
def test_domain_error_exposes_code() -> None:
    err = DomainError(failure(FailureCode.NOT_AUTHORIZED))
    assert err.code is FailureCode.NOT_AUTHORIZED
    assert str(err) == "NOT_AUTHORIZED"


@pytest.mark.unit
def test_default_retryable_classification() -> None:
    assert default_retryable(FailureCode.DOWNLOAD_FAILED) is True
    assert default_retryable(FailureCode.INVALID_URL) is False


@pytest.mark.unit
def test_domain_failure_defaults_locale_key() -> None:
    f = DomainFailure(code=FailureCode.INTERNAL_ERROR, user_message_key="")
    assert f.user_message_key == "failure.internal_error"
