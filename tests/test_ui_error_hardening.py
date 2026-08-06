"""Regression tests for keeping technical exception text out of the UI."""

from __future__ import annotations

from pathlib import Path

from nonvisualaudio.app import _unexpected_main_loop_error
from nonvisualaudio.audio.decoder import (
    _ffmpeg_error_to_user_error,
    _validate_file,
)
from nonvisualaudio.audio.ffmpeg_runner import FFmpegError
from nonvisualaudio.ui import analysis_workflow
from nonvisualaudio.ui.results_dialog import _export_failed_error


_TECHNICAL_SECRET = "RuntimeError: token=do-not-display /private/secret.wav"


def _assert_technical_text_is_hidden(message: str) -> None:
    assert _TECHNICAL_SECRET not in message
    assert "do-not-display" not in message
    assert "/private/secret.wav" not in message


def test_unexpected_file_analysis_error_hides_exception_text(monkeypatch) -> None:
    context = analysis_workflow._RunContext(
        request=analysis_workflow.AnalysisRequest.create(
            ["target.wav"], None, None
        ),
        callbacks=analysis_workflow.AnalysisCallbacks(
            on_done=lambda *_args: None,
            on_error=lambda _error: None,
            on_progress=lambda *_args: None,
            confirm_memory=lambda _estimate: True,
        ),
        cancel=analysis_workflow.Cancellation(),
    )
    monkeypatch.setattr(
        analysis_workflow,
        "analyze",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(_TECHNICAL_SECRET)
        ),
    )

    error = analysis_workflow._analyze_target(
        context,
        "target.wav",
        index=0,
        target_count=1,
        percent_start=0,
        percent_end=90,
        label_prefix="",
    )

    assert isinstance(error, analysis_workflow.UserFacingError)
    _assert_technical_text_is_hidden(error.as_message())


def test_report_builder_error_hides_exception_text(monkeypatch) -> None:
    request = analysis_workflow.AnalysisRequest.create(["target.wav"], None, None)
    monkeypatch.setattr(
        analysis_workflow,
        "_build_target_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(_TECHNICAL_SECRET)
        ),
    )

    error = analysis_workflow._render_target_report(
        request,
        analysis_workflow.AnalysisResult,
        None,
        target_path="target.wav",
        filename="target.wav",
        index=0,
        target_count=1,
    )

    assert isinstance(error, analysis_workflow.UserFacingError)
    _assert_technical_text_is_hidden(error.as_message())


def test_ffmpeg_decode_error_hides_stderr_text() -> None:
    error = _ffmpeg_error_to_user_error(
        FFmpegError(f"exit_1:\n{_TECHNICAL_SECRET}"),
        Path("target.wav"),
    )

    _assert_technical_text_is_hidden(error.as_message())


def test_unreadable_file_error_hides_oserror_text(monkeypatch) -> None:
    monkeypatch.setattr(Path, "exists", lambda _path: True)
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    monkeypatch.setattr(
        Path,
        "stat",
        lambda _path: (_ for _ in ()).throw(OSError(_TECHNICAL_SECRET)),
    )

    try:
        _validate_file("target.wav")
    except analysis_workflow.UserFacingError as error:
        _assert_technical_text_is_hidden(error.as_message())
    else:
        raise AssertionError("_validate_file() did not report the stat failure")


def test_main_loop_fallback_has_no_exception_placeholder() -> None:
    error = _unexpected_main_loop_error()

    assert "{detail}" not in error.as_message()
    assert "Technical detail" not in error.as_message()


def test_export_failure_uses_only_friendly_text() -> None:
    error = _export_failed_error("report.txt")

    assert "report.txt" in error.as_message()
    assert "{details}" not in error.as_message()
    assert "file system reported" not in error.as_message().lower()
