"""Build a screen-reader-friendly text report from an AnalysisResult.

All natural language is generated from deterministic rules — no LLM calls.
The same AnalysisResult always produces the same text, which is an advantage
for screen reader users who rely on predictable structure.
"""

from __future__ import annotations

from nonvisualaudio.analysis.result import (
    AnalysisResult,
    BandEnergies,
    DynamicsMetrics,
    FileInfo,
    LoudnessMetrics,
    SpectralPeak,
    SpectrumMetrics,
)
from nonvisualaudio.localization import t
from nonvisualaudio.reporting.templates import (
    fmt_decimal,
    fmt_duration,
    fmt_hz,
    fmt_signed,
    heading,
    paragraph,
)

# --------------------------------------------------------------------------- #
# Band tables
# --------------------------------------------------------------------------- #

# (attribute name, catalog key suffix, low Hz, high Hz)
_BAND_LABELS: tuple[tuple[str, str, float, float], ...] = (
    ("sub_db", "sub_bass", 20.0, 80.0),
    ("bass_db", "bass", 80.0, 250.0),
    ("low_mid_db", "low_midrange", 250.0, 500.0),
    ("mid_db", "midrange", 500.0, 2000.0),
    ("presence_db", "presence", 2000.0, 6000.0),
    ("air_db", "air", 6000.0, 20000.0),
)


def _band_label(key_suffix: str) -> str:
    return t(f"report.band.{key_suffix}")


def _region_label(key_suffix: str) -> str:
    return t(f"report.region.{key_suffix}")


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #


def _file_info_section(info: FileInfo) -> str:
    lines = [heading(t("report.heading.file_info"))]
    lines.append(t("report.file_info.filename", filename=info.filename))
    lines.append(t("report.file_info.duration", duration=fmt_duration(info.duration_seconds)))
    lines.append(t("report.file_info.sample_rate", rate=info.sample_rate))
    if info.bit_depth is not None:
        lines.append(t("report.file_info.bit_depth", bit=info.bit_depth))
    if info.channels == 1:
        channels_label = t("report.file_info.channels.mono")
    elif info.channels == 2:
        channels_label = t("report.file_info.channels.stereo")
    else:
        channels_label = t("report.file_info.channels.other", n=info.channels)
    lines.append(t("report.file_info.channels", label=channels_label))
    return "\n".join(lines)


def _loudness_section(loud: LoudnessMetrics) -> str:
    lines = [heading(t("report.heading.loudness"))]
    lines.append(t("report.loudness.integrated", value=fmt_signed(loud.integrated_lufs)))
    lines.append(t("report.loudness.short_term", value=fmt_signed(loud.short_term_max_lufs)))
    lines.append(t("report.loudness.true_peak", value=fmt_signed(loud.true_peak_dbtp)))
    lines.append(t("report.loudness.lra", value=fmt_signed(loud.loudness_range_lu)))

    # Interpretive sentence.
    i = loud.integrated_lufs
    tp = loud.true_peak_dbtp
    if i > -9.0:
        verdict = t("report.loudness.verdict.very_loud")
    elif i > -13.0:
        verdict = t("report.loudness.verdict.loud")
    elif i > -20.0:
        verdict = t("report.loudness.verdict.moderate")
    else:
        verdict = t("report.loudness.verdict.quiet")
    lines.append(verdict)

    if tp > -0.5:
        lines.append(t("report.loudness.true_peak.risk"))
    elif tp > -1.0:
        lines.append(t("report.loudness.true_peak.close"))

    return "\n".join(lines)


def _dynamics_section(dyn: DynamicsMetrics) -> str:
    lines = [heading(t("report.heading.dynamics"))]
    lines.append(t("report.dynamics.crest", value=fmt_signed(dyn.crest_factor_db)))
    lines.append(t("report.dynamics.dr_score", score=int(round(dyn.dr_score))))

    crest = dyn.crest_factor_db
    if crest < 6.0:
        verdict = t("report.dynamics.verdict.compressed")
    elif crest < 10.0:
        verdict = t("report.dynamics.verdict.moderate")
    elif crest < 14.0:
        verdict = t("report.dynamics.verdict.natural")
    else:
        verdict = t("report.dynamics.verdict.wide")
    lines.append(verdict)
    return "\n".join(lines)


def _band_range_str(low: float, high: float) -> str:
    if low >= 1000:
        return t(
            "report.band_range.khz",
            low=f"{low / 1000:.0f}",
            high=f"{high / 1000:.0f}",
        )
    if high >= 1000:
        return t(
            "report.band_range.mixed",
            low=int(low),
            high=f"{high / 1000:.0f}",
        )
    return t("report.band_range.hz", low=int(low), high=int(high))


