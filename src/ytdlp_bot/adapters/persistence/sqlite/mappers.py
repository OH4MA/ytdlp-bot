"""Map between SQLite rows and domain models."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ytdlp_bot.domain.enums import (
    ArtifactAccessState,
    DeletionReason,
    FailureCode,
    JobKind,
    JobState,
    MediaMode,
    MediaType,
    Platform,
    WarningCode,
    WorkerPhase,
)
from ytdlp_bot.domain.identity import (
    ArtifactId,
    Identity,
    JobId,
    MessageContext,
    MessageReference,
)
from ytdlp_bot.domain.jobs import Artifact, Job, WorkerLease
from ytdlp_bot.domain.progress import ProgressSnapshot


def ms_to_dt(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000.0, tz=UTC)


def dt_to_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.timestamp() * 1000)


def job_from_row(row: Any) -> Job:
    ctx = json.loads(row["context_json"])
    message_context = MessageContext(
        platform=Platform(ctx["platform"]),
        chat_id=str(ctx["chat_id"]),
        response_target=str(ctx["response_target"]),
        effective_upload_limit_bytes=int(ctx["effective_upload_limit_bytes"]),
    )
    message_reference = None
    if ctx.get("message_id"):
        message_reference = MessageReference(
            platform=Platform(ctx["platform"]),
            chat_id=str(ctx["chat_id"]),
            message_id=str(ctx["message_id"]),
        )
    progress = None
    if row["progress_json"]:
        p = json.loads(row["progress_json"])
        phase = WorkerPhase(p["phase"]) if p.get("phase") else None
        progress = ProgressSnapshot(
            phase=phase,
            downloaded_bytes=p.get("downloaded_bytes"),
            total_bytes=p.get("total_bytes"),
            speed_bytes_per_second=p.get("speed_bytes_per_second"),
            eta_seconds=p.get("eta_seconds"),
            playlist_completed=p.get("playlist_completed"),
            playlist_total=p.get("playlist_total"),
            current_entry_index=p.get("current_entry_index"),
            current_entry_title=p.get("current_entry_title"),
            updated_at=ms_to_dt(p.get("updated_at_ms")),
            source_sequence=int(p.get("source_sequence", 0)),
            malformed=bool(p.get("malformed", False)),
        )
    warnings_raw = json.loads(row["warning_codes_json"] or "[]")
    warning_codes = tuple(WarningCode(w) for w in warnings_raw)
    worker_lease = None
    if row["controller_instance_id"] and row["worker_lease_expires_at"]:
        worker_lease = WorkerLease(
            controller_id=str(row["controller_instance_id"]),
            heartbeat_at=ms_to_dt(row["worker_lease_expires_at"]) or datetime.now(UTC),
        )
    return Job(
        job_id=JobId(str(row["job_id"])),
        idempotency_key=str(ctx.get("idempotency_key", f"{row['owner_platform']}:{row['job_id']}")),
        owner=Identity(
            platform=Platform(row["owner_platform"]),
            user_id=str(row["owner_user_id"]),
        ),
        message_context=message_context,
        request_mode=MediaMode(row["request_mode"]),
        selected_preset=str(row["selected_preset"]),
        source_display=str(row["source_display"]),
        state=JobState(row["state"]),
        kind=JobKind(row["media_kind"]),
        progress=progress,
        warning_codes=warning_codes,
        error_code=FailureCode(row["error_code"]) if row["error_code"] else None,
        cancellation_requested=bool(row["cancellation_requested"]),
        dispatchable=bool(row["dispatchable"]),
        message_reference=message_reference,
        acknowledged_at=ms_to_dt(row["acknowledged_at"]),
        worker_lease=worker_lease,
        version=int(row["version"]),
        created_at=ms_to_dt(row["created_at"]),
        started_at=ms_to_dt(row["started_at"]),
        updated_at=ms_to_dt(row["updated_at"]),
        ready_at=ms_to_dt(row["ready_at"]),
        terminal_at=ms_to_dt(row["terminal_at"]),
        last_event_sequence=int(row["last_worker_sequence"]),
    )


def job_to_context_json(job: Job) -> str:
    payload: dict[str, object] = {
        "platform": job.message_context.platform.value,
        "chat_id": job.message_context.chat_id,
        "response_target": job.message_context.response_target,
        "effective_upload_limit_bytes": job.message_context.effective_upload_limit_bytes,
        "idempotency_key": job.idempotency_key,
    }
    if job.message_reference is not None:
        payload["message_id"] = job.message_reference.message_id
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def progress_to_json(progress: ProgressSnapshot | None) -> str | None:
    if progress is None:
        return None
    return json.dumps(
        {
            "phase": progress.phase.value if progress.phase else None,
            "downloaded_bytes": progress.downloaded_bytes,
            "total_bytes": progress.total_bytes,
            "speed_bytes_per_second": progress.speed_bytes_per_second,
            "eta_seconds": progress.eta_seconds,
            "playlist_completed": progress.playlist_completed,
            "playlist_total": progress.playlist_total,
            "current_entry_index": progress.current_entry_index,
            "current_entry_title": progress.current_entry_title,
            "updated_at_ms": dt_to_ms(progress.updated_at),
            "source_sequence": progress.source_sequence,
            "malformed": progress.malformed,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def artifact_from_row(row: Any) -> Artifact:
    return Artifact(
        artifact_id=ArtifactId(str(row["artifact_id"])),
        job_id=JobId(str(row["job_id"])),
        storage_key=str(row["storage_key"]),
        display_name=str(row["display_name"]),
        media_type=MediaType(row["media_type"]),
        byte_size=int(row["byte_size"]),
        ready_at=ms_to_dt(row["ready_at"]) or datetime.now(UTC),
        expires_at=ms_to_dt(row["expires_at"]) or datetime.now(UTC),
        access_state=ArtifactAccessState(row["access_state"]),
        deletion_reason=(
            DeletionReason(row["deletion_reason"]) if row["deletion_reason"] else None
        ),
        token_version=int(row["token_version"]),
        deletion_retry_count=int(row["deletion_attempts"]),
        deletion_next_attempt_at=ms_to_dt(row["next_deletion_attempt_at"]),
        deletion_last_error=row["last_deletion_error"],
        version=int(row["version"]),
    )
