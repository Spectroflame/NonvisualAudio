"""Run audio analysis on a background thread so the UI stays responsive.

The worker runs each file in isolation: a single corrupt file never
kills the whole batch. Per-file failures are collected and injected
back into the report as an error section at the top, so the user sees
what worked and what did not in one place.

Truly fatal conditions that apply to the whole run — most notably a
missing ffmpeg — abort the run with a separate, actionable message.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable
from pathlib import Path

import wx

from nonvisualaudio.analysis.pipeline import analyze
from nonvisualaudio.analysis.result import AnalysisResult
from nonvisualaudio.errors import (
    AudioDecodeError,
    LoudnessMeasurementError,
    MissingFFmpegError,
    UserFacingError,
)
from nonvisualaudio.reporting.builder import build_report
from nonvisualaudio.reporting.comparison import (
    build_genre_comparison,
    build_reference_comparison,
)
from nonvisualaudio.reporting.genre_profiles import GENRES

log = logging.getLogger("nonvisualaudio.worker")


def _file_section_header(index: int, total: int, filename: str) -> str:
    """Plain-text header between per-file report sections.

    Screen readers read "equals equals equals" letter-by-letter, so we
    avoid punctuation banners — just a clear heading line in the same
    style as the other section headings.
    """
    return f"FILE {index} OF {total}: {filename}"


def _error_section(failures: list[tuple[str, UserFacingError]]) -> str:
    """Build an error summary section from per-file failures."""
    if not failures:
        return ""
    count = len(failures)
    lines = [
        "ERRORS",
        (
            f"{count} file could not be analyzed."
            if count == 1
            else f"{count} files could not be analyzed."
        ),
        "",
    ]
    for filename, err in failures:
        lines.append(f"File: {filename}")
        lines.append(err.title + ".")
        lines.append(err.body)
        if err.hint:
            lines.append("What to do: " + err.hint)
        lines.append("")
    # Trim trailing blank line.
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


class AnalysisWorker:
    """Run analysis in a background thread and post results to the UI thread."""

    def __init__(
        self,
        target_paths: list[str],
        genre_keys: list[str] | None,
        reference_path: str | None,
        on_done,
        on_error,
        on_progress,
    ) -> None:
        self._targets = list(target_paths)
        self._genre_keys = list(genre_keys or [])
        self._reference = reference_path
        self._on_done = on_done
        self._on_error = on_error
        self._on_progress = on_progress
        self._thread = threading.Thread(target=self._run, daemon=True, name="nva-analysis")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        self._thread.start()

    def _emit_progress(self, percent: int, label: str) -> None:
        wx.CallAfter(self._on_progress, percent, label)

    def _emit_done(self, report: str, had_failures: bool) -> None:
        wx.CallAfter(self._on_done, report, had_failures)

    def _emit_error(self, err: UserFacingError) -> None:
        wx.CallAfter(self._on_error, err)

    # ------------------------------------------------------------------ #
    # Actual analysis
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        t0 = time.time()
        n_targets = len(self._targets)
        log.info(
            "worker start targets=%d genres=%s reference=%s",
            n_targets,
            self._genre_keys,
            self._reference,
        )
        if n_targets == 0:
            # Should never happen — the UI disables Analyze unless files are
            # selected — but report cleanly instead of crashing if it does.
            self._emit_error(
                UserFacingError(
                    title="No files to analyze",
                    body="No audio files are currently in the list.",
                    hint="Add at least one audio file, then press Analyze again.",
                )
            )
            return

        # Budget: 0..15% for the reference (if any), 15..90% for the targets,
        # 90..100% for report assembly.
        has_reference = bool(self._reference)
        reference: AnalysisResult | None = None
        targets_start = 0
        if has_reference:
            try:
                reference = analyze(
                    self._reference,
                    progress_cb=self._emit_progress,
                    percent_start=0,
                    percent_end=15,
                    label_prefix="Reference",
                )
            except MissingFFmpegError as exc:
                self._emit_error(exc)
                return
            except UserFacingError as exc:
                # A bad reference file is a hard stop: without the reference
                # the comparison mode makes no sense, so we fail the run
                # with a dedicated message rather than silently downgrading.
                self._emit_error(
                    UserFacingError(
                        title="Reference file could not be analyzed",
                        body=exc.body,
                        hint=(
                            (exc.hint + " ") if exc.hint else ""
                        )
                        + "You can also clear the reference file to run the analysis without one.",
                    )
                )
                return
            log.info(
                "reference analyzed: I=%.1f LUFS crest=%.1f dB",
                reference.loudness.integrated_lufs,
                reference.dynamics.crest_factor_db,
            )
            targets_start = 15

        targets_end = 90
        per_file = max(1, (targets_end - targets_start) // n_targets)

        report_parts: list[tuple[str, str]] = []  # (filename, rendered section)
        failures: list[tuple[str, UserFacingError]] = []

        for i, target_path in enumerate(self._targets):
            slice_start = targets_start + per_file * i
            slice_end = targets_end if i == n_targets - 1 else slice_start + per_file
            prefix = f"File {i + 1} of {n_targets}" if n_targets > 1 else ""
            filename = Path(target_path).name
            try:
                result = analyze(
                    target_path,
                    progress_cb=self._emit_progress,
                    percent_start=slice_start,
                    percent_end=slice_end,
                    label_prefix=prefix,
                )
            except MissingFFmpegError as exc:
                # FFmpeg missing is a global stop — continuing with other
                # files would just fail the same way.
                self._emit_error(exc)
                return
            except UserFacingError as exc:
                log.warning("file %d/%d failed: %s", i + 1, n_targets, exc)
                failures.append((filename, exc))
                self._emit_progress(slice_end, f"{filename} skipped")
                continue
            except Exception as exc:  # noqa: BLE001 — any other failure is unexpected
                log.exception("unexpected error on %s", filename)
                failures.append(
                    (
                        filename,
                        UserFacingError(
                            title=f"Unexpected error while analyzing {filename}",
                            body=str(exc) or "The audio pipeline raised an unexpected error.",
                            hint=(
                                "Try re-exporting the file as WAV or FLAC. "
                                "If the problem persists, relaunch the app "
                                "with NVA_DEBUG=1 to capture a detailed log."
                            ),
                        ),
                    )
                )
                self._emit_progress(slice_end, f"{filename} skipped")
                continue

            log.info(
                "target %d/%d analyzed: %s I=%.1f LUFS crest=%.1f dB",
                i + 1,
                n_targets,
                filename,
                result.loudness.integrated_lufs,
                result.dynamics.crest_factor_db,
            )
            extras: list[str] = []
            for key in self._genre_keys:
                profile = GENRES.get(key)
                if profile:
                    extras.append(build_genre_comparison(result, profile))
            if reference is not None:
                extras.append(build_reference_comparison(result, reference))
            try:
                section = build_report(result, extra_sections=extras)
            except Exception as exc:  # noqa: BLE001 — report builder is deterministic but paranoid
                log.exception("report builder failed for %s", filename)
                failures.append(
                    (
                        filename,
                        UserFacingError(
                            title=f"Could not format the report for {filename}",
                            body=str(exc),
                            hint="The underlying analysis succeeded; please try again.",
                        ),
                    )
                )
                continue
            report_parts.append((target_path, section))

        # Nothing survived: show a single clear error rather than an empty
        # report with a giant error section.
        if not report_parts and failures:
            # If every file failed with the same kind of error, surface that
            # directly; otherwise show the first one and note the count.
            unique_titles = {err.title for _, err in failures}
            if len(unique_titles) == 1:
                _, first_err = failures[0]
                self._emit_error(
                    UserFacingError(
                        title=first_err.title
                        if len(failures) == 1
                        else f"None of the {len(failures)} files could be analyzed",
                        body=first_err.body,
                        hint=first_err.hint,
                    )
                )
            else:
                self._emit_error(
                    UserFacingError(
                        title=f"None of the {len(failures)} files could be analyzed",
                        body=(
                            "Each file failed for a different reason. Open "
                            "the Details button to see them all."
                        ),
                        hint="\n\n".join(
                            f"{name}: {err.title}. {err.body}"
                            + ((" " + err.hint) if err.hint else "")
                            for name, err in failures
                        ),
                    )
                )
            return

        self._emit_progress(92, "Assembling report")
        error_block = _error_section(failures)

        if len(report_parts) == 1 and not failures:
            full_report = report_parts[0][1]
        else:
            chunks: list[str] = []
            if error_block:
                chunks.append(error_block)
            total_ok = len(report_parts)
            for i, (path, part) in enumerate(report_parts, start=1):
                if total_ok > 1 or failures:
                    chunks.append(_file_section_header(i, total_ok, Path(path).name))
                chunks.append(part)
            full_report = "\n\n".join(chunks)

        log.info(
            "report ready, length=%d chars, %d file(s) ok, %d failed, total=%.2fs",
            len(full_report),
            len(report_parts),
            len(failures),
            time.time() - t0,
        )
        self._emit_progress(100, "Done")
        self._emit_done(full_report, had_failures=bool(failures))


def start_analysis(
    target_paths: Iterable[str],
    genre_keys: Iterable[str] | None,
    reference_path: str | None,
    on_done,
    on_error,
    on_progress,
) -> AnalysisWorker:
    """Spin up the background worker. Returns it so callers can keep a ref."""
    worker = AnalysisWorker(
        list(target_paths),
        list(genre_keys) if genre_keys is not None else None,
        reference_path,
        on_done,
        on_error,
        on_progress,
    )
    worker.start()
    return worker