def _describe_band_vs_loudest(
    name: str,
    range_str: str,
    delta_below_loudest_db: float,
    is_quietest: bool,
) -> str:
    """Describe one band as 'X dB quieter than the loudest band'.

    delta_below_loudest_db is always >= 0 (the loudest band has delta 0 and
    is reported separately). We avoid mentioning the loudest band's name on
    every line — the preceding sentence already established it as the
    anchor.
    """
    if delta_below_loudest_db < 1.0:
        line = t("report.freq.band_same_level", name=name, range=range_str)
    else:
        line = t(
            "report.freq.band_quieter",
            name=name,
            range=range_str,
            delta=fmt_decimal(delta_below_loudest_db),
        )
    if is_quietest and delta_below_loudest_db >= 1.0:
        # Drop the trailing period, append the quietest-band tag, add a period.
        line = line[:-1] + t("report.freq.quietest_suffix") + "."
    return line


def _region_for_hz(hz: float) -> str:
    """Human-readable region label for a specific frequency."""
    if hz < 80:
        return _region_label("sub_bass")
    if hz < 250:
        return _region_label("bass")
    if hz < 500:
        return _region_label("low_midrange")
    if hz < 2000:
        return _region_label("midrange")
    if hz < 6000:
        return _region_label("presence")
    return _region_label("air")


def _interpret_peak(hz: float) -> str:
    """Short note on what a resonance at this frequency typically sounds like."""
    if hz < 80:
        return t("report.peak_char.rumble")
    if hz < 150:
        return t("report.peak_char.bloom")
    if hz < 300:
        return t("report.peak_char.warmth")
    if hz < 500:
        return t("report.peak_char.boxiness")
    if hz < 800:
        return t("report.peak_char.hollow")
    if hz < 2000:
        return t("report.peak_char.nasal")
    if hz < 4000:
        return t("report.peak_char.bite")
    if hz < 6000:
        return t("report.peak_char.sibilance")
    if hz < 10000:
        return t("report.peak_char.air_sheen")
    return t("report.peak_char.top_end")


def _ranked_bands(bands: BandEnergies) -> list[tuple[str, float, float, float, str]]:
    """Return (localised_name, value_db, low_hz, high_hz, key_suffix) sorted loudest first."""
    entries = [
        (_band_label(key), getattr(bands, attr), lo, hi, key)
        for attr, key, lo, hi in _BAND_LABELS
    ]
    entries.sort(key=lambda e: e[1], reverse=True)
    return entries


def _frequency_section(spec: SpectrumMetrics) -> str:
    lines = [heading(t("report.heading.frequency"))]
    b = spec.bands
    ranked = _ranked_bands(b)
    loudest_name, loudest_db, loudest_lo, loudest_hi, _ = ranked[0]
    quietest_name, quietest_db, *_ = ranked[-1]
    spread = loudest_db - quietest_db

    # Anchor: name the loudest band once, up front. Every following line
    # is "X dB quieter" against this anchor, which is something the user
    # can hear (the loudest part of the file) rather than an abstract
    # average.
    lines.append(
        t(
            "report.freq.loudest_anchor",
            name=loudest_name,
            range=_band_range_str(loudest_lo, loudest_hi),
        )
    )
    # List the other bands in order of loudness (next-loudest first),
    # so each row shows a small step down from the one above. Walking in
    # spectrum order instead makes the deltas jump around (e.g. 14 dB,
    # then 1 dB, then 9 dB), which is harder to follow when read aloud.
    for name, value, lo, hi, _ in ranked[1:]:
        delta = loudest_db - value
        lines.append(
            _describe_band_vs_loudest(
                name,
                _band_range_str(lo, hi),
                delta,
                is_quietest=(name == quietest_name),
            )
        )

    # One closing line makes the overall range explicit.
    if spread < 3.0:
        lines.append(t("report.freq.spread_flat", spread=fmt_decimal(spread)))
    else:
        lines.append(
            t(
                "report.freq.spread_total",
                loudest=loudest_name,
                quietest=quietest_name,
                spread=fmt_decimal(spread),
            )
        )

    # Always enumerate the detected spectral peaks with their exact
    # frequency and prominence, so the user gets actionable numbers
    # instead of vague range descriptions.
    if spec.peaks:
        lines.append("")
        key = (
            "report.freq.peaks_detected.one"
            if len(spec.peaks) == 1
            else "report.freq.peaks_detected.other"
        )
        lines.append(t(key, count=len(spec.peaks)))
        # Sort by frequency for a logical low-to-high listing in the report.
        for i, peak in enumerate(sorted(spec.peaks, key=lambda p: p.frequency_hz), 1):
            region = _region_for_hz(peak.frequency_hz)
            character = _interpret_peak(peak.frequency_hz)
            lines.append(
                t(
                    "report.freq.peak_line",
                    index=i,
                    hz=fmt_hz(peak.frequency_hz),
                    region=region,
                    prominence=fmt_signed(peak.prominence_db),
                    character=character,
                )
            )
    else:
        lines.append(t("report.freq.no_peaks"))
    return "\n".join(lines)


