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
from collections.abc import Callable, Iterable
from pathlib import Path

import wx

from nonvisualaudio.analysis.memory import (
    MemoryEstimate,
    RamCheckCancelled,
)
from nonvisualaudio.analysis.pipeline import analyze
from nonvisualaudio.analysis.project import analyze_project
from nonvisualaudio.analysis.result import AnalysisResult
from nonvisualaudio.errors import (
    AudioDecodeError,
    LoudnessMeasurementError,
    MissingFFmpegError,
    UserFacingError,
)
from nonvisualaudio.localization import t
from nonvisualaudio.reporting import genre_profiles
from nonvisualaudio.reporting.builder import ReportSections, build_report
from nonvisualaudio.reporting.comparison import (
    build_genre_comparison,
    build_reference_comparison,
)
from nonvisualaudio.reporting.project_report import build_project_report
from nonvisualaudio.reporting.templates import ReportDoc, Section, heading_text

log = logging.getLogger("nonvisualaudio.worker")


def _error_section(failures: list[tuple[str, UserFacingError]]) -> Section | None:
    """Build an error summary section from per-file failures, or ``None``.

    Returns ``None`` when there is nothing to report; the caller can
    use that as a "skip this block" signal without thinking about the
    inner structure of a :class:`Section`.
    """
    if not failures:
        return None
    count = len(failures)
    key = "ui.worker.error_count.one" if count == 1 else "ui.worker.error_count.other"
    lines: list[str] = [t(key, count=count), ""]
    for filename, err in failures:
        lines.append(t("ui.worker.error_file_line", filename=filename))
        lines.append(err.title + ".")
        lines.append(err.body)
        if err.hint:
            lines.append(t("ui.worker.error_whatdo", hint=err.hint))
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return Section(
        level=2,
        heading=heading_text(t("ui.worker.errors_heading"), level=2),
        body=tuple(lines),
    )


