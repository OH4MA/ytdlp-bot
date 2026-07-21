"""FND-05 / FND-06: models and progress/delivery value objects."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from ytdlp_bot.domain.enums import (
    DeliveryPlan,
    JobState,
    MediaMode,
    MediaType,
    Platform,
    PlaylistEntryState,
    VideoQuality,
    WorkerPhase,
)
from ytdlp_bot.domain.errors import ValidationError
from ytdlp_bot.domain.identity import ArtifactId, Identity, JobId, MessageContext
from ytdlp_bot.domain.jobs import (
    Artifact,
    DownloadRequest,
    Job,
    PlaylistEntry,
    archive_name_padding,
    sanitize_source_display,
)
from ytdlp_bot.domain.progress import (
    DeliveryPlanDecision,
    ProgressSnapshot,
    progress_from_worker_values,
)


def _job() -> Job:
    return Job(
        job_id=JobId("J" * 22),
        idempotency_key="telegram:1",
        owner=Identity(platform=Platform.TELEGRAM, user_id="1"),
        message_context=MessageContext(
            platform=Platform.TELEGRAM,
            chat_id="1",
            response_target="1",
            effective_upload_limit_bytes=10,
        ),
        request_mode=MediaMode.VIDEO,
        selected_preset="best",
        source_display="https://example.com",
        state=JobState.QUEUED,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.unit
def test_download_request_invariants() -> None:
    DownloadRequest(
        source_url="https://ex.test/a",
        mode=MediaMode.VIDEO,
        video_quality=VideoQuality.BEST,
    )
    with pytest.raises(ValidationError):
        DownloadRequest(
            source_url="https://ex.test/a",
            mode=MediaMode.VIDEO,
            video_quality=VideoQuality.BEST,
            audio_bitrate=None,
        )
        DownloadRequest(
            source_url="https://ex.test/a",
            mode=MediaMode.VIDEO,
            video_quality=VideoQuality.BEST,
            audio_bitrate=__import__(
                "ytdlp_bot.domain.enums", fromlist=["AudioBitrate"]
            ).AudioBitrate.K128,
        )


@pytest.mark.unit
def test_job_serialization_has_no_source_url() -> None:
    job = _job()
    blob = json.dumps(job.to_dict())
    assert "source_url" not in blob
    assert "example.com" in blob


@pytest.mark.unit
def test_artifact_invariants() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    art = Artifact(
        artifact_id=ArtifactId("A" * 22),
        job_id=JobId("J" * 22),
        storage_key="S" * 22,
        display_name="clip.mp4",
        media_type=MediaType.VIDEO_MP4,
        byte_size=100,
        ready_at=now,
        expires_at=now + timedelta(hours=12),
    )
    assert "storage_key" in art.to_dict()
    with pytest.raises(ValidationError):
        Artifact(
            artifact_id=ArtifactId("A" * 22),
            job_id=JobId("J" * 22),
            storage_key="bad/key/with/slashes!!!!",
            display_name="clip.mp4",
            media_type=MediaType.VIDEO_MP4,
            byte_size=100,
            ready_at=now,
            expires_at=now + timedelta(hours=1),
        )


@pytest.mark.unit
def test_playlist_entry_and_padding() -> None:
    entry = PlaylistEntry(
        job_id=JobId("J" * 22),
        playlist_index=1,
        extractor_source_id="abc",
        sanitized_title="t",
        state=PlaylistEntryState.PENDING,
    )
    assert entry.playlist_index == 1
    assert archive_name_padding(None) == 6
    assert archive_name_padding(12) == 3
    assert archive_name_padding(1234) == 4


@pytest.mark.unit
def test_sanitize_source_display() -> None:
    assert sanitize_source_display("HTTPS", "Example.COM") == "https://example.com"
    with pytest.raises(ValidationError):
        sanitize_source_display("https", "evil.com/path")


@pytest.mark.unit
def test_progress_percent_unknown_and_zero() -> None:
    snap = ProgressSnapshot(downloaded_bytes=50, total_bytes=None)
    assert snap.percent is None
    snap0 = ProgressSnapshot(downloaded_bytes=50, total_bytes=0)
    assert snap0.percent is None
    snap2 = ProgressSnapshot(downloaded_bytes=50, total_bytes=100)
    assert snap2.percent == 50
    snap3 = ProgressSnapshot(downloaded_bytes=200, total_bytes=100)
    assert snap3.percent == 100


@pytest.mark.unit
def test_malformed_worker_progress_flagged() -> None:
    snap = progress_from_worker_values(
        phase=WorkerPhase.DOWNLOADING,
        downloaded_bytes=-5,
        total_bytes=100,
        speed_bytes_per_second=None,
        eta_seconds=None,
        playlist_completed=None,
        playlist_total=None,
        current_entry_index=None,
        current_entry_title=None,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_sequence=1,
    )
    assert snap.malformed is True
    assert snap.downloaded_bytes is None


@pytest.mark.unit
def test_delivery_plan_boundary() -> None:
    assert DeliveryPlanDecision.decide(10, 10).plan is DeliveryPlan.DIRECT_UPLOAD
    assert DeliveryPlanDecision.decide(11, 10).plan is DeliveryPlan.SIGNED_LINK