def _tonal_balance_phrase(bands: BandEnergies) -> str:
    """Summarize tonal balance in one everyday-language sentence fragment."""
    ranked = _ranked_bands(bands)
    loudest_name = ranked[0][0]
    quietest_name = ranked[-1][0]
    spread = ranked[0][1] - ranked[-1][1]

    # Under 3 dB nobody would call this tilted; 3-8 dB is a mild lean you
    # may or may not notice; 8 dB or more is a clear tonal character.
    if spread < 3.0:
        return t("report.tonal.flat")
    if spread < 8.0:
        return t(
            "report.tonal.mild_lean",
            name=loudest_name,
            spread=fmt_decimal(spread),
            quietest=quietest_name,
        )
    return t(
        "report.tonal.heavy",
        name=loudest_name,
        spread=fmt_decimal(spread),
        quietest=quietest_name,
    )


def _overall_section(result: AnalysisResult) -> str:
    lines = [heading(t("report.heading.overall"))]
    parts: list[str] = []
    loud = result.loudness
    dyn = result.dynamics
    bands = result.spectrum.bands

    if loud.integrated_lufs > -10.0 and dyn.crest_factor_db < 8.0:
        parts.append(t("report.overall.loud_compressed"))
    elif dyn.crest_factor_db >= 12.0:
        parts.append(t("report.overall.dynamic"))
    else:
        parts.append(t("report.overall.moderate"))

    parts.append(_tonal_balance_phrase(bands))

    lines.append(paragraph(", ".join(parts)))
    return "\n".join(lines)


def _peak_fix_recommendation(peak: SpectralPeak) -> str:
    """Turn a detected spectral peak into a concrete EQ starting point.

    The wording names the frequency in Hz, proposes a cut amount derived
    from the peak's prominence (roughly half, clamped to 1.5–4 dB), and
    picks a Q value and technique suited to the frequency region — a
    narrow notch for mains hum, a broad bell for midrange issues, a
    shelf or de-esser for the top end, and a high pass filter for
    sub-50 Hz rumble.
    """
    hz = peak.frequency_hz
    # Half the prominence is a safe first move; clamp so a 10 dB peak
    # does not immediately propose a surgical -5 dB cut.
    cut = max(1.5, min(4.0, round(peak.prominence_db * 0.5, 1)))
    cut_str = fmt_decimal(cut)
    hz_str = fmt_hz(hz)

    if hz < 50:
        hpf = max(20, int(hz) + 10)
        return t("report.peak_fix.rumble", hz=hz_str, hpf=hpf)
    if hz <= 65:
        return t("report.peak_fix.mains", hz=hz_str, cut=cut_str)
    if hz < 120:
        return t("report.peak_fix.low_bass", hz=hz_str, cut=cut_str)
    if hz < 250:
        return t("report.peak_fix.muddiness", hz=hz_str, cut=cut_str)
    if hz < 500:
        return t("report.peak_fix.low_mid", hz=hz_str, cut=cut_str)
    if hz < 1000:
        return t("report.peak_fix.hollow", hz=hz_str, cut=cut_str)
    if hz < 2000:
        return t("report.peak_fix.nasal", hz=hz_str, cut=cut_str)
    if hz < 4000:
        return t("report.peak_fix.bite", hz=hz_str, cut=cut_str)
    if hz < 7000:
        return t("report.peak_fix.sibilance", hz=hz_str, cut=cut_str)
    if hz < 10000:
        return t("report.peak_fix.upper_sibilance", hz=hz_str, cut=cut_str)
    return t("report.peak_fix.brightness", hz=hz_str, cut=cut_str)


def _recommendations_section(result: AnalysisResult) -> str:
    lines = [heading(t("report.heading.recommendations"))]
    recs: list[str] = []

    loud = result.loudness
    if loud.true_peak_dbtp > -1.0:
        recs.append(t("report.rec.true_peak"))
    if loud.integrated_lufs > -9.0:
        recs.append(t("report.rec.limit_less"))

    # One actionable EQ starting point per detected prominent peak. The
    # peaks are already prominence-filtered upstream, so everything in
    # this list is worth a mention. Sorted low to high so the reader
    # walks the spectrum in one direction.
    for peak in sorted(result.spectrum.peaks, key=lambda p: p.frequency_hz):
        recs.append(_peak_fix_recommendation(peak))

    b = result.spectrum.bands
    if b.air_db < -22.0:
        recs.append(t("report.rec.air_boost"))
    if b.sub_db < -25.0:
        recs.append(t("report.rec.sub_absent"))
    if b.sub_db > -6.0:
        recs.append(t("report.rec.sub_dominant"))

    if not recs:
        recs.append(t("report.rec.none"))

    lines.extend(recs)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Top-level
# --------------------------------------------------------------------------- #


def build_report(result: AnalysisResult, extra_sections: list[str] | None = None) -> str:
    """Return the full report as a single plain-text string.

    ``extra_sections`` are appended after Overall Assessment and before
    Recommendations, used for Genre or Reference comparison output.
    """
    sections = [
        _file_info_section(result.file_info),
        _loudness_section(result.loudness),
        _dynamics_section(result.dynamics),
        _frequency_section(result.spectrum),
    ]
    sections.append(_overall_section(result))
    if extra_sections:
        sections.extend(s for s in extra_sections if s)
    sections.append(_recommendations_section(result))
    return "\n\n".join(sections) + "\n"
