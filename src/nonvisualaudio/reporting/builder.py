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
from nonvisualaudio.reporting.templates import (
    fmt_duration,
    fmt_hz,
    fmt_signed,
    heading,
    paragraph,
)

# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #


def _file_info_section(info: FileInfo) -> str:
    lines = [heading("File Info")]
    lines.append(f"Filename: {info.filename}.")
    lines.append(f"Duration: {fmt_duration(info.duration_seconds)}.")
    lines.append(f"Sample rate: {info.sample_rate} Hz.")
    if info.bit_depth is not None:
        lines.append(f"Bit depth: {info.bit_depth} bit.")
    channels_label = {1: "mono", 2: "stereo"}.get(info.channels, f"{info.channels} channels")
    lines.append(f"Channels: {channels_label}.")
    return "\n".join(lines)


def _loudness_section(loud: LoudnessMetrics) -> str:
    lines = [heading("Loudness Summary")]
    lines.append(f"Integrated loudness: {fmt_signed(loud.integrated_lufs)} LUFS.")
    lines.append(f"Short term peak loudness: {fmt_signed(loud.short_term_max_lufs)} LUFS.")
    lines.append(f"True peak: {fmt_signed(loud.true_peak_dbtp)} dBTP.")
    lines.append(f"Loudness range: {fmt_signed(loud.loudness_range_lu)} LU.")

    # Interpretive sentence.
    i = loud.integrated_lufs
    tp = loud.true_peak_dbtp
    if i > -9.0:
        verdict = "The file is very loud and likely heavily limited."
    elif i > -13.0:
        verdict = "The file is loud, consistent with modern streaming masters."
    elif i > -20.0:
        verdict = "The file sits at a moderate loudness level typical of broadcast material."
    else:
        verdict = "The file is quiet, leaving plenty of headroom."
    lines.append(verdict)

    if tp > -0.5:
        lines.append(
            "True peak is at or above minus 0.5 dBTP, which risks intersample clipping on lossy playback."
        )
    elif tp > -1.0:
        lines.append("True peak is close to full scale.")

    return "\n".join(lines)


def _dynamics_section(dyn: DynamicsMetrics) -> str:
    lines = [heading("Dynamics Summary")]
    lines.append(f"Crest factor: {fmt_signed(dyn.crest_factor_db)} dB.")
    lines.append(f"Dynamic range score: {int(round(dyn.dr_score))}.")

    crest = dyn.crest_factor_db
    if crest < 6.0:
        verdict = (
            "Dynamics are highly compressed with very little headroom between peaks and average level."
        )
    elif crest < 10.0:
        verdict = "Dynamics are moderate, consistent with pop or broadcast mastering."
    elif crest < 14.0:
        verdict = "Dynamics are open and natural."
    else:
        verdict = "The file has a wide, healthy dynamic range."
    lines.append(verdict)
    return "\n".join(lines)


def _band_range_str(low: float, high: float) -> str:
    if low >= 1000:
        return f"{low / 1000:.0f} to {high / 1000:.0f} kHz"
    if high >= 1000:
        return f"{int(low)} Hz to {high / 1000:.0f} kHz"
    return f"{int(low)} to {int(high)} Hz"


def _describe_band_vs_loudest(
    name: str,
    low: float,
    high: float,
    delta_below_loudest_db: float,
    is_quietest: bool,
) -> str:
    """Describe one band as 'X dB quieter than the loudest band'.

    delta_below_loudest_db is always >= 0 (the loudest band has delta 0 and
    is reported separately). We avoid mentioning the loudest band's name on
    every line — the preceding sentence already established it as the
    anchor.
    """
    range_str = _band_range_str(low, high)
    if delta_below_loudest_db < 1.0:
        line = (
            f"The {name} region ({range_str}) is roughly at the same level."
        )
    else:
        line = (
            f"The {name} region ({range_str}) is "
            f"{delta_below_loudest_db:.1f} dB quieter."
        )
    if is_quietest and delta_below_loudest_db >= 1.0:
        # Tag the weakest band so the reader can locate it without math.
        line = line[:-1] + " — the quietest band in this file."
    return line


def _region_for_hz(hz: float) -> str:
    """Human-readable region label for a specific frequency."""
    if hz < 80:
        return "sub bass"
    if hz < 250:
        return "bass"
    if hz < 500:
        return "low midrange"
    if hz < 2000:
        return "midrange"
    if hz < 6000:
        return "presence"
    return "air"


def _interpret_peak(hz: float) -> str:
    """Short note on what a resonance at this frequency typically sounds like."""
    if hz < 80:
        return "low end rumble"
    if hz < 150:
        return "fullness or bloom"
    if hz < 300:
        return "warmth, sometimes muddiness if excessive"
    if hz < 500:
        return "boxiness or a honky, boxy character"
    if hz < 800:
        return "cardboard or hollow colouration"
    if hz < 2000:
        return "nasal or telephone-like character"
    if hz < 4000:
        return "bite, edge, or harshness on vocals"
    if hz < 6000:
        return "sibilance or presence emphasis"
    if hz < 10000:
        return "sibilance and air sheen"
    return "top-end brightness"


