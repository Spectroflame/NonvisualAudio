"""Run audio analysis on a background thread so the UI stays responsive."""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

log = logging.getLogger("nonvisualaudio.worker")

from nonvisualaudio.analysis.pipeline import analyze
from nonvisualaudio.analysis.result import AnalysisResult
from nonvisualaudio.reporting.builder import build_report
from nonvisualaudio.reporting.comparison import (
    build_genre_comparison,
    build_reference_comparison,
)
from nonvisualaudio.reporting.genre_profiles import GENRES


def _file_section_header(index: int, total: int, filename: str) -> str:
    """Plain-text header between per-file report sections.

    Screen readers read "equals equals equals" letter-by-letter, so we
    avoid any punctuation banners — just a clear heading line in the
    same style as the other section headings.
    """
    return f"FILE {index} OF {total}: {filename}"


class AnalysisWorker(QObject):
    finished = Signal(str)        # report text
    failed = Signal(str)          # error message
    progress = Signal(int, str)   # percent 0-100, stage label

    def __init__(
        self,
        target_paths: list[str],
        genre_keys: list[str] | None = None,
        reference_path: str | None = None,
    ) -> None:
        super().__init__()
        self._targets = list(target_paths)
        self._genre_keys = list(genre_keys or [])
        self._reference = reference_path

    def _emit_progress(self, percent: int, label: str) -> None:
        self.progress.emit(percent, label)

    def run(self) -> None:
        t0 = time.time()
        n_targets = len(self._targets)
        log.info(
            "worker.run start targets=%d genres=%s reference=%s",
            n_targets,
            self._genre_keys,
            self._reference,
        )
        try:
            if n_targets == 0:
                raise ValueError("No files selected for analysis.")

            # Budget: reserve 0..10% for the reference (if any) and the
            # remaining 10..90% for the targets. 90..100% is reserved for
            # report building.
            has_reference = bool(self._reference)
            reference: AnalysisResult | None = None
            targets_start = 0
            if has_reference:
                reference = analyze(
                    self._reference,
                    progress_cb=self._emit_progress,
                    percent_start=0,
                    percent_end=15,
                    label_prefix="Reference",
                )
                log.info(
                    "reference analyzed: I=%.1f LUFS crest=%.1f dB",
                    reference.loudness.integrated_lufs,
                    reference.dynamics.crest_factor_db,
                )
                targets_start = 15

            # Divide the remaining 10..90% among the target files.
            targets_end = 90
            per_file = max(1, (targets_end - targets_start) // n_targets)

            report_parts: list[str] = []
            for i, target_path in enumerate(self._targets):
                slice_start = targets_start + per_file * i
                slice_end = targets_end if i == n_targets - 1 else slice_start + per_file
                prefix = (
                    f"File {i + 1} of {n_targets}"
                    if n_targets > 1
                    else ""
                )
                result = analyze(
                    target_path,
                    progress_cb=self._emit_progress,
                    percent_start=slice_start,
                    percent_end=slice_end,
                    label_prefix=prefix,
                )
                log.info(
                    "target %d/%d analyzed: %s I=%.1f LUFS crest=%.1f dB",
                    i + 1,
                    n_targets,
                    Path(target_path).name,
                    result.loudness.integrated_lufs,
                    result.dynamics.crest_factor_db,
                )
                # Build report section for this file.
                extras: list[str] = []
                for key in self._genre_keys:
                    profile = GENRES.get(key)
                    if profile:
                        extras.append(build_genre_comparison(result, profile))
                if reference is not None:
                    extras.append(build_reference_comparison(result, reference))
                report_parts.append(build_report(result, extra_sections=extras))

            self.progress.emit(92, "Assembling report")
            if n_targets == 1:
                full_report = report_parts[0]
            else:
                chunks: list[str] = []
                for i, (path, part) in enumerate(
                    zip(self._targets, report_parts, strict=True), start=1
                ):
                    chunks.append(
                        _file_section_header(i, n_targets, Path(path).name)
                    )
                    chunks.append(part)
                full_report = "\n\n".join(chunks)

            log.info(
                "report ready, length=%d chars, total=%.2fs",
                len(full_report),
                time.time() - t0,
            )
            self.progress.emit(100, "Done")
            self.finished.emit(full_report)
        except Exception as exc:  # noqa: BLE001 — report all errors to UI
            tb = traceback.format_exc()
            log.exception("analysis failed: %s", exc)
            self.failed.emit(f"{exc}\n\n{tb}")


def start_analysis(
    parent: QObject,
    target_paths: Iterable[str],
    genre_keys: Iterable[str] | None,
    reference_path: str | None,
    on_done,
    on_error,
    on_progress=None,
) -> QThread:
    """Spin up a thread running AnalysisWorker. Returns the QThread.

    The caller is responsible for keeping a reference to the returned thread
    until it emits ``finished``.
    """
    thread = QThread(parent)
    worker = AnalysisWorker(
        list(target_paths),
        list(genre_keys) if genre_keys is not None else None,
        reference_path,
    )
    # Pin the worker to the thread so the Python reference outlives this
    # function — otherwise the worker would be garbage collected before the
    # QThread's event loop fires ``started`` and invokes ``run``.
    thread._nva_worker = worker  # type: ignore[attr-defined]
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(on_done)
    worker.failed.connect(on_error)
    if on_progress is not None:
        worker.progress.connect(on_progress)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread
