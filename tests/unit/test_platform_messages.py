"""User-facing zh-TW message rendering for platform adapters."""

from __future__ import annotations

from ytdlp_bot.adapters.platform.messages import (
    render_command_result,
    render_final,
    render_job_accepted,
    render_progress,
    translate_failure,
    translate_state,
)
from ytdlp_bot.domain.commands import (
    AcceptedJob,
    AdminView,
    HelpView,
    StatusView,
    UserError,
)
from ytdlp_bot.domain.enums import FailureCode, JobState
from ytdlp_bot.domain.identity import JobId
from ytdlp_bot.domain.progress import FinalOutcomeView, ProgressView


def test_help_is_human_chinese() -> None:
    text = render_command_result(HelpView())
    assert text is not None
    assert "可用指令" in text
    assert "/ytdl" in text
    assert "help.main" not in text


def test_admin_status_interpolates_fields() -> None:
    text = render_command_result(
        AdminView(
            message_key="admin.status",
            safe_fields={
                "queued": 1,
                "active": 2,
                "used": 100,
                "capacity": 1000,
                "cleanup": "ok",
            },
        )
    )
    assert text is not None
    assert "佇列" in text and "1" in text
    assert "進行中" in text and "2" in text
    assert "admin.status" not in text


def test_status_list_and_empty() -> None:
    empty = render_command_result(StatusView(jobs=(), message_key="status.list_header"))
    assert empty is not None
    assert "沒有可顯示" in empty

    jid = JobId("J" * 22)
    listed = render_command_result(
        StatusView(
            message_key="status.list_header",
            jobs=(
                StatusView(
                    job_id=jid,
                    state=JobState.COMPLETED.value,
                    phase=None,
                    percent=None,
                ),
            ),
        )
    )
    assert listed is not None
    assert "最近工作" in listed
    assert jid.value in listed
    assert "已完成" in listed


def test_accepted_skipped_to_avoid_duplicate() -> None:
    text = render_command_result(AcceptedJob(job_id=JobId("A" * 22), state=JobState.QUEUED.value))
    assert text is None


def test_job_accepted_human() -> None:
    text = render_job_accepted(JobId("B" * 22), JobState.QUEUED)
    assert "已接受下載工作" in text
    assert "排隊中" in text
    assert "accepted job" not in text


def test_progress_and_final() -> None:
    jid = JobId("C" * 22)
    progress = render_progress(
        ProgressView(
            job_id=jid,
            state="downloading",
            phase="downloading",
            percent=42,
            playlist_completed=None,
            playlist_total=None,
            current_entry_title=None,
            warning_codes=(),
        )
    )
    assert "下載中" in progress
    assert "42%" in progress

    final_ok = render_final(
        FinalOutcomeView(
            job_id=jid,
            outcome="completed",
            message_key="outcome.completed",
            download_url="https://example.invalid/file",
            has_signed_link_hint=True,
        )
    )
    assert "下載完成" in final_ok
    assert "https://example.invalid/file" in final_ok
    assert "簽章連結" in final_ok or "上傳上限" in final_ok

    final_fail = render_final(
        FinalOutcomeView(
            job_id=jid,
            outcome="failed",
            message_key="outcome.failed",
            error_code=FailureCode.DOWNLOAD_FAILED,
        )
    )
    assert "失敗" in final_fail
    assert "媒體傳輸失敗" in final_fail


def test_user_error_and_helpers() -> None:
    text = render_command_result(
        UserError(code=FailureCode.NOT_AUTHORIZED, message_key="failure.not_authorized")
    )
    assert text is not None
    assert "沒有權限" in text
    assert translate_state("queued") == "排隊中"
    assert "純音訊" in translate_failure(FailureCode.AUDIO_ONLY_SOURCE)