_BAND_LABELS: tuple[tuple[str, str, float, float], ...] = (
    ("sub_db", "sub bass", 20.0, 80.0),
    ("bass_db", "bass", 80.0, 250.0),
    ("low_mid_db", "low midrange", 250.0, 500.0),
    ("mid_db", "midrange", 500.0, 2000.0),
    ("presence_db", "presence", 2000.0, 6000.0),
    ("air_db", "air", 6000.0, 20000.0),
)


def _ranked_bands(bands: BandEnergies) -> list[tuple[str, float, float, float]]:
    """Return (name, value_db, low_hz, high_hz) sorted loudest first."""
    entries = [
        (name, getattr(bands, attr), lo, hi)
        for attr, name, lo, hi in _BAND_LABELS
    ]
    entries.sort(key=lambda e: e[1], reverse=True)
    return entries


def _frequency_section(spec: SpectrumMetrics) -> str:
    lines = [heading("Frequency Balance")]
    b = spec.bands
    ranked = _ranked_bands(b)
    loudest_name, loudest_db, loudest_lo, loudest_hi = ranked[0]
    quietest_name, quietest_db, *_ = ranked[-1]
    spread = loudest_db - quietest_db

    # Anchor: name the loudest band once, up front. Every following line
    # is "X dB quieter" against this anchor, which is something the user
    # can hear (the loudest part of the file) rather than an abstract
    # average.
    lines.append(
        f"The loudest band in this file is the {loudest_name} region "
        f"({_band_range_str(loudest_lo, loudest_hi)})."
    )
    # Walk the six bands in the natural low-to-high spectrum order, not
    # ranked, so the listener's mental model matches an EQ plugin.
    for attr, name, lo, hi in _BAND_LABELS:
        value = getattr(b, attr)
        if name == loudest_name:
            continue
        delta = loudest_db - value
        lines.append(
            _describe_band_vs_loudest(
                name, lo, hi, delta, is_quietest=(name == quietest_name)
            )
        )

    # One closing line makes the overall range explicit.
    if spread < 3.0:
        lines.append(
            f"Overall the six bands sit within {spread:.1f} dB of each "
            f"other — a flat, even tonal balance."
        )
    else:
        lines.append(
            f"Total spread between loudest ({loudest_name}) and quietest "
            f"({quietest_name}) band: {spread:.1f} dB."
        )

    # Always enumerate the detected spectral peaks with their exact
    # frequency and prominence, so the user gets actionable numbers
    # instead of vague range descriptions.
    if spec.peaks:
        lines.append("")
        lines.append(
            f"Detected {len(spec.peaks)} prominent spectral "
            f"{'peak' if len(spec.peaks) == 1 else 'peaks'}:"
        )
        # Sort by frequency for a logical low-to-high listing in the report.
        for i, peak in enumerate(sorted(spec.peaks, key=lambda p: p.frequency_hz), 1):
            region = _region_for_hz(peak.frequency_hz)
            character = _interpret_peak(peak.frequency_hz)
            lines.append(
                f"Peak {i}: {fmt_hz(peak.frequency_hz)} "
                f"({region}), about {fmt_signed(peak.prominence_db)} dB "
                f"above the surrounding spectrum. Typically perceived as {character}."
            )
    else:
        lines.append(
            "No narrow resonances stand out above the surrounding spectrum."
        )
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
        return "with a flat tonal balance across all six bands"
    if spread < 8.0:
        return (
            f"with a mild lean toward the {loudest_name} "
            f"(about {spread:.1f} dB louder than the {quietest_name})"
        )
    return (
        f"with a clearly {loudest_name}-heavy tonal balance — "
        f"the {loudest_name} is {spread:.1f} dB louder than the {quietest_name}"
    )


