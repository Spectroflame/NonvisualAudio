"""Run the analysis workflow off the UI thread.

The worker owns only thread lifecycle, cancellation, and wx callback
marshalling.  Synchronous analysis and report orchestration live in
``analysis_workflow`` so they remain testable without a running GUI.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable

import wx

from nonvisualaudio.analysis.memory import MemoryEstimate
from nonvisualaudio.cancellation import Cancellation, CancelledError
from nonvisualaudio.errors import UserFacingError
from nonvisualaudio.reporting.builder import ReportSections
from nonvisualaudio.reporting.templates import ReportDoc
from nonvisualaudio.ui.analysis_workflow import (
    AnalysisCallbacks,
    AnalysisRequest,
    run_analysis,
)

log = logging.getLogger("nonvisualaudio.worker")


class AnalysisWorker:
    """Run one synchronous workflow and marshal its callbacks to wx."""

    def __init__(
        self,
        target_paths: list[str],
        genre_keys: list[str] | None,
        reference_paths: list[str] | None,
        on_done: Callable[[ReportDoc, bool], None],
        on_error: Callable[[UserFacingError], None],
        on_progress: Callable[[int, str], None],
        sections: ReportSections | None = None,
        project_mode: bool = False,
        project_name: str | None = None,
        reference_name: str | None = None,
        on_confirm_memory: Callable[[MemoryEstimate], bool] | None = None,
    ) -> None:
        self._request = AnalysisRequest.create(
            target_paths,
            genre_keys,
            reference_paths,
            sections=sections,
            project_mode=project_mode,
            project_name=project_name,
            reference_name=reference_name,
        )
        self._on_done = on_done
        self._on_error = on_error
        self._on_progress = on_progress
        # The RAM guard asks a question on the UI thread.  Headless callers
        # and tests may omit it, in which case the workflow proceeds.
        self._on_confirm_memory = on_confirm_memory
        self._cancel = Cancellation()
        self._thread = threading.Thread(
            target=self._run_guarded,
            daemon=True,
            name="nva-analysis",
        )

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        """Signal the workflow and any bound subprocess to stop."""
        log.info("cancel requested for running analysis")
        self._cancel.cancel()

    def _emit_progress(self, percent: int, label: str) -> None:
        wx.CallAfter(self._on_progress, percent, label)

    def _emit_done(self, report: ReportDoc, had_failures: bool) -> None:
        wx.CallAfter(self._on_done, report, had_failures)

    def _emit_error(self, err: UserFacingError) -> None:
        wx.CallAfter(self._on_error, err)

    def _confirm_memory(self, estimate: MemoryEstimate) -> bool:
        """Ask the UI thread whether a memory-heavy run may proceed."""
        if self._on_confirm_memory is None:
            return True
        answer: list[bool] = []
        ready = threading.Event()

        def _ask_ui() -> None:
            try:
                answer.append(bool(self._on_confirm_memory(estimate)))
            except Exception:  # noqa: BLE001 -- dialog failure must cancel safely
                log.exception("memory-confirm dialog crashed; defaulting to cancel")
                answer.append(False)
            finally:
                ready.set()

        wx.CallAfter(_ask_ui)
        ready.wait()
        return answer[0] if answer else False

    def _run_guarded(self) -> None:
        """Run the workflow, treating user cancellation as a normal exit."""
        callbacks = AnalysisCallbacks(
            on_done=self._emit_done,
            on_error=self._emit_error,
            on_progress=self._emit_progress,
            confirm_memory=self._confirm_memory,
        )
        try:
            run_analysis(self._request, callbacks, self._cancel)
        except CancelledError:
            log.info("analysis cancelled by user; no result posted")


def start_analysis(
    target_paths: Iterable[str],
    genre_keys: Iterable[str] | None,
    reference_paths: Iterable[str] | None,
    on_done: Callable[[ReportDoc, bool], None],
    on_error: Callable[[UserFacingError], None],
    on_progress: Callable[[int, str], None],
    sections: ReportSections | None = None,
    project_mode: bool = False,
    project_name: str | None = None,
    reference_name: str | None = None,
    on_confirm_memory: Callable[[MemoryEstimate], bool] | None = None,
) -> AnalysisWorker:
    """Start a background analysis and return its cancellation handle."""
    worker = AnalysisWorker(
        list(target_paths),
        list(genre_keys) if genre_keys is not None else None,
        list(reference_paths) if reference_paths is not None else None,
        on_done,
        on_error,
        on_progress,
        sections=sections,
        project_mode=project_mode,
        project_name=project_name,
        reference_name=reference_name,
        on_confirm_memory=on_confirm_memory,
    )
    worker.start()
    return worker
