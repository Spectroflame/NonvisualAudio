"""Synchronous orchestration for one user-requested analysis run.

This module deliberately knows nothing about threads or wxPython.  It owns the
application workflow: reference analysis, batch/project routing, per-file error
policy, and report assembly.  :mod:`nonvisualaudio.ui.worker` is the thin
adapter that runs this workflow off the UI thread and posts callbacks back to
wx.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from nonvisualaudio import logging_setup
from nonvisualaudio.analysis.memory import MemoryEstimate, RamCheckCancelled
from nonvisualaudio.analysis.pipeline import analyze
from nonvisualaudio.analysis.project import analyze_project
from nonvisualaudio.analysis.result import AnalysisResult
from nonvisualaudio.cancellation import Cancellation, CancelledError
from nonvisualaudio.errors import MissingFFmpegError, UserFacingError
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


@dataclass(frozen=True)
class AnalysisRequest:
    """Immutable inputs for one analysis run."""

    targets: tuple[str, ...]
    genre_keys: tuple[str, ...]
    reference_paths: tuple[str, ...]
    sections: ReportSections
    project_mode: bool = False
    project_name: str | None = None
    reference_name: str | None = None

    @classmethod
    def create(
        cls,
        target_paths: Iterable[str],
        genre_keys: Iterable[str] | None,
        reference_paths: Iterable[str] | None,
        *,
        sections: ReportSections | None = None,
        project_mode: bool = False,
        project_name: str | None = None,
        reference_name: str | None = None,
    ) -> "AnalysisRequest":
        """Copy caller-owned iterables into a stable request snapshot."""
        return cls(
            targets=tuple(target_paths),
            genre_keys=tuple(genre_keys or ()),
            reference_paths=tuple(reference_paths or ()),
            sections=sections if sections is not None else ReportSections.all(),
            project_mode=project_mode,
            project_name=project_name,
            reference_name=reference_name,
        )


@dataclass(frozen=True)
class AnalysisCallbacks:
    """Side-effect boundary used by the synchronous workflow."""

    on_done: Callable[[ReportDoc, bool], None]
    on_error: Callable[[UserFacingError], None]
    on_progress: Callable[[int, str], None]
    confirm_memory: Callable[[MemoryEstimate], bool]


@dataclass(frozen=True)
class _RunContext:
    request: AnalysisRequest
    callbacks: AnalysisCallbacks
    cancel: Cancellation


@dataclass(frozen=True)
class _ReferenceOutcome:
    """Reference result plus an explicit run-may-continue flag."""

    may_continue: bool
    analysis: AnalysisResult | None = None


def _ram_cancelled_error() -> UserFacingError:
    return UserFacingError(
        title=t("worker.error.ram_cancelled.title"),
        body=t("worker.error.ram_cancelled.body"),
        hint=t("worker.error.ram_cancelled.hint"),
    )


def _bad_reference_error(exc: UserFacingError) -> UserFacingError:
    extra_hint = t("worker.error.bad_reference.extra_hint")
    return UserFacingError(
        title=t("worker.error.bad_reference.title"),
        body=exc.body,
        hint=((exc.hint + " ") if exc.hint else "") + extra_hint,
    )


def _error_section(failures: list[tuple[str, UserFacingError]]) -> Section | None:
    """Build the report block that describes skipped input files."""
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


def _all_failed_error(
    failures: list[tuple[str, UserFacingError]],
) -> UserFacingError:
    """Collapse per-file failures into one actionable run-level error."""
    unique_titles = {err.title for _, err in failures}
    if len(unique_titles) == 1:
        _, first_err = failures[0]
        return UserFacingError(
            title=(
                first_err.title
                if len(failures) == 1
                else t("worker.error.all_failed.title", count=len(failures))
            ),
            body=first_err.body,
            hint=first_err.hint,
        )
    return UserFacingError(
        title=t("worker.error.all_failed.title", count=len(failures)),
        body=t("worker.error.all_failed.body"),
        hint="\n\n".join(
            f"{name}: {err.title}. {err.body}"
            + ((" " + err.hint) if err.hint else "")
            for name, err in failures
        ),
    )


def _analyze_reference(
    context: _RunContext,
    percent_start: int,
    percent_end: int,
) -> AnalysisResult:
    """Analyze a configured single-file or project-style reference."""
    request = context.request
    n = len(request.reference_paths)
    if n == 0:
        raise ValueError("reference analysis requires at least one path")
    prefix = t("ui.worker.reference_prefix")
    if n == 1:
        return analyze(
            request.reference_paths[0],
            progress_cb=context.callbacks.on_progress,
            percent_start=percent_start,
            percent_end=percent_end,
            label_prefix=prefix,
            confirm_memory_cb=context.callbacks.confirm_memory,
            cancel=context.cancel,
        )
    project = analyze_project(
        list(request.reference_paths),
        project_name=request.reference_name
        or t("project.reference_default_name"),
        progress_cb=context.callbacks.on_progress,
        percent_start=percent_start,
        percent_end=percent_end,
        confirm_memory_cb=context.callbacks.confirm_memory,
        cancel=context.cancel,
    )
    return project.combined


def _prepare_reference(
    context: _RunContext,
    *,
    ram_cancel_log: str,
) -> _ReferenceOutcome:
    """Analyze the optional reference and apply its run-level error policy."""
    if not context.request.reference_paths:
        return _ReferenceOutcome(may_continue=True)
    try:
        analysis = _analyze_reference(context, percent_start=0, percent_end=15)
    except MissingFFmpegError as exc:
        context.callbacks.on_error(exc)
        return _ReferenceOutcome(may_continue=False)
    except RamCheckCancelled:
        log.info(ram_cancel_log)
        context.callbacks.on_error(_ram_cancelled_error())
        return _ReferenceOutcome(may_continue=False)
    except UserFacingError as exc:
        context.callbacks.on_error(_bad_reference_error(exc))
        return _ReferenceOutcome(may_continue=False)
    return _ReferenceOutcome(may_continue=True, analysis=analysis)


def _comparison_sections(
    request: AnalysisRequest,
    result: AnalysisResult,
    reference: AnalysisResult | None,
    *,
    project: bool,
    level: int,
) -> list[Section]:
    """Build genre and optional A/B comparison blocks for one result."""
    extras: list[Section] = []
    for key in request.genre_keys:
        profile = genre_profiles.GENRES.get(key)
        if profile:
            extras.append(
                build_genre_comparison(
                    result,
                    profile,
                    project=project,
                    level=level,
                )
            )
    if reference is not None:
        extras.append(
            build_reference_comparison(
                result,
                reference,
                project=project,
                reference_is_project=len(request.reference_paths) > 1,
                level=level,
            )
        )
    return extras


def _build_target_report(
    request: AnalysisRequest,
    result: AnalysisResult,
    reference: AnalysisResult | None,
    *,
    filename: str,
    index: int,
    target_count: int,
) -> ReportDoc:
    """Render one successfully analyzed target at the proper hierarchy."""
    is_batch = target_count > 1
    title = (
        t("report.title.batch.file", index=index + 1, filename=filename)
        if is_batch
        else t("report.title.single", filename=filename)
    )
    title_level = 2 if is_batch else 1
    section_level = 3 if is_batch else 2
    extras = _comparison_sections(
        request,
        result,
        reference,
        project=False,
        level=section_level,
    )
    return build_report(
        result,
        extra_sections=extras,
        sections=request.sections,
        material=genre_profiles.material_context_for(request.genre_keys),
        tonality=genre_profiles.tonality_context_for(request.genre_keys),
        profile_target_lufs=genre_profiles.loudest_target_lufs_for(
            request.genre_keys
        ),
        title=title,
        title_level=title_level,
        section_level=section_level,
    )


def _combine_batch_reports(
    report_parts: list[tuple[str, ReportDoc]],
    failures: list[tuple[str, UserFacingError]],
) -> ReportDoc:
    """Return a single report, adding batch/error wrappers when needed."""
    if len(report_parts) == 1 and not failures:
        return report_parts[0][1]

    sections: list[Section] = [
        Section(
            level=1,
            heading=heading_text(t("report.title.batch"), level=1),
            body=(),
        )
    ]
    error_block = _error_section(failures)
    if error_block is not None:
        sections.append(error_block)
    for _path, part in report_parts:
        sections.extend(part.sections)
    return ReportDoc(sections=tuple(sections))


def _analyze_target(
    context: _RunContext,
    target_path: str,
    *,
    index: int,
    target_count: int,
    percent_start: int,
    percent_end: int,
    label_prefix: str,
) -> AnalysisResult | UserFacingError:
    """Analyze one target, translating only file-local failures."""
    try:
        return analyze(
            target_path,
            progress_cb=context.callbacks.on_progress,
            percent_start=percent_start,
            percent_end=percent_end,
            label_prefix=label_prefix,
            confirm_memory_cb=context.callbacks.confirm_memory,
            cancel=context.cancel,
        )
    except (CancelledError, MissingFFmpegError, RamCheckCancelled):
        raise
    except UserFacingError as exc:
        log.warning("file %d/%d failed: %s", index + 1, target_count, exc)
        return exc
    except Exception:  # noqa: BLE001 -- defensive workflow boundary
        log.exception(
            "unexpected error on %s",
            logging_setup.path_for_log(target_path),
        )
        filename = Path(target_path).name
        return UserFacingError(
            title=t("worker.error.unexpected.title", filename=filename),
            body=t("worker.error.unexpected.body"),
            hint=t("worker.error.unexpected.hint"),
        )


def _render_target_report(
    request: AnalysisRequest,
    result: AnalysisResult,
    reference: AnalysisResult | None,
    *,
    target_path: str,
    filename: str,
    index: int,
    target_count: int,
) -> ReportDoc | UserFacingError:
    """Build one report, translating a formatter crash to a file failure."""
    try:
        return _build_target_report(
            request,
            result,
            reference,
            filename=filename,
            index=index,
            target_count=target_count,
        )
    except Exception:  # noqa: BLE001 -- report boundary
        log.exception(
            "report builder failed for %s",
            logging_setup.path_for_log(target_path),
        )
        return UserFacingError(
            title=t("worker.error.report_format.title", filename=filename),
            body=t("worker.error.report_format.body"),
            hint=t("worker.error.report_format.hint"),
        )


def _run_batch(context: _RunContext, started_at: float) -> None:
    request = context.request
    callbacks = context.callbacks
    target_count = len(request.targets)
    if target_count == 0:
        callbacks.on_error(
            UserFacingError(
                title=t("worker.error.no_files.title"),
                body=t("worker.error.no_files.body"),
                hint=t("worker.error.no_files.hint"),
            )
        )
        return

    reference_outcome = _prepare_reference(
        context,
        ram_cancel_log="ram guard cancelled the reference analysis",
    )
    if not reference_outcome.may_continue:
        return
    reference = reference_outcome.analysis
    targets_start = 15 if request.reference_paths else 0
    if reference is not None:
        log.info(
            "reference analyzed: I=%.1f LUFS crest=%.1f dB",
            reference.loudness.integrated_lufs,
            reference.dynamics.crest_factor_db,
        )

    targets_end = 90
    per_file = max(1, (targets_end - targets_start) // target_count)
    report_parts: list[tuple[str, ReportDoc]] = []
    failures: list[tuple[str, UserFacingError]] = []

    for index, target_path in enumerate(request.targets):
        slice_start = targets_start + per_file * index
        slice_end = (
            targets_end
            if index == target_count - 1
            else slice_start + per_file
        )
        prefix = (
            t("ui.worker.file_prefix", index=index + 1, total=target_count)
            if target_count > 1
            else ""
        )
        filename = Path(target_path).name
        try:
            result = _analyze_target(
                context,
                target_path,
                index=index,
                target_count=target_count,
                percent_start=slice_start,
                percent_end=slice_end,
                label_prefix=prefix,
            )
        except MissingFFmpegError as exc:
            callbacks.on_error(exc)
            return
        except RamCheckCancelled:
            log.info(
                "ram guard cancelled the run at file %d/%d",
                index + 1,
                target_count,
            )
            callbacks.on_error(_ram_cancelled_error())
            return
        if isinstance(result, UserFacingError):
            failures.append((filename, result))
            callbacks.on_progress(
                slice_end,
                t("ui.worker.skipped", filename=filename),
            )
            continue

        log.info(
            "target %d/%d analyzed: %s I=%.1f LUFS crest=%.1f dB",
            index + 1,
            target_count,
            logging_setup.path_for_log(target_path),
            result.loudness.integrated_lufs,
            result.dynamics.crest_factor_db,
        )
        doc = _render_target_report(
            request,
            result,
            reference,
            target_path=target_path,
            filename=filename,
            index=index,
            target_count=target_count,
        )
        if isinstance(doc, UserFacingError):
            failures.append((filename, doc))
            continue
        report_parts.append((target_path, doc))

    if not report_parts and failures:
        callbacks.on_error(_all_failed_error(failures))
        return

    callbacks.on_progress(92, t("ui.worker.assembling"))
    full_report = _combine_batch_reports(report_parts, failures)
    log.info(
        "report ready, %d section(s), %d file(s) ok, %d failed, total=%.2fs",
        len(full_report.sections),
        len(report_parts),
        len(failures),
        time.time() - started_at,
    )
    callbacks.on_progress(100, t("ui.worker.done"))
    callbacks.on_done(full_report, bool(failures))


def _run_project(context: _RunContext, started_at: float) -> None:
    request = context.request
    callbacks = context.callbacks
    reference_outcome = _prepare_reference(
        context,
        ram_cancel_log="ram guard cancelled the project reference analysis",
    )
    if not reference_outcome.may_continue:
        return
    reference = reference_outcome.analysis
    project_start = 15 if request.reference_paths else 0

    try:
        project = analyze_project(
            list(request.targets),
            project_name=request.project_name,
            progress_cb=callbacks.on_progress,
            percent_start=project_start,
            percent_end=92,
            confirm_memory_cb=callbacks.confirm_memory,
            cancel=context.cancel,
        )
    except CancelledError:
        raise
    except MissingFFmpegError as exc:
        callbacks.on_error(exc)
        return
    except RamCheckCancelled:
        log.info("ram guard cancelled the project analysis")
        callbacks.on_error(_ram_cancelled_error())
        return
    except UserFacingError as exc:
        callbacks.on_error(exc)
        return
    except Exception:  # noqa: BLE001 -- defensive workflow boundary
        log.exception("project pipeline crashed")
        callbacks.on_error(
            UserFacingError(
                title=t("worker.error.project_failed.title"),
                body=t("worker.error.project_failed.body"),
                hint=t("worker.error.project_failed.hint"),
            )
        )
        return

    extras = _comparison_sections(
        request,
        project.combined,
        reference,
        project=True,
        level=2,
    )
    try:
        full_report = build_project_report(
            project,
            extra_sections=extras,
            sections=request.sections,
            material=genre_profiles.material_context_for(request.genre_keys),
            tonality=genre_profiles.tonality_context_for(request.genre_keys),
            profile_target_lufs=genre_profiles.loudest_target_lufs_for(
                request.genre_keys
            ),
        )
    except Exception:  # noqa: BLE001 -- report boundary
        log.exception("project report builder crashed")
        callbacks.on_error(
            UserFacingError(
                title=t("worker.error.project_failed.title"),
                body=t("worker.error.project_failed.body"),
                hint=t("worker.error.project_failed.hint"),
            )
        )
        return

    log.info(
        "project report ready: %d files, %d section(s), total=%.2fs",
        len(project.files),
        len(full_report.sections),
        time.time() - started_at,
    )
    callbacks.on_progress(100, t("ui.worker.done"))
    callbacks.on_done(full_report, False)


def run_analysis(
    request: AnalysisRequest,
    callbacks: AnalysisCallbacks,
    cancel: Cancellation,
) -> None:
    """Execute one request synchronously and report through callbacks."""
    started_at = time.time()
    log.info(
        "worker start targets=%d genres=%s reference=%d project=%s",
        len(request.targets),
        request.genre_keys,
        len(request.reference_paths),
        request.project_mode,
    )
    context = _RunContext(request=request, callbacks=callbacks, cancel=cancel)
    if request.project_mode and request.targets:
        _run_project(context, started_at)
    else:
        _run_batch(context, started_at)