def _overall_section(result: AnalysisResult) -> str:
    lines = [heading("Overall Assessment")]
    parts: list[str] = []
    loud = result.loudness
    dyn = result.dynamics
    bands = result.spectrum.bands

    if loud.integrated_lufs > -10.0 and dyn.crest_factor_db < 8.0:
        parts.append("This is a loud, compressed master with little remaining headroom")
    elif dyn.crest_factor_db >= 12.0:
        parts.append("This is a dynamic recording with natural peaks")
    else:
        parts.append("This file sits in a moderate loudness and dynamics range")

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
    cut_str = f"{cut:.1f}"

    if hz < 50:
        # Rumble, traffic, HVAC, or subsonic DC offset. A high-pass
        # filter clears this more transparently than a notch.
        hpf = max(20, int(hz) + 10)
        return (
            f"The peak at {fmt_hz(hz)} is in the rumble and sub-sonic range, "
            f"often caused by traffic, HVAC noise, footsteps, or DC offset. "
            f"A high pass filter at about {hpf} Hz with a 12 dB per octave "
            f"slope is usually more transparent than a notch cut. Listen for "
            f"unwanted thinning of the bass when you set the cutoff."
        )
    if hz <= 65:
        # Mains hum territory (50 Hz in Europe, 60 Hz in North America).
        return (
            f"The peak at {fmt_hz(hz)} sits on the mains hum frequency. "
            f"Start with a narrow notch, Q about 8, cutting around {cut_str} dB "
            f"at exactly {fmt_hz(hz)}. If the peak is a musical note rather "
            f"than a constant hum, widen the Q to about 3."
        )
    if hz < 120:
        return (
            f"For the low bass buildup at {fmt_hz(hz)}, start with a cut of "
            f"about {cut_str} dB at that frequency, Q around 1.5 (roughly an "
            f"octave wide). That typically tightens the low end without "
            f"thinning the kick or bass."
        )
    if hz < 250:
        return (
            f"To reduce muddiness at {fmt_hz(hz)}, try a cut of about "
            f"{cut_str} dB at that frequency with a medium Q of about 1.5. "
            f"A/B the change on vocal and bass passages to avoid losing "
            f"warmth."
        )
    if hz < 500:
        return (
            f"For the low midrange peak at {fmt_hz(hz)}, start with a cut of "
            f"about {cut_str} dB, Q around 1.2, to open up the midrange and "
            f"reduce boxiness without thinning the bass."
        )
    if hz < 1000:
        return (
            f"The peak at {fmt_hz(hz)} is in the hollow, cardboard range. "
            f"A cut of about {cut_str} dB with Q around 1.5 is a safe starting "
            f"point. If it is room resonance, the same EQ will help most "
            f"material recorded in that room."
        )
    if hz < 2000:
        return (
            f"To tame the nasal or telephone-like quality at {fmt_hz(hz)}, "
            f"try a cut of about {cut_str} dB with Q around 1.5. This region "
            f"is very audible, so make small changes and compare often."
        )
    if hz < 4000:
        return (
            f"For the bite or harshness at {fmt_hz(hz)}, start with a cut of "
            f"about {cut_str} dB, Q around 1.5. On vocals a dynamic EQ often "
            f"works better than a static cut, because this range only gets "
            f"harsh on the loudest syllables."
        )
    if hz < 7000:
        return (
            f"For the sibilance near {fmt_hz(hz)}, a narrow notch with Q "
            f"about 3 and a cut of around {cut_str} dB is a good starting "
            f"point. On vocal material a de-esser tuned to this frequency is "
            f"usually cleaner than a static EQ cut."
        )
    if hz < 10000:
        return (
            f"The peak at {fmt_hz(hz)} is in the upper sibilance and air "
            f"range. Try a cut of about {cut_str} dB, Q around 3, or a "
            f"de-esser tuned to this frequency if it is a vocal problem."
        )
    return (
        f"For the brightness peak at {fmt_hz(hz)}, a high shelf starting one "
        f"octave below, pulled down by about {cut_str} dB, usually sounds "
        f"more natural than a narrow cut at the exact frequency."
    )


def _recommendations_section(result: AnalysisResult) -> str:
    lines = [heading("Recommendations")]
    recs: list[str] = []

    loud = result.loudness
    if loud.true_peak_dbtp > -1.0:
        recs.append(
            "Lower the true peak ceiling to minus 1 dBTP to prevent "
            "intersample clipping. In most brickwall limiters this is the "
            "Ceiling or Output Level parameter."
        )
    if loud.integrated_lufs > -9.0:
        recs.append(
            "Consider reducing limiting to restore some dynamic range if "
            "the target platform allows it."
        )

    # One actionable EQ starting point per detected prominent peak. The
    # peaks are already prominence-filtered upstream, so everything in
    # this list is worth a mention. Sorted low to high so the reader
    # walks the spectrum in one direction.
    for peak in sorted(result.spectrum.peaks, key=lambda p: p.frequency_hz):
        recs.append(_peak_fix_recommendation(peak))

    b = result.spectrum.bands
    if b.air_db < -22.0:
        recs.append(
            "A small shelf boost of about 2 dB above 8 kHz can add clarity "
            "and air."
        )
    if b.sub_db < -25.0:
        recs.append(
            "The very low end is almost absent. If this is music, check "
            "whether the high pass filter on the mix or master bus is set "
            "too high."
        )
    if b.sub_db > -6.0:
        recs.append(
            "The sub bass is dominant. A gentle reduction below 60 Hz with "
            "a high pass filter or a low shelf of about 2 dB usually "
            "improves overall mix clarity."
        )

    if not recs:
        recs.append(
            "No specific corrective actions are obvious from the measurements. "
            "The file appears balanced and well prepared."
        )

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
