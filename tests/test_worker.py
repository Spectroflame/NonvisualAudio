"""Tests for the thread/wx adapter around the analysis workflow."""

from __future__ import annotations

from nonvisualaudio.cancellation import CancelledError
from nonvisualaudio.reporting.templates import ReportDoc, Section
from nonvisualaudio.ui import worker


def _make_worker(on_done, on_error, on_progress) -> worker.AnalysisWorker:
    return worker.AnalysisWorker(
        ["target.wav"],
        None,
        None,
        on_done,
        on_error,
        on_progress,
    )


def test_worker_marshals_workflow_callbacks_through_call_after(monkeypatch) -> None:
    done: list[tuple[ReportDoc, bool]] = []
    errors: list[object] = []
    progress: list[tuple[int, str]] = []
    call_after_invocations: list[object] = []
    report = ReportDoc(
        sections=(Section(level=1, heading="target.wav", body=()),)
    )

    def immediate_call_after(callback, *args) -> None:
        call_after_invocations.append(callback)
        callback(*args)

    def fake_run(_request, callbacks, _cancel) -> None:
        callbacks.on_progress(50, "Analyzing")
        callbacks.on_done(report, False)

    monkeypatch.setattr(worker.wx, "CallAfter", immediate_call_after)
    monkeypatch.setattr(worker, "run_analysis", fake_run)
    instance = _make_worker(
        lambda result, partial: done.append((result, partial)),
        errors.append,
        lambda percent, label: progress.append((percent, label)),
    )

    instance._run_guarded()

    assert errors == []
    assert progress == [(50, "Analyzing")]
    assert done == [(report, False)]
    assert len(call_after_invocations) == 2


def test_worker_treats_cancellation_as_normal_exit(monkeypatch) -> None:
    done: list[object] = []
    errors: list[object] = []
    progress: list[object] = []

    def cancel_run(_request, _callbacks, _cancel) -> None:
        raise CancelledError()

    monkeypatch.setattr(worker, "run_analysis", cancel_run)
    instance = _make_worker(
        lambda result, partial: done.append((result, partial)),
        errors.append,
        lambda percent, label: progress.append((percent, label)),
    )

    instance._run_guarded()

    assert done == []
    assert errors == []
    assert progress == []