class AnalysisWorker:
    """Run analysis in a background thread and post results to the UI thread."""

    def __init__(
        self,
        target_paths: list[str],
        genre_keys: list[str] | None,
        reference_paths: list[str] | None,
        on_done,
        on_error,
        on_progress,
        sections: ReportSections | None = None,
        project_mode: bool = False,
        project_name: str | None = None,
        reference_name: str | None = None,
        on_confirm_memory: Callable[[MemoryEstimate], bool] | None = None,
    ) -> None:
        self._targets = list(target_paths)
        self._genre_keys = list(genre_keys or [])
        # Reference is now a list so the user can pick a multi-file
        # reference project (e.g. a previously released album to A/B
        # against). One-element lists keep the historical single-file
        # behaviour byte-for-byte.
        self._reference_paths = list(reference_paths or [])
        self._on_done = on_done
        self._on_error = on_error
        self._on_progress = on_progress
        # The RAM guard runs the confirm dialog on the UI thread. When
        # no callback is supplied (tests, headless callers), the guard
        # is effectively disabled and analyses run unattended.
        self._on_confirm_memory = on_confirm_memory
        self._sections = sections if sections is not None else ReportSections.all()
        self._project_mode = project_mode
        self._project_name = project_name
        self._reference_name = reference_name
        self._thread = threading.Thread(target=self._run, daemon=True, name="nva-analysis")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        self._thread.start()

    def _emit_progress(self, percent: int, label: str) -> None:
        wx.CallAfter(self._on_progress, percent, label)

    def _emit_done(self, report: ReportDoc, had_failures: bool) -> None:
        wx.CallAfter(self._on_done, report, had_failures)

    def _emit_error(self, err: UserFacingError) -> None:
        wx.CallAfter(self._on_error, err)

    def _confirm_memory(self, estimate: MemoryEstimate) -> bool:
        """Ask the UI thread whether to proceed with a memory-heavy run.

        The worker runs on a background thread, but the warning dialog
        belongs on the UI thread. We marshal the question across via
        ``wx.CallAfter`` and block this thread on a one-shot event
        until the user clicks an option. Without a UI callback (e.g.
        in unit tests) we let the analysis proceed silently — the
        guard is then effectively disabled.
        """
        if self._on_confirm_memory is None:
            return True
        answer: list[bool] = []
        ready = threading.Event()

        def _ask_ui() -> None:
            try:
                answer.append(bool(self._on_confirm_memory(estimate)))
            except Exception:  # noqa: BLE001 — never let the dialog leak
                log.exception("memory-confirm dialog crashed; defaulting to cancel")
                answer.append(False)
            finally:
                ready.set()

        wx.CallAfter(_ask_ui)
        ready.wait()
        return answer[0] if answer else False

    # ------------------------------------------------------------------ #
    # Actual analysis
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Reference helper
    # ------------------------------------------------------------------ #

    def _analyze_reference(
        self,
        percent_start: int,
        percent_end: int,
    ) -> AnalysisResult | None:
        """Analyse the configured reference. Single file → analyze();
        multi-file/folder → analyze_project() and return its combined
        result. Returns None when no reference was configured.
        """
        n = len(self._reference_paths)
        if n == 0:
            return None
        prefix = t("ui.worker.reference_prefix")
        if n == 1:
            return analyze(
                self._reference_paths[0],
                progress_cb=self._emit_progress,
                percent_start=percent_start,
                percent_end=percent_end,
                label_prefix=prefix,
                confirm_memory_cb=self._confirm_memory,
            )
        # Multi-file reference: build a project-style reference. The
        # combined AnalysisResult plugs straight into the existing
        # comparison builder with no further changes.
        ref_project = analyze_project(
            self._reference_paths,
            project_name=self._reference_name or t("project.reference_default_name"),
            progress_cb=self._emit_progress,
            percent_start=percent_start,
            percent_end=percent_end,
            confirm_memory_cb=self._confirm_memory,
        )
        return ref_project.combined

    def _run(self) -> None:
        t0 = time.time()
        n_targets = len(self._targets)
        log.info(
            "worker start targets=%d genres=%s reference=%d project=%s",
            n_targets,
            self._genre_keys,
            len(self._reference_paths),
            self._project_mode,
        )
        if self._project_mode and n_targets >= 1:
            self._run_project(t0)
            return
        if n_targets == 0:
            # Should never happen — the UI disables Analyze unless files are
            # selected — but report cleanly instead of crashing if it does.
            self._emit_error(
                UserFacingError(
                    title=t("worker.error.no_files.title"),
                    body=t("worker.error.no_files.body"),
                    hint=t("worker.error.no_files.hint"),
                )
            )
            return

        # Budget: 0..15% for the reference (if any), 15..90% for the targets,
        # 90..100% for report assembly.
        has_reference = bool(self._reference_paths)
        reference: AnalysisResult | None = None
        targets_start = 0
        if has_reference:
            try:
                reference = self._analyze_reference(
                    percent_start=0, percent_end=15
                )
            except MissingFFmpegError as exc:
                self._emit_error(exc)
                return
            except RamCheckCancelled:
                log.info("ram guard cancelled the reference analysis")
                self._emit_error(
                    UserFacingError(
                        title=t("worker.error.ram_cancelled.title"),
                        body=t("worker.error.ram_cancelled.body"),
                        hint=t("worker.error.ram_cancelled.hint"),
                    )
                )
                return
            except UserFacingError as exc:
                # A bad reference file is a hard stop: without the reference
                # the comparison mode makes no sense, so we fail the run
                # with a dedicated message rather than silently downgrading.
                extra_hint = t("worker.error.bad_reference.extra_hint")
                self._emit_error(
                    UserFacingError(
                        title=t("worker.error.bad_reference.title"),
                        body=exc.body,
                        hint=(
                            (exc.hint + " ") if exc.hint else ""
                        )
                        + extra_hint,
                    )
                )
                return
            assert reference is not None
            log.info(
                "reference analyzed: I=%.1f LUFS crest=%.1f dB",
                reference.loudness.integrated_lufs,
                reference.dynamics.crest_factor_db,
            )
            targets_start = 15

        targets_end = 90
        per_file = max(1, (targets_end - targets_start) // n_targets)

        report_parts: list[tuple[str, ReportDoc]] = []  # (filename, doc)
        failures: list[tuple[str, UserFacingError]] = []

        # Heading hierarchy. A multi-file batch nests each file under a
        # "Track X: filename" <h2>, so the per-file inner sections push
        # down to <h3>. A single-file run keeps the filename as the
        # <h1> of its own report and the sections live at <h2>.
        is_batch = n_targets > 1
        per_file_title_level = 2 if is_batch else 1
        per_file_section_level = 3 if is_batch else 2

        for i, target_path in enumerate(self._targets):
            slice_start = targets_start + per_file * i
            slice_end = targets_end if i == n_targets - 1 else slice_start + per_file
            prefix = (
                t("ui.worker.file_prefix", index=i + 1, total=n_targets)
                if n_targets > 1
                else ""
            )
            filename = Path(target_path).name
            try:
                result = analyze(
                    target_path,
                    progress_cb=self._emit_progress,
                    percent_start=slice_start,
                    percent_end=slice_end,
                    label_prefix=prefix,
                    confirm_memory_cb=self._confirm_memory,
                )
            except MissingFFmpegError as exc:
                # FFmpeg missing is a global stop — continuing with other
                # files would just fail the same way.
                self._emit_error(exc)
                return
            except RamCheckCancelled:
                # User declined the RAM warning for this file. We stop
                # the whole batch — analysing the remainder after the
                # user has signalled "this run is too big" would be
                # surprising and almost always unwanted.
                log.info("ram guard cancelled the run at file %d/%d", i + 1, n_targets)
                self._emit_error(
                    UserFacingError(
                        title=t("worker.error.ram_cancelled.title"),
                        body=t("worker.error.ram_cancelled.body"),
                        hint=t("worker.error.ram_cancelled.hint"),
                    )
                )
                return
            except UserFacingError as exc:
                log.warning("file %d/%d failed: %s", i + 1, n_targets, exc)
                failures.append((filename, exc))
                self._emit_progress(slice_end, t("ui.worker.skipped", filename=filename))
                continue
            except Exception as exc:  # noqa: BLE001 — any other failure is unexpected
                log.exception("unexpected error on %s", filename)
                failures.append(
                    (
                        filename,
                        UserFacingError(
                            title=t("worker.error.unexpected.title", filename=filename),
                            body=str(exc) or t("worker.error.unexpected.body"),
                            hint=t("worker.error.unexpected.hint"),
                        ),
                    )
                )
                self._emit_progress(slice_end, t("ui.worker.skipped", filename=filename))
                continue

            log.info(
                "target %d/%d analyzed: %s I=%.1f LUFS crest=%.1f dB",
                i + 1,
                n_targets,
                filename,
                result.loudness.integrated_lufs,
                result.dynamics.crest_factor_db,
            )
            extras: list[Section] = []
            for key in self._genre_keys:
                profile = genre_profiles.GENRES.get(key)
                if profile:
                    extras.append(
                        build_genre_comparison(
                            result, profile, level=per_file_section_level
                        )
                    )
            if reference is not None:
                extras.append(
                    build_reference_comparison(
                        result,
                        reference,
                        reference_is_project=len(self._reference_paths) > 1,
                        level=per_file_section_level,
                    )
                )
            try:
                if is_batch:
                    title = t(
                        "report.title.batch.file",
                        index=i + 1,
                        filename=filename,
                    )
                else:
                    title = t("report.title.single", filename=filename)
                doc = build_report(
                    result,
                    extra_sections=extras,
                    sections=self._sections,
                    title=title,
                    title_level=per_file_title_level,
                    section_level=per_file_section_level,
                )
            except Exception as exc:  # noqa: BLE001 — report builder is deterministic but paranoid
                log.exception("report builder failed for %s", filename)
                failures.append(
                    (
                        filename,
                        UserFacingError(
                            title=t(
                                "worker.error.report_format.title", filename=filename
                            ),
                            body=str(exc),
                            hint=t("worker.error.report_format.hint"),
                        ),
                    )
                )
                continue
            report_parts.append((target_path, doc))

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
                        else t(
                            "worker.error.all_failed.title", count=len(failures)
                        ),
                        body=first_err.body,
                        hint=first_err.hint,
                    )
                )
            else:
                self._emit_error(
                    UserFacingError(
                        title=t(
                            "worker.error.all_failed.title", count=len(failures)
                        ),
                        body=t("worker.error.all_failed.body"),
                        hint="\n\n".join(
                            f"{name}: {err.title}. {err.body}"
                            + ((" " + err.hint) if err.hint else "")
                            for name, err in failures
                        ),
                    )
                )
            return

        self._emit_progress(92, t("ui.worker.assembling"))
        error_block = _error_section(failures)

        if len(report_parts) == 1 and not failures:
            full_report = report_parts[0][1]
        else:
            sections: list[Section] = []
            # Batch wrapper <h1>: every per-file doc already carries
            # its own "Track X" <h2>, so this top heading sets the
            # overall document title and gives the screen reader a
            # single anchor to land on when the report opens.
            sections.append(
                Section(
                    level=1,
                    heading=heading_text(t("report.title.batch"), level=1),
                    body=(),
                )
            )
            if error_block is not None:
                sections.append(error_block)
            for _path, part in report_parts:
                sections.extend(part.sections)
            full_report = ReportDoc(sections=tuple(sections))

        log.info(
            "report ready, %d section(s), %d file(s) ok, %d failed, total=%.2fs",
            len(full_report.sections),
            len(report_parts),
            len(failures),
            time.time() - t0,
        )
        self._emit_progress(100, t("ui.worker.done"))
        self._emit_done(full_report, had_failures=bool(failures))

    # ------------------------------------------------------------------ #
    # Project-mode flow
    # ------------------------------------------------------------------ #

    def _run_project(self, t0: float) -> None:
        """Analyse all selected files together as one project."""
        # Optional reference still works the same way: it occupies the
        # first 0..15 % slice and turns into a reference comparison
        # against the project's combined result.
        has_reference = bool(self._reference_paths)
        reference: AnalysisResult | None = None
        project_start = 0
        if has_reference:
            try:
                reference = self._analyze_reference(
                    percent_start=0, percent_end=15
                )
            except MissingFFmpegError as exc:
                self._emit_error(exc)
                return
            except RamCheckCancelled:
                log.info("ram guard cancelled the project reference analysis")
                self._emit_error(
                    UserFacingError(
                        title=t("worker.error.ram_cancelled.title"),
                        body=t("worker.error.ram_cancelled.body"),
                        hint=t("worker.error.ram_cancelled.hint"),
                    )
                )
                return
            except UserFacingError as exc:
                self._emit_error(
                    UserFacingError(
                        title=t("worker.error.bad_reference.title"),
                        body=exc.body,
                        hint=(
                            (exc.hint + " ") if exc.hint else ""
                        )
                        + t("worker.error.bad_reference.extra_hint"),
                    )
                )
                return
            project_start = 15

        try:
            project = analyze_project(
                self._targets,
                project_name=self._project_name,
                progress_cb=self._emit_progress,
                percent_start=project_start,
                percent_end=92,
                confirm_memory_cb=self._confirm_memory,
            )
        except MissingFFmpegError as exc:
            self._emit_error(exc)
            return
        except RamCheckCancelled:
            log.info("ram guard cancelled the project analysis")
            self._emit_error(
                UserFacingError(
                    title=t("worker.error.ram_cancelled.title"),
                    body=t("worker.error.ram_cancelled.body"),
                    hint=t("worker.error.ram_cancelled.hint"),
                )
            )
            return
        except UserFacingError as exc:
            self._emit_error(exc)
            return
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            log.exception("project pipeline crashed")
            self._emit_error(
                UserFacingError(
                    title=t("worker.error.project_failed.title"),
                    body=str(exc) or t("worker.error.project_failed.body"),
                    hint=t("worker.error.project_failed.hint"),
                )
            )
            return

        extras: list[Section] = []
        # Project-mode combined sections sit at <h2>, so the comparison
        # blocks that get interleaved with them have to match.
        for key in self._genre_keys:
            profile = genre_profiles.GENRES.get(key)
            if profile:
                extras.append(
                    build_genre_comparison(
                        project.combined, profile, project=True, level=2
                    )
                )
        if reference is not None:
            extras.append(
                build_reference_comparison(
                    project.combined,
                    reference,
                    project=True,
                    reference_is_project=len(self._reference_paths) > 1,
                    level=2,
                )
            )

        try:
            full_report = build_project_report(
                project,
                extra_sections=extras,
                sections=self._sections,
            )
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            log.exception("project report builder crashed")
            self._emit_error(
                UserFacingError(
                    title=t("worker.error.project_failed.title"),
                    body=str(exc) or t("worker.error.project_failed.body"),
                    hint=t("worker.error.project_failed.hint"),
                )
            )
            return

        log.info(
            "project report ready: %d files, %d section(s), total=%.2fs",
            len(project.files),
            len(full_report.sections),
            time.time() - t0,
        )
        self._emit_progress(100, t("ui.worker.done"))
        self._emit_done(full_report, had_failures=False)


def start_analysis(
    target_paths: Iterable[str],
    genre_keys: Iterable[str] | None,
    reference_paths: Iterable[str] | None,
    on_done,
    on_error,
    on_progress,
    sections: ReportSections | None = None,
    project_mode: bool = False,
    project_name: str | None = None,
    reference_name: str | None = None,
    on_confirm_memory: Callable[[MemoryEstimate], bool] | None = None,
) -> AnalysisWorker:
    """Spin up the background worker. Returns it so callers can keep a ref."""
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
