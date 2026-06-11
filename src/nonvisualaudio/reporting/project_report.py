"""Render a project-mode analysis as a single screen-reader-friendly report.

The shape mirrors a single-file report so users don't have to learn a
new layout: a project header, the combined per-section analysis, and a
"cross-track consistency" block that calls out outliers.

All wording is locale-aware via :func:`nonvisualaudio.localization.t`.
"""

from __future__ import annotations

from nonvisualaudio.analysis.project import ProjectResult
from nonvisualaudio.localization import t
from nonvisualaudio.reporting.builder import (
    MATERIAL_MUSIC,
    TONALITY_FULL_RANGE,
    ReportSections,
    build_report,
)
from nonvisualaudio.reporting.templates import (
    ReportDoc,
    Section,
    fmt_decimal,
    fmt_duration,
    fmt_signed,
    heading_text,
)


# --------------------------------------------------------------------------- #
# Project header
# --------------------------------------------------------------------------- #


def _project_header(project: ProjectResult) -> Section:
    n = len(project.files)
    total = project.combined.file_info.duration_seconds
    # The project name lives in the level-1 heading itself rather than
    # on a separate "Project name: …" body line — the heading is what
    # screen readers and the HTML <h1> use as the document title, so
    # duplicating the name underneath was just noise.
    lines: list[str] = []
    count_key = (
        "report.project.file_count.one"
        if n == 1
        else "report.project.file_count.other"
    )
    lines.append(t(count_key, count=n))
    lines.append(t("report.project.total_duration", duration=fmt_duration(total)))
    sample_rate = project.combined.file_info.sample_rate
    if sample_rate:
        lines.append(t("report.project.combined_rate", rate=sample_rate))
    # Track listing — one line per file with its position in the project.
    lines.append("")
    lines.append(t("report.project.tracks_heading"))
    for i, fr in enumerate(project.files, start=1):
        lines.append(
            t(
                "report.project.track_line",
                index=i,
                filename=fr.file_info.filename,
                duration=fmt_duration(fr.file_info.duration_seconds),
            )
        )
    return Section(
        level=1,
        heading=heading_text(
            t("report.title.project", name=project.project_name), level=1
        ),
        body=tuple(lines),
    )


# --------------------------------------------------------------------------- #
# Cross-track consistency
# --------------------------------------------------------------------------- #


def _loudness_consistency(project: ProjectResult) -> list[str]:
    """One-line summary: how much the per-track loudness varies plus
    which track is loudest and which is quietest. Earlier versions also
    flagged dynamics and frequency outliers; user feedback was that the
    extra detail was more noise than signal, so the consistency block
    now sticks to loudness only.
    """
    files = project.files
    if len(files) < 2:
        return []
    values = [(fr.file_info.filename, fr.loudness.integrated_lufs) for fr in files]
    loudest_name, loudest = max(values, key=lambda x: x[1])
    quietest_name, quietest = min(values, key=lambda x: x[1])
    spread = loudest - quietest

    if spread < 1.0:
        return [t("report.project.loudness_consistent", spread=fmt_decimal(spread))]
    return [
        t(
            "report.project.loudness_spread",
            spread=fmt_decimal(spread),
            loudest_name=loudest_name,
            loudest_value=fmt_signed(loudest),
            quietest_name=quietest_name,
            quietest_value=fmt_signed(quietest),
        )
    ]


def _consistency_section(project: ProjectResult, *, level: int = 2) -> Section:
    body = _loudness_consistency(project)
    if not body:
        body = [t("report.project.no_consistency_findings")]
    return Section(
        level=level,
        heading=heading_text(t("report.heading.consistency"), level=level),
        body=tuple(body),
    )


# --------------------------------------------------------------------------- #
# Top-level
# --------------------------------------------------------------------------- #


def build_project_report(
    project: ProjectResult,
    extra_sections: list[Section] | None = None,
    sections: ReportSections | None = None,
    include_consistency: bool = True,
    *,
    material: str = MATERIAL_MUSIC,
    tonality: str = TONALITY_FULL_RANGE,
) -> ReportDoc:
    """Render the full project-mode report as a structured :class:`ReportDoc`.

    ``sections`` mirrors the single-file knob — the user can keep just
    loudness, just spectrum, etc. The project header is governed by the
    ``file_info`` flag (it stands in for the per-file FILE INFO block);
    the cross-track consistency block uses its own ``include_consistency``
    flag because it has no analogue in single-file mode.

    ``material`` and ``tonality`` mirror :func:`build_report`'s
    parameters and are passed straight through to the inner combined
    report.
    """
    selected = sections if sections is not None else ReportSections.all()

    out: list[Section] = []
    if selected.file_info:
        out.append(_project_header(project))

    # Combined inner report. The header was already emitted above (or
    # intentionally omitted), so suppress the inner FILE INFO block.
    # ``project=True`` flips verdict wording from "the file" to "the
    # project" throughout the inner report. The combined sections sit
    # directly under the project's <h1>, so they emit at <h2> — no
    # extra wrapper, no inner <h1>.
    inner_sections = selected.with_disabled(file_info=False)
    if inner_sections.any_enabled() or (selected.comparison and extra_sections):
        combined = build_report(
            project.combined,
            extra_sections=extra_sections,
            sections=inner_sections,
            project=True,
            material=material,
            tonality=tonality,
            title=None,
            section_level=2,
        )
        out.extend(combined.sections)

    if include_consistency and len(project.files) > 1:
        out.append(_consistency_section(project, level=2))

    return ReportDoc(sections=tuple(out))
