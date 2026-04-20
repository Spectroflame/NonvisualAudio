"""Build comparison sections for genre and custom reference modes."""

from __future__ import annotations

from nonvisualaudio.analysis.result import AnalysisResult
from nonvisualaudio.reporting.genre_profiles import GenreProfile
from nonvisualaudio.reporting.templates import fmt_signed, heading


def _lufs_diff_sentence(target: float, reference: float, label: str) -> str:
    diff = target - reference
    if diff > 1.0:
        return (
            f"At {fmt_signed(target)} LUFS, this file is about {fmt_signed(abs(diff))} "
            f"LU louder than {label}, which usually sits around {fmt_signed(reference)} LUFS. "
            "That means less dynamic headroom than the target expects."
        )
    if diff < -1.0:
        return (
            f"At {fmt_signed(target)} LUFS, this file is about {fmt_signed(abs(diff))} "
            f"LU quieter than {label}, which usually sits around {fmt_signed(reference)} LUFS."
        )
    return (
        f"At {fmt_signed(target)} LUFS, this file is in line with {label} "
        f"(around {fmt_signed(reference)} LUFS)."
    )


def build_genre_comparison(target: AnalysisResult, genre: GenreProfile) -> str:
    lines = [heading(f"Comparison To {genre.display_name}")]
    lines.append(_lufs_diff_sentence(
        target.loudness.integrated_lufs, genre.target_lufs, f"typical {genre.display_name.lower()}"
    ))

    lra = target.loudness.loudness_range_lu
    if lra < genre.lra_low:
        lines.append(
            f"The loudness range of {fmt_signed(lra)} LU is narrower than the "
            f"{int(genre.lra_low)} to {int(genre.lra_high)} LU typical for this genre, "
            "suggesting heavier compression than expected."
        )
    elif lra > genre.lra_high:
        lines.append(
            f"The loudness range of {fmt_signed(lra)} LU is wider than the "
            f"{int(genre.lra_low)} to {int(genre.lra_high)} LU typical for this genre."
        )
    else:
        lines.append(
            f"The loudness range of {fmt_signed(lra)} LU is within the typical "
            f"{int(genre.lra_low)} to {int(genre.lra_high)} LU range for this genre."
        )

    lines.append(f"Typical tonal character for this genre: {genre.notes}.")
    return "\n".join(lines)


def _band_diff_sentence(name: str, a: float, b: float) -> str | None:
    diff = a - b
    if abs(diff) < 2.0:
        return None
    direction = "stronger" if diff > 0 else "weaker"
    return f"The {name} region is about {abs(round(diff, 1))} dB {direction} than the reference."


def build_reference_comparison(target: AnalysisResult, reference: AnalysisResult) -> str:
    lines = [heading("Comparison To Reference File")]
    lines.append(f"Reference filename: {reference.file_info.filename}.")

    # Loudness.
    di = target.loudness.integrated_lufs - reference.loudness.integrated_lufs
    if abs(di) >= 0.5:
        direction = "louder" if di > 0 else "quieter"
        lines.append(
            f"The target is about {abs(round(di, 1))} LU {direction} than the reference. "
            f"Target integrated loudness is {fmt_signed(target.loudness.integrated_lufs)} LUFS, "
            f"reference is {fmt_signed(reference.loudness.integrated_lufs)} LUFS."
        )
    else:
        lines.append("Target and reference integrated loudness are essentially the same.")

    # LRA.
    dlra = target.loudness.loudness_range_lu - reference.loudness.loudness_range_lu
    if abs(dlra) >= 1.0:
        direction = "wider" if dlra > 0 else "narrower"
        lines.append(
            f"The target has a {direction} loudness range than the reference by "
            f"about {abs(round(dlra, 1))} LU."
        )

    # Dynamics.
    dcrest = target.dynamics.crest_factor_db - reference.dynamics.crest_factor_db
    if abs(dcrest) >= 1.5:
        direction = "more dynamic" if dcrest > 0 else "more compressed"
        lines.append(
            f"The target is {direction} than the reference by about "
            f"{abs(round(dcrest, 1))} dB of crest factor."
        )

    # Frequency balance.
    tb = target.spectrum.bands
    rb = reference.spectrum.bands
    band_lines = []
    for name, a, b in (
        ("sub", tb.sub_db, rb.sub_db),
        ("bass", tb.bass_db, rb.bass_db),
        ("low midrange", tb.low_mid_db, rb.low_mid_db),
        ("midrange", tb.mid_db, rb.mid_db),
        ("presence", tb.presence_db, rb.presence_db),
        ("air", tb.air_db, rb.air_db),
    ):
        s = _band_diff_sentence(name, a, b)
        if s:
            band_lines.append(s)
    if not band_lines:
        band_lines.append("The frequency balance is very similar to the reference.")
    lines.extend(band_lines)
    return "\n".join(lines)
