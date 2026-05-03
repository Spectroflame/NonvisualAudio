"""Build comparison sections for genre and custom reference modes."""

from __future__ import annotations

from nonvisualaudio.analysis.result import AnalysisResult
from nonvisualaudio.localization import t, t_subject
from nonvisualaudio.reporting.genre_profiles import GenreProfile
from nonvisualaudio.reporting.templates import fmt_decimal, fmt_signed, heading


def _lufs_diff_sentence(
    target: float,
    reference: float,
    label: str,
    *,
    project: bool = False,
) -> str:
    diff = target - reference
    target_s = fmt_signed(target)
    reference_s = fmt_signed(reference)
    diff_s = fmt_signed(abs(diff))
    if diff > 1.0:
        return t_subject(
            "report.comp.lufs_louder",
            project=project,
            target=target_s,
            diff=diff_s,
            label=label,
            reference=reference_s,
        )
    if diff < -1.0:
        return t_subject(
            "report.comp.lufs_quieter",
            project=project,
            target=target_s,
            diff=diff_s,
            label=label,
            reference=reference_s,
        )
    return t_subject(
        "report.comp.lufs_inline",
        project=project,
        target=target_s,
        label=label,
        reference=reference_s,
    )


def build_genre_comparison(
    target: AnalysisResult,
    genre: GenreProfile,
    *,
    project: bool = False,
) -> str:
    lines = [heading(t("report.heading.comparison_genre", name=genre.display_name))]
    label = t("report.comp.typical_prefix", name=genre.display_name.lower())
    lines.append(_lufs_diff_sentence(
        target.loudness.integrated_lufs, genre.target_lufs, label,
        project=project,
    ))

    lra = target.loudness.loudness_range_lu
    lra_s = fmt_signed(lra)
    low_i = int(genre.lra_low)
    high_i = int(genre.lra_high)
    if lra < genre.lra_low:
        lines.append(
            t("report.comp.lra_narrow", lra=lra_s, low=low_i, high=high_i)
        )
    elif lra > genre.lra_high:
        lines.append(
            t("report.comp.lra_wide", lra=lra_s, low=low_i, high=high_i)
        )
    else:
        lines.append(
            t("report.comp.lra_within", lra=lra_s, low=low_i, high=high_i)
        )

    lines.append(t("report.comp.genre_notes", notes=genre.notes))
    return "\n".join(lines)


def _band_diff_sentence(name: str, a: float, b: float) -> str | None:
    diff = a - b
    if abs(diff) < 2.0:
        return None
    direction = (
        t("report.ref.band.stronger") if diff > 0 else t("report.ref.band.weaker")
    )
    return t(
        "report.ref.band_diff",
        name=name,
        diff=fmt_decimal(abs(diff)),
        direction=direction,
    )


def build_reference_comparison(
    target: AnalysisResult,
    reference: AnalysisResult,
    *,
    project: bool = False,
    reference_is_project: bool = False,
) -> str:
    """Compare target against reference.

    ``project=True`` swaps "the target" wording so the comparison reads
    as "the project is X LU louder than the reference" instead of "the
    target is...". ``reference_is_project=True`` tells the renderer the
    reference itself was assembled from multiple files (a "reference
    project"); the opening line then reads "Reference project: ..."
    instead of "Reference filename: ...".
    """
    lines = [heading(t("report.heading.comparison_reference"))]
    intro_key = (
        "report.ref.filename.project"
        if reference_is_project
        else "report.ref.filename"
    )
    lines.append(t(intro_key, filename=reference.file_info.filename))

    # Loudness.
    di = target.loudness.integrated_lufs - reference.loudness.integrated_lufs
    if abs(di) >= 0.5:
        direction = (
            t("report.ref.direction.louder") if di > 0
            else t("report.ref.direction.quieter")
        )
        lines.append(
            t_subject(
                "report.ref.loudness_diff",
                project=project,
                diff=fmt_decimal(abs(di)),
                direction=direction,
                target=fmt_signed(target.loudness.integrated_lufs),
                reference=fmt_signed(reference.loudness.integrated_lufs),
            )
        )
    else:
        lines.append(t_subject("report.ref.loudness_same", project=project))

    # LRA.
    dlra = target.loudness.loudness_range_lu - reference.loudness.loudness_range_lu
    if abs(dlra) >= 1.0:
        direction = (
            t("report.ref.lra.wider") if dlra > 0
            else t("report.ref.lra.narrower")
        )
        lines.append(
            t_subject(
                "report.ref.lra_diff",
                project=project,
                direction=direction,
                diff=fmt_decimal(abs(dlra)),
            )
        )

    # Dynamics.
    dcrest = target.dynamics.crest_factor_db - reference.dynamics.crest_factor_db
    if abs(dcrest) >= 1.5:
        direction = (
            t("report.ref.dyn.more_dynamic") if dcrest > 0
            else t("report.ref.dyn.more_compressed")
        )
        lines.append(
            t_subject(
                "report.ref.dyn_diff",
                project=project,
                direction=direction,
                diff=fmt_decimal(abs(dcrest)),
            )
        )

    # Frequency balance.
    tb = target.spectrum.bands
    rb = reference.spectrum.bands
    band_pairs = (
        ("sub_bass", tb.sub_db, rb.sub_db),
        ("bass", tb.bass_db, rb.bass_db),
        ("low_midrange", tb.low_mid_db, rb.low_mid_db),
        ("midrange", tb.mid_db, rb.mid_db),
        ("presence", tb.presence_db, rb.presence_db),
        ("air", tb.air_db, rb.air_db),
    )
    band_lines: list[str] = []
    for key, a, b in band_pairs:
        s = _band_diff_sentence(t(f"report.band.{key}"), a, b)
        if s:
            band_lines.append(s)
    if not band_lines:
        band_lines.append(t("report.ref.freq_same"))
    lines.extend(band_lines)
    return "\n".join(lines)
