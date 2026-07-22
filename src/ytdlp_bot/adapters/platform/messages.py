"""Platform-neutral user-facing message rendering (zh-TW)."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache

from ytdlp_bot.domain.commands import (
    AcceptedJob,
    AdminView,
    CommandResult,
    HelpView,
    StatusView,
    UserError,
)
from ytdlp_bot.domain.enums import FailureCode, JobState
from ytdlp_bot.domain.identity import JobId
from ytdlp_bot.domain.locale import format_message, load_zh_tw_catalog
from ytdlp_bot.domain.progress import FinalOutcomeView, ProgressView

# Job state value -> locale key
_STATE_KEYS: Mapping[str, str] = {
    JobState.QUEUED.value: "job.state.queued",
    JobState.INSPECTING.value: "job.state.inspecting",
    JobState.DOWNLOADING.value: "job.state.downloading",
    JobState.POST_PROCESSING.value: "job.state.post_processing",
    JobState.ARCHIVING.value: "job.state.archiving",
    JobState.DELIVERING.value: "job.state.delivering",
    JobState.COMPLETED.value: "job.state.completed",
    JobState.COMPLETED_WITH_ERRORS.value: "job.state.completed_with_errors",
    JobState.FAILED.value: "job.state.failed",
    JobState.CANCELLED.value: "job.state.cancelled",
    JobState.CANCELLED_BY_RESTART.value: "job.state.cancelled_by_restart",
    JobState.CANCELLING.value: "job.state.cancelling",
    JobState.EXPIRED.value: "job.state.expired",
    JobState.EVICTED.value: "job.state.evicted",
}


@lru_cache(maxsize=1)
def _catalog() -> dict[str, str]:
    return load_zh_tw_catalog()


def _label(catalog: Mapping[str, str], key: str, fallback: str | None = None) -> str:
    return catalog.get(
        key, fallback if fallback is not None else catalog.get("value.unknown", "未知")
    )


def translate_state(state: str | None, catalog: Mapping[str, str] | None = None) -> str:
    """Translate a job state value into zh-TW."""
    cat = catalog or _catalog()
    if not state:
        return _label(cat, "value.unknown")
    key = _STATE_KEYS.get(state)
    if key:
        return _label(cat, key, state)
    return state


def translate_phase(phase: str | None, catalog: Mapping[str, str] | None = None) -> str:
    """Translate a worker/progress phase into zh-TW."""
    cat = catalog or _catalog()
    if not phase:
        return _label(cat, "progress.phase.unknown")
    return _label(cat, f"progress.phase.{phase}", phase)


def translate_failure(
    code: FailureCode | str | None, catalog: Mapping[str, str] | None = None
) -> str:
    """Map FailureCode (or raw code string) to a user-facing failure sentence."""
    cat = catalog or _catalog()
    if code is None:
        return _label(cat, "failure.internal_error")
    raw = code.value if isinstance(code, FailureCode) else str(code)
    key = f"failure.{raw.lower()}"
    return _label(cat, key, _label(cat, "failure.internal_error"))


def render_job_accepted(job_id: JobId | str, state: str | JobState) -> str:
    """Human-readable job acknowledgement."""
    catalog = _catalog()
    state_val = state.value if isinstance(state, JobState) else state
    jid = job_id.value if isinstance(job_id, JobId) else job_id
    return format_message(
        catalog,
        "job.accepted",
        job_id=jid,
        state=translate_state(state_val, catalog),
    )


def render_progress(view: ProgressView) -> str:
    """Human-readable progress edit text."""
    catalog = _catalog()
    percent: str
    if view.percent is None:
        percent = _label(catalog, "progress.percent.unknown")
    else:
        percent = f"{view.percent}%"
    playlist_line = ""
    if view.playlist_total is not None:
        done = view.playlist_completed if view.playlist_completed is not None else 0
        playlist_line = format_message(
            catalog,
            "progress.playlist_line",
            completed=done,
            total=view.playlist_total,
        )
        if view.current_entry_title:
            playlist_line = f"{playlist_line}\n{view.current_entry_title}"
    text = format_message(
        catalog,
        view.message_key or "progress.update",
        job_id=view.job_id.value,
        state=translate_state(view.state, catalog),
        phase=translate_phase(view.phase, catalog),
        percent=percent,
        playlist_line=playlist_line,
    )
    return text.rstrip()


def render_final(view: FinalOutcomeView) -> str:
    """Human-readable final outcome (may include ephemeral download URL)."""
    catalog = _catalog()
    if view.outcome == "failed":
        error = translate_failure(view.error_code, catalog)
        text = format_message(catalog, "outcome.failed", error=error)
    else:
        text = format_message(catalog, view.message_key)
    if view.job_id is not None:
        text = f"{text}\n{format_message(catalog, 'job.id_line', job_id=view.job_id.value)}"
    for code in view.warning_codes:
        warn_key = f"warning.{code.value.lower()}"
        if warn_key in catalog:
            text = f"{text}\n{_label(catalog, warn_key)}"
    if view.has_signed_link_hint and "warning.delivery_fallback_link" in catalog:
        # Append link-delivery hint if not already included via warning_codes.
        hint = _label(catalog, "warning.delivery_fallback_link")
        if hint not in text:
            text = f"{text}\n{hint}"
    if view.download_url:
        text = f"{text}\n{view.download_url}"
    return text


def render_command_result(
    result: CommandResult,
    *,
    recent_limit: int = 10,
) -> str | None:
    """Render a CommandResult for chat.

    Returns None when no additional message should be sent (e.g. AcceptedJob
    already announced via acknowledge_job).
    """
    catalog = _catalog()

    if isinstance(result, AcceptedJob):
        # acknowledge_job already sent the acceptance message.
        return None

    if isinstance(result, HelpView):
        return format_message(
            catalog,
            result.message_key,
            legal=_label(catalog, "legal.use_reminder"),
        )

    if isinstance(result, UserError):
        return format_message(catalog, result.message_key)

    if isinstance(result, AdminView):
        fields = dict(result.safe_fields or {})
        return format_message(catalog, result.message_key, **fields)

    if isinstance(result, StatusView):
        # List view: no single job_id, message_key is status.list_header.
        if result.job_id is None and result.message_key == "status.list_header":
            if not result.jobs:
                return _label(catalog, "status.empty")
            lines = [
                format_message(catalog, "status.list_header", limit=recent_limit),
            ]
            for item in result.jobs:
                jid = item.job_id.value if item.job_id else _label(catalog, "value.unknown")
                st = translate_state(item.state, catalog)
                phase = translate_phase(item.phase, catalog) if item.phase else ""
                pct = f"{item.percent}%" if item.percent is not None else ""
                extra = " ".join(x for x in (phase, pct) if x)
                if extra:
                    lines.append(
                        format_message(
                            catalog,
                            "status.list_item_detail",
                            job_id=jid,
                            state=st,
                            detail=extra,
                        )
                    )
                else:
                    lines.append(
                        format_message(
                            catalog,
                            "status.list_item",
                            job_id=jid,
                            state=st,
                        )
                    )
            return "\n".join(lines)

        # Single-job outcome (including cancel -> outcome.cancelled).
        if result.message_key.startswith("outcome."):
            body = format_message(catalog, result.message_key)
            if result.job_id is not None:
                return (
                    f"{body}\n{format_message(catalog, 'job.id_line', job_id=result.job_id.value)}"
                )
            return body

        if result.message_key == "status.renewed":
            body = format_message(catalog, "status.renewed")
            if result.renew_url:
                body = f"{body}\n{result.renew_url}"
            if result.job_id is not None:
                body = (
                    f"{body}\n{format_message(catalog, 'job.id_line', job_id=result.job_id.value)}"
                )
            return body

        percent = (
            f"{result.percent}%"
            if result.percent is not None
            else _label(catalog, "progress.percent.unknown")
        )
        return format_message(
            catalog,
            result.message_key or "status.view",
            job_id=result.job_id.value if result.job_id else _label(catalog, "value.unknown"),
            state=translate_state(result.state, catalog),
            phase=translate_phase(result.phase, catalog),
            percent=percent,
        )

    return _label(catalog, "value.unknown")
