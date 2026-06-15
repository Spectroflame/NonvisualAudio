"""Build a screen-reader-friendly text report from an AnalysisResult.

All natural language is generated from deterministic rules — no LLM calls.
The same AnalysisResult always produces the same text, which is an advantage
for screen reader users who rely on predictable structure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable

from nonvisualaudio.analysis.result import (
    AnalysisResult,
    BandEnergies,
    DynamicsMetrics,
    FileInfo,
    LoudnessMetrics,
    SpectralPeak,
    SpectrumMetrics,
    StereoMetrics,
)
from nonvisualaudio.localization import t, t_subject
from nonvisualaudio.reporting.templates import (
    ReportDoc,
    Section,
    band_forms,
    fmt_decimal,
    fmt_duration,
    fmt_peak_time,
    fmt_hz,
    fmt_signed,
    heading_text,
    paragraph,
    t_band,
)


@dataclass(frozen=True)
class ReportSections:
    """Which top-level sections appear in the rendered report.

    Defaults to every section enabled, which is how the report has always
    been built. Callers that want a sliced report (only loudness, only
    spectrum, …) construct an instance with the unwanted sections turned
    off. The helpers ``ReportSections.all()`` and
    ``ReportSections.from_keys()`` are convenience constructors used by
    the UI's section-picker dialog.
    """

    file_info: bool = True
    loudness: bool = True
    dynamics: bool = True
    frequency: bool = True
    overall: bool = True
    comparison: bool = True
    stereo: bool = True
    recommendations: bool = True

    @classmethod
    def all(cls) -> "ReportSections":
        return cls()

    @classmethod
    def none(cls) -> "ReportSections":
        return cls(
            file_info=False,
            loudness=False,
            dynamics=False,
            frequency=False,
            overall=False,
            comparison=False,
            stereo=False,
            recommendations=False,
        )

    @classmethod
    def from_keys(cls, keys: Iterable[str]) -> "ReportSections":
        """Build an instance where only the listed keys are enabled.

        Unknown keys are ignored — that keeps the user-facing dialog
        forward-compatible if a future version adds new sections.
        """
        wanted = {k.strip() for k in keys if k}
        return cls(
            file_info="file_info" in wanted,
            loudness="loudness" in wanted,
            dynamics="dynamics" in wanted,
            frequency="frequency" in wanted,
            overall="overall" in wanted,
            comparison="comparison" in wanted,
            stereo="stereo" in wanted,
            recommendations="recommendations" in wanted,
        )

    def to_keys(self) -> list[str]:
        return [k for k, on in self.as_dict().items() if on]

    def as_dict(self) -> dict[str, bool]:
        return {
            "file_info": self.file_info,
            "loudness": self.loudness,
            "dynamics": self.dynamics,
            "frequency": self.frequency,
            "overall": self.overall,
            "comparison": self.comparison,
            "stereo": self.stereo,
            "recommendations": self.recommendations,
        }

    def any_enabled(self) -> bool:
        return any(self.as_dict().values())

    def with_disabled(self, **flags: bool) -> "ReportSections":
        return replace(self, **flags)


# Stable order for the section picker UI.
SECTION_ORDER: tuple[str, ...] = (
    "file_info",
    "loudness",
    "dynamics",
    "frequency",
    "overall",
    "comparison",
    "stereo",
    "recommendations",
)


# Bands with relative energy below this threshold are reported as
# "effectively silent" rather than with a "X dB quieter than the loudest
# band" sentence. A pure sine tone parks all other bands far below this
# level; a 16-bit master's noise floor sits around here too, so the
# threshold lines up with what a human ear genuinely hears.
SILENT_BAND_THRESHOLD_DB = -90.0

# --------------------------------------------------------------------------- #
# Material context
# --------------------------------------------------------------------------- #

# What kind of material the report should assume. ``"music"`` is the
# historic behaviour and stays the default so direct callers and old
# tests keep their output. The UI layer always passes the value derived
# from the selected profiles (``genre_profiles.material_context_for``):
# ``"neutral"`` when nothing is selected, ``"speech"`` when a speech
# profile is selected.
MATERIAL_MUSIC = "music"
MATERIAL_NEUTRAL = "neutral"
MATERIAL_SPEECH = "speech"

# How the overall verdict reads the tonal balance. ``"full_range"`` is
# the historic music wording where a big loudest-vs-quietest gap is
# called a clear tonal character. ``"speech"`` is declared by spoken-word
# profiles (audio drama, audiobook, podcast): there, mids and lows
# carrying most of the energy IS the expected shape, so the same
# numbers get a calmer reading and the "clearly X-heavy" wording is
# reserved for genuinely profile-atypical extremes. Mirrors
# ``genre_profiles.TONALITY_*``; the UI derives the value via
# ``genre_profiles.tonality_context_for``.
TONALITY_FULL_RANGE = "full_range"
TONALITY_SPEECH = "speech"

# Verdict keys whose reference points (music mixes, streaming masters,
# broadcast levels) only hold when a music profile is selected. Without
# a profile — and with a speech profile — the report must not guess the
# material, so these swap to a ".neutral" catalogue sibling that states
# the same finding without the genre comparison. The measurements and
# bucket thresholds stay identical; only the wording changes.
_GENRE_REFERENCING_KEYS = frozenset(
    {
        "report.loudness.lra.verdict.narrow",
        "report.loudness.lra.verdict.typical",
        "report.loudness.verdict.loud",
        "report.loudness.verdict.moderate",
        "report.dynamics.verdict.moderate",
        "report.overall.loud_compressed",
        "report.stereo.width.typical",
    }
)


def _material_key(
    key: str, material: str, tonality: str = TONALITY_FULL_RANGE
) -> str:
    """Pick the verdict wording that matches the active profile.

    Three voices for the genre-referencing keys (see
    ``_GENRE_REFERENCING_KEYS``):

    - **music** (``material == "music"`` and ``tonality == "full_range"``):
      the historic wording with its music reference points (music mixes,
      streaming masters, …).
    - **spoken-word** (``material == "music"`` and ``tonality ==
      "speech"`` — mastered audio drama, audiobook, podcast): the
      ``.speech`` sibling, which keeps the "finished production" framing
      but swaps the music references for speech ones (broadcast /
      streaming versions of spoken-word productions). Same detector as
      the tonal-balance phrase and the overall all-clear.
    - **neutral** (no profile, or a *raw* speech take where ``material ==
      "speech"``): the ``.neutral`` sibling, which states the finding
      with no material assumption at all — the right reading for raw
      source material that is not yet a master.
    """
    if key not in _GENRE_REFERENCING_KEYS:
        return key
    if material == MATERIAL_MUSIC and tonality == TONALITY_SPEECH:
        return f"{key}.speech"
    if material != MATERIAL_MUSIC:
        return f"{key}.neutral"
    return key

# Neutral-mode interpretation: only descriptive statements, derived from
# robust findings, always closed with a "may be intentional" note.
NEUTRAL_SUB_LOW_DB = -25.0
# A spread of 8 dB marks a "clear tonal character" in the music wording;
# the cautious modes reuse the same boundary for "energy concentrates".
ENERGY_CONCENTRATION_SPREAD_DB = 8.0

# Speech-mode interpretation thresholds, all relative to the midrange
# band (500 Hz – 2 kHz) that anchors the speech body. First guesses,
# pinned by synthetic tests — expect tuning with real material.
SPEECH_SUB_HIGH_REL_DB = -10.0       # sub bass close to mid → rumble suspicion
SPEECH_BASS_LOW_HIGH_REL_DB = -6.0   # 80-150 Hz close to mid → boomy/proximity
SPEECH_BASS_LOW_THIN_REL_DB = -25.0  # 80-150 Hz far below mid → thin
SPEECH_WARMTH_HIGH_REL_DB = -4.0     # 150-250 Hz close to mid → very warm/full
SPEECH_WARMTH_LOW_REL_DB = -25.0     # 150-250 Hz far below mid → lean
SPEECH_MUD_NEAR_MID_DB = 2.0         # 250-500 Hz within 2 dB of mid → boxy
SPEECH_MUD_OVER_PRESENCE_DB = 3.0    # 250-500 Hz well above presence → boxy
SPEECH_PRESENCE_LOW_REL_DB = -10.0   # presence well below mid → check clarity
SPEECH_PRESENCE_HIGH_REL_DB = -3.0   # presence close to mid → harshness possible
SPEECH_SIBILANCE_REL_DB = -8.0       # 6-10 kHz close to mid → sibilance
SPEECH_AIR_LOW_REL_DB = -32.0        # 10-20 kHz far below mid → reduced openness

# Spoken-word tonality (Overall Assessment wording only — no measurement
# or band threshold depends on these). For audio drama / audiobook /
# podcast profiles the verdict treats mids, low mids and bass as the
# natural home of the energy and recessed top bands as unremarkable.
# "Clearly X-heavy" survives only for shapes that are atypical even for
# spoken material:
_SPOKEN_TYPICAL_LOUDEST_KEYS = frozenset({"bass", "low_midrange", "midrange"})
# bass or low mids this far ABOVE the midrange speech body reads as
# boom/mud even with a music bed under the dialogue.
SPOKEN_LOWS_OVER_MID_DB = 6.0
# presence AND air at least this far below the mids → the "presence and
# treble are more restrained" wording is factually backed; otherwise the
# verdict just calls the balance unremarkable for the profile.
SPOKEN_RESTRAINED_TOP_DB = 4.0

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


def _file_info_section(info: FileInfo, *, level: int = 3) -> Section:
    body: list[str] = []
    body.append(t("report.file_info.filename", filename=info.filename))
    body.append(t("report.file_info.duration", duration=fmt_duration(info.duration_seconds)))
    body.append(t("report.file_info.sample_rate", rate=info.sample_rate))
    if info.bit_depth is not None:
        body.append(t("report.file_info.bit_depth", bit=info.bit_depth))
    if info.channels == 1:
        channels_label = t("report.file_info.channels.mono")
    elif info.channels == 2:
        channels_label = t("report.file_info.channels.stereo")
    else:
        channels_label = t("report.file_info.channels.other", n=info.channels)
    body.append(t("report.file_info.channels", label=channels_label))
    return Section(
        level=level,
        heading=heading_text(t("report.heading.file_info"), level=level),
        body=tuple(body),
    )


def _lra_verdict_key(lra: float) -> str:
    """Verbal interpretation of EBU R128 loudness range in LU.

    LRA reads how much *perceived* loudness varies over time, which is
    what most engineers actually mean by "dynamic range". A value of 2
    LU is heavily limited even if the crest factor is high — the
    transients are intact but the macro level barely moves.
    """
    if not math.isfinite(lra):
        return ""
    if lra < 3.0:
        return "report.loudness.lra.verdict.very_narrow"
    if lra < 6.0:
        return "report.loudness.lra.verdict.narrow"
    if lra < 12.0:
        return "report.loudness.lra.verdict.typical"
    return "report.loudness.lra.verdict.wide"


def _loudness_section(
    loud: LoudnessMetrics,
    *,
    material: str = MATERIAL_MUSIC,
    tonality: str = TONALITY_FULL_RANGE,
    project: bool = False,
    level: int = 3,
) -> Section:
    lines: list[str] = []
    lines.append(t("report.loudness.integrated", value=fmt_signed(loud.integrated_lufs)))
    lines.append(t("report.loudness.short_term", value=fmt_signed(loud.short_term_max_lufs)))
    lines.append(t("report.loudness.true_peak", value=fmt_signed(loud.true_peak_dbtp)))
    # Where the loudest peak sits, as a position to jump to in a DAW.
    # In single-file mode that is an in-file H/M/S; in project mode it
    # is the name of the loudest track plus the position inside that
    # track — anything mapped to the concatenated project timeline
    # would map to no single source file and be useless for the user.
    if loud.true_peak_time_seconds is not None:
        if project and loud.true_peak_track_filename:
            lines.append(
                t(
                    "report.loudness.true_peak_time.project",
                    time=fmt_peak_time(loud.true_peak_time_seconds),
                    filename=loud.true_peak_track_filename,
                )
            )
        elif not project:
            lines.append(
                t(
                    "report.loudness.true_peak_time",
                    time=fmt_peak_time(loud.true_peak_time_seconds),
                )
            )
    lines.append(t("report.loudness.lra", value=fmt_signed(loud.loudness_range_lu)))
    lra_verdict_key = _lra_verdict_key(loud.loudness_range_lu)
    if lra_verdict_key:
        lines.append(
            t_subject(
                _material_key(lra_verdict_key, material, tonality), project=project
            )
        )

    # Interpretive sentence.
    i = loud.integrated_lufs
    tp = loud.true_peak_dbtp
    if i > -9.0:
        verdict_key = "report.loudness.verdict.very_loud"
    elif i > -13.0:
        verdict_key = "report.loudness.verdict.loud"
    elif i > -20.0:
        verdict_key = "report.loudness.verdict.moderate"
    else:
        verdict_key = "report.loudness.verdict.quiet"
    lines.append(
        t_subject(_material_key(verdict_key, material, tonality), project=project)
    )

    if tp > -0.5:
        lines.append(t("report.loudness.true_peak.risk"))
    elif tp > -1.0:
        lines.append(t("report.loudness.true_peak.close"))

    return Section(
        level=level,
        heading=heading_text(t("report.heading.loudness"), level=level),
        body=tuple(lines),
    )


def _dynamics_verdict_key(crest: float, lra: float) -> str:
    """Pick a dynamics verdict from crest factor *and* loudness range.

    Looking at crest alone was misleading: a heavily limited dance
    master with snare transients sitting on top can clock 15 dB crest
    while LRA hugs 2 LU. The legacy logic happily called that "wide
    dynamic range". The combined check below keeps both metrics in
    agreement, and adds a dedicated mismatch verdict for the case the
    user actually has on disk: flat macro level, lively transients.

    When LRA is unknown (NaN — happens for very short or silent files)
    the function falls back to the crest-only thresholds rather than
    silently mis-categorising the file.
    """
    if not math.isfinite(lra):
        if crest < 6.0:
            return "report.dynamics.verdict.compressed"
        if crest < 10.0:
            return "report.dynamics.verdict.moderate"
        if crest < 14.0:
            return "report.dynamics.verdict.natural"
        return "report.dynamics.verdict.wide"

    if lra < 3.0 and crest >= 12.0:
        return "report.dynamics.verdict.compressed_lively"
    if lra < 3.0 or crest < 6.0:
        return "report.dynamics.verdict.compressed"
    if lra >= 12.0 and crest >= 14.0:
        return "report.dynamics.verdict.wide"
    if lra >= 6.0 and crest >= 10.0:
        return "report.dynamics.verdict.natural"
    return "report.dynamics.verdict.moderate"


def _dynamics_section(
    dyn: DynamicsMetrics,
    loud: LoudnessMetrics,
    *,
    material: str = MATERIAL_MUSIC,
    tonality: str = TONALITY_FULL_RANGE,
    project: bool = False,
    level: int = 3,
) -> Section:
    lines: list[str] = []
    lines.append(t("report.dynamics.crest", value=fmt_signed(dyn.crest_factor_db)))
    lines.append(t("report.dynamics.dr_score", score=int(round(dyn.dr_score))))

    verdict_key = _dynamics_verdict_key(dyn.crest_factor_db, loud.loudness_range_lu)
    lines.append(
        t_subject(_material_key(verdict_key, material, tonality), project=project)
    )
    return Section(
        level=level,
        heading=heading_text(t("report.heading.dynamics"), level=level),
        body=tuple(lines),
    )


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
    band_key: str,
    range_str: str,
    delta_below_loudest_db: float,
    is_quietest: bool,
    *,
    project: bool = False,
) -> str:
    """Describe one band as 'X dB quieter than the loudest band'.

    delta_below_loudest_db is always >= 0 (the loudest band has delta 0 and
    is reported separately). We avoid mentioning the loudest band's name on
    every line — the preceding sentence already established it as the
    anchor.
    """
    if delta_below_loudest_db < 1.0:
        line = t_band("report.freq.band_same_level", band_key, range=range_str)
    else:
        line = t_band(
            "report.freq.band_quieter",
            band_key,
            range=range_str,
            delta=fmt_decimal(delta_below_loudest_db),
        )
    if is_quietest and delta_below_loudest_db >= 1.0:
        # Drop the trailing period, append the quietest-band tag, add a period.
        line = line[:-1] + t_subject(
            "report.freq.quietest_suffix", project=project
        ) + "."
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


def _neutral_findings(bands: BandEnergies) -> list[str]:
    """Cautious, material-agnostic observations for profile-free runs.

    Only descriptive statements derived from robust numbers — no genre
    or speech assumptions, no hint to pick a profile. Closed with a
    "may be intentional" note whenever anything fired.
    """
    lines: list[str] = []
    ranked = _ranked_bands(bands)
    audible = [e for e in ranked if e[1] >= SILENT_BAND_THRESHOLD_DB]
    if bands.sub_db < NEUTRAL_SUB_LOW_DB:
        lines.append(t("report.freq.neutral.sub_low"))
    if len(audible) >= 2:
        spread = audible[0][1] - audible[-1][1]
        if spread >= ENERGY_CONCENTRATION_SPREAD_DB:
            lines.append(
                t("report.freq.neutral.concentration", **band_forms(audible[0][4]))
            )
    if lines:
        lines.append(t("report.freq.neutral.material_note"))
    return lines


@dataclass(frozen=True)
class _SpeechFinding:
    """One speech-mode observation, tagged with the public band it
    belongs to so a narrow peak inside the same band can be merged into
    a combined "recessed overall, but narrow peak at X" sentence."""

    line: str
    band_lo_hz: float | None = None
    band_hi_hz: float | None = None
    band_key: str | None = None
    recessed: bool = False


def _speech_band_findings(bands: BandEnergies) -> list[_SpeechFinding]:
    """Band observations for raw speech material.

    All thresholds are relative to the midrange band (500 Hz – 2 kHz),
    the anchor that carries the speech body. Sub-band values may be
    ``None`` on legacy fixtures; those findings are skipped then. A low
    sub bass is deliberately NOT a finding — that is normal for speech.
    """
    mid = bands.mid_db
    findings: list[_SpeechFinding] = []

    if bands.sub_db > mid + SPEECH_SUB_HIGH_REL_DB:
        findings.append(
            _SpeechFinding(line=t("report.freq.speech.sub_high"))
        )

    if bands.bass_low_db is not None:
        if bands.bass_low_db > mid + SPEECH_BASS_LOW_HIGH_REL_DB:
            findings.append(
                _SpeechFinding(line=t("report.freq.speech.lowbass_boomy"))
            )
        elif bands.bass_low_db < mid + SPEECH_BASS_LOW_THIN_REL_DB:
            findings.append(
                _SpeechFinding(line=t("report.freq.speech.lowbass_thin"))
            )

    if bands.bass_high_db is not None:
        if bands.bass_high_db > mid + SPEECH_WARMTH_HIGH_REL_DB:
            findings.append(
                _SpeechFinding(line=t("report.freq.speech.warmth_high"))
            )
        elif bands.bass_high_db < mid + SPEECH_WARMTH_LOW_REL_DB:
            findings.append(
                _SpeechFinding(line=t("report.freq.speech.warmth_low"))
            )

    if (
        bands.low_mid_db >= mid - SPEECH_MUD_NEAR_MID_DB
        or bands.low_mid_db > bands.presence_db + SPEECH_MUD_OVER_PRESENCE_DB
    ):
        findings.append(_SpeechFinding(line=t("report.freq.speech.mud")))

    if bands.presence_db < mid + SPEECH_PRESENCE_LOW_REL_DB:
        findings.append(
            _SpeechFinding(
                line=t("report.freq.speech.presence_low"),
                band_lo_hz=2000.0,
                band_hi_hz=6000.0,
                band_key="presence",
                recessed=True,
            )
        )
    elif bands.presence_db > mid + SPEECH_PRESENCE_HIGH_REL_DB:
        findings.append(
            _SpeechFinding(line=t("report.freq.speech.presence_high"))
        )

    if (
        bands.air_low_db is not None
        and bands.air_low_db > mid + SPEECH_SIBILANCE_REL_DB
    ):
        findings.append(
            _SpeechFinding(line=t("report.freq.speech.sibilance"))
        )

    if (
        bands.air_high_db is not None
        and bands.air_high_db < mid + SPEECH_AIR_LOW_REL_DB
        and bands.air_high_db >= SILENT_BAND_THRESHOLD_DB
    ):
        findings.append(
            _SpeechFinding(
                line=t("report.freq.speech.air_note"),
                band_lo_hz=10000.0,
                band_hi_hz=20000.0,
                band_key="air",
                recessed=True,
            )
        )

    return findings


def _speech_lines(
    bands: BandEnergies, peaks: tuple[SpectralPeak, ...]
) -> list[str]:
    """Render the speech interpretation block.

    Broadband tonality and narrow resonances are kept apart: when a
    band is recessed overall but contains a detected narrow peak, both
    facts go into ONE combined sentence so the report explains the
    broadband state and the outlier together.
    """
    lines = [t("report.freq.speech.body")]
    for finding in _speech_band_findings(bands):
        peak_inside = None
        if finding.recessed and finding.band_lo_hz is not None:
            for peak in sorted(peaks, key=lambda p: p.frequency_hz):
                if finding.band_lo_hz <= peak.frequency_hz < finding.band_hi_hz:
                    peak_inside = peak
                    break
        if peak_inside is not None:
            lines.append(
                t_band(
                    "report.freq.speech.recessed_with_peak",
                    finding.band_key,
                    hz=fmt_hz(peak_inside.frequency_hz),
                )
            )
        else:
            lines.append(finding.line)
    return lines


def _frequency_section(
    spec: SpectrumMetrics,
    *,
    material: str = MATERIAL_MUSIC,
    project: bool = False,
    level: int = 3,
) -> Section:
    lines: list[str] = []
    b = spec.bands
    ranked = _ranked_bands(b)
    loudest_name, loudest_db, loudest_lo, loudest_hi, loudest_key = ranked[0]

    # Split the non-loudest bands into "audible" and "silent" groups so a
    # pure-tone-style signal doesn't produce a wall of "120 dB quieter"
    # sentences — those numbers carry no information once a band drops
    # below human hearing. The dominant band is never grouped as silent
    # even if its absolute level happens to fall under the threshold;
    # that's a fully-silent file, where the existing "below 3 dB spread"
    # path already speaks sensibly.
    others = ranked[1:]
    audible_others = [
        entry for entry in others if entry[1] >= SILENT_BAND_THRESHOLD_DB
    ]
    silent_others = [
        entry for entry in others if entry[1] < SILENT_BAND_THRESHOLD_DB
    ]
    quietest_audible_name = audible_others[-1][0] if audible_others else None

    # Anchor: name the loudest band once, up front. Every following line
    # is "X dB quieter" against this anchor, which is something the user
    # can hear (the loudest part of the file) rather than an abstract
    # average.
    lines.append(
        t_band(
            "report.freq.loudest_anchor",
            loudest_key,
            project=project,
            range=_band_range_str(loudest_lo, loudest_hi),
        )
    )
    # List the audible non-loudest bands in order of loudness
    # (next-loudest first), so each row shows a small step down from
    # the one above. Walking in spectrum order instead makes the deltas
    # jump around, which is harder to follow when read aloud.
    for name, value, lo, hi, key in audible_others:
        delta = loudest_db - value
        lines.append(
            _describe_band_vs_loudest(
                key,
                _band_range_str(lo, hi),
                delta,
                is_quietest=(name == quietest_audible_name),
                project=project,
            )
        )

    # Collapse all silent bands into a single sentence at the end of the
    # band listing. Joining with ", " keeps a screen reader's natural
    # pause structure intact across both German and English.
    if silent_others:
        silent_names = ", ".join(name for name, *_ in silent_others)
        key = (
            "report.freq.silent_bands.one"
            if len(silent_others) == 1
            else "report.freq.silent_bands.other"
        )
        lines.append(
            t(
                key,
                threshold=int(SILENT_BAND_THRESHOLD_DB),
                names=silent_names,
            )
        )

    # Closing line. Three cases:
    #   - No audible bands besides the loudest → a sine-tone-style
    #     signal: name the dominant band explicitly instead of quoting
    #     a meaningless spread number against a silent floor.
    #   - All bands within 3 dB → "flat, even balance".
    #   - Otherwise → quote the spread between loudest and quietest
    #     *audible* band; the silent ones already got their own line.
    if not audible_others:
        lines.append(
            t("report.freq.single_band_dominant", **band_forms(loudest_key))
        )
    else:
        quietest_audible_db = audible_others[-1][1]
        spread = loudest_db - quietest_audible_db
        if spread < 3.0:
            lines.append(t("report.freq.spread_flat", spread=fmt_decimal(spread)))
        else:
            lines.append(
                t(
                    "report.freq.spread_total",
                    loudest=loudest_name,
                    quietest=quietest_audible_name,
                    spread=fmt_decimal(spread),
                )
            )

    # Interpretation block. The factual lines above are identical in
    # every material mode; only the reading of those numbers differs.
    # Music mode appends nothing — that is the historic output.
    if material == MATERIAL_NEUTRAL:
        neutral_lines = _neutral_findings(b)
        if neutral_lines:
            lines.append("")
            lines.extend(neutral_lines)
    elif material == MATERIAL_SPEECH:
        lines.append("")
        lines.extend(_speech_lines(b, spec.peaks))

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
    return Section(
        level=level,
        heading=heading_text(t("report.heading.frequency"), level=level),
        body=tuple(lines),
    )


def _spoken_tonality_atypical(bands: BandEnergies) -> bool:
    """True when the band shape is extreme even for spoken-word material.

    Mids in front, lows close behind, top end clearly quieter — that is
    the normal shape of an audio drama or audiobook mix and must not be
    judged. Atypical means: a band outside the speech body is the
    loudest one (sub bass booming, presence/air screaming), or the lows
    sit well ABOVE the mids that carry the dialogue.
    """
    ranked = _ranked_bands(bands)
    if ranked[0][4] not in _SPOKEN_TYPICAL_LOUDEST_KEYS:
        return True
    return (
        max(bands.bass_db, bands.low_mid_db) - bands.mid_db
        > SPOKEN_LOWS_OVER_MID_DB
    )


def _tonal_balance_phrase(
    bands: BandEnergies,
    material: str = MATERIAL_MUSIC,
    tonality: str = TONALITY_FULL_RANGE,
) -> str:
    """Summarize tonal balance in one everyday-language sentence fragment."""
    ranked = _ranked_bands(bands)
    loudest_key = ranked[0][4]
    quietest_key = ranked[-1][4]
    spread = ranked[0][1] - ranked[-1][1]

    # Under 3 dB nobody would call this tilted; 3-8 dB is a mild lean you
    # may or may not notice; 8 dB or more is a clear tonal character.
    if spread < 3.0:
        return t("report.tonal.flat")

    # Without a profile (and for speech material) a loud-band-vs-quiet-
    # band gap is not a verdict: a speech take with hardly any sub bass
    # is perfectly normal. State where the energy sits, name no culprit,
    # and skip the "clearly X-heavy" judgement entirely.
    if material in (MATERIAL_NEUTRAL, MATERIAL_SPEECH):
        return t(
            "report.tonal.cautious",
            spread=fmt_decimal(spread),
            **band_forms(loudest_key),
        )

    # Spoken-word profile (audio drama, audiobook, podcast): the music
    # wording below would call the normal speech shape "clearly
    # mid-heavy" with a dramatic dB quote. As long as the shape is
    # typical for the profile, describe it as such; the full numbers
    # stay in the Frequency Balance section. Atypical extremes fall
    # through to the clear music wording on purpose.
    if tonality == TONALITY_SPEECH and not _spoken_tonality_atypical(bands):
        top_restrained = (
            bands.presence_db <= bands.mid_db - SPOKEN_RESTRAINED_TOP_DB
            and bands.air_db <= bands.mid_db - SPOKEN_RESTRAINED_TOP_DB
        )
        if top_restrained:
            return t("report.tonal.speech_centered")
        return t("report.tonal.speech_plain")

    quietest_nom = band_forms(quietest_key)["nom"]
    if spread < 8.0:
        return t(
            "report.tonal.mild_lean",
            spread=fmt_decimal(spread),
            quietest_nom=quietest_nom,
            **band_forms(loudest_key),
        )
    return t_band(
        "report.tonal.heavy",
        loudest_key,
        spread=fmt_decimal(spread),
        quietest_nom=quietest_nom,
    )


def _overall_section(
    result: AnalysisResult,
    *,
    material: str = MATERIAL_MUSIC,
    tonality: str = TONALITY_FULL_RANGE,
    project: bool = False,
    level: int = 3,
) -> Section:
    parts: list[str] = []
    loud = result.loudness
    dyn = result.dynamics
    bands = result.spectrum.bands

    lra = loud.loudness_range_lu
    lra_low = math.isfinite(lra) and lra < 4.0
    lra_wide = math.isfinite(lra) and lra >= 8.0
    # The overall verdict guards against the same crest-vs-LRA mismatch
    # as the dynamics block: a loud master with a low LRA stays in the
    # "loud and compressed" bucket even when transients are still alive,
    # and a "dynamic" verdict requires both crest *and* LRA to be open.
    if loud.integrated_lufs > -10.0 and (dyn.crest_factor_db < 8.0 or lra_low):
        verdict_key = "report.overall.loud_compressed"
    elif dyn.crest_factor_db >= 12.0 and lra_wide:
        verdict_key = "report.overall.dynamic"
    else:
        verdict_key = "report.overall.moderate"
    parts.append(
        t_subject(_material_key(verdict_key, material, tonality), project=project)
    )

    parts.append(_tonal_balance_phrase(bands, material, tonality))

    # Spoken-word profiles get a short all-clear when the spectrum
    # analysis found no narrow resonances either — for dialogue mixes
    # that absence is the actual quality signal, not a flat band
    # distribution. Kept to one calm fragment; the cautious modes
    # (neutral / raw speech) stay without it on purpose.
    if (
        tonality == TONALITY_SPEECH
        and material == MATERIAL_MUSIC
        and not result.spectrum.peaks
        and not _spoken_tonality_atypical(bands)
    ):
        parts.append(t("report.tonal.speech_no_resonances"))

    return Section(
        level=level,
        heading=heading_text(t("report.heading.overall"), level=level),
        body=(paragraph(", ".join(parts)),),
    )


# Worst-block reporting. The minimum per-block correlation only earns
# its own line when it sits at least this far below the mean — otherwise
# it is just measurement noise around the value we already printed.
_STEREO_WORST_BLOCK_MARGIN = 0.05
# When the mean correlation is at least this healthy AND the mono drop
# stays above this (small) level, a single bad block reads as a calm
# check hint rather than a phase warning — the worst block is then most
# likely a short, wide passage, not a structural problem.
_STEREO_CALM_MEAN_CORR = 0.5
_STEREO_CALM_MONO_DROP_DB = -1.0


def _stereo_correlation_verdict_key(corr: float) -> str:
    if corr > 0.9:
        return "report.stereo.verdict.very_narrow"
    if corr > 0.5:
        return "report.stereo.verdict.natural"
    if corr > 0.2:
        return "report.stereo.verdict.wide"
    if corr > -0.2:
        return "report.stereo.verdict.very_wide"
    return "report.stereo.verdict.out_of_phase"


def _stereo_mono_verdict_key(mono_drop_db: float) -> str:
    # mono_drop_db is 0 for perfectly correlated stereo and grows
    # increasingly negative as cancellations kick in.
    if mono_drop_db > -0.5:
        return "report.stereo.mono.compatible"
    if mono_drop_db > -3.0:
        return "report.stereo.mono.moderate"
    return "report.stereo.mono.problematic"


def _stereo_width_verdict_key(side_to_mid_db: float) -> str:
    if side_to_mid_db < -12.0:
        return "report.stereo.width.narrow"
    if side_to_mid_db < -3.0:
        return "report.stereo.width.typical"
    return "report.stereo.width.very_wide"


def _stereo_section(
    stereo: StereoMetrics,
    channels: int,
    *,
    material: str = MATERIAL_MUSIC,
    tonality: str = TONALITY_FULL_RANGE,
    project: bool = False,
    level: int = 3,
) -> Section:
    heading_line = heading_text(t("report.heading.stereo"), level=level)
    lines: list[str] = []
    if not stereo.is_stereo:
        # Mono input, mixed mono/stereo project, or an unanalysable
        # buffer — keep the section visible (so the screen-reader
        # navigation stays the same) but say there is nothing to read.
        if channels == 1:
            lines.append(t_subject("report.stereo.mono_file", project=project))
        else:
            lines.append(t_subject("report.stereo.not_available", project=project))
        return Section(level=level, heading=heading_line, body=tuple(lines))

    lines.append(
        t(
            "report.stereo.correlation",
            value=fmt_signed(stereo.mean_correlation, 2),
        )
    )
    if stereo.min_correlation < stereo.mean_correlation - _STEREO_WORST_BLOCK_MARGIN:
        # The worst block diverges noticeably from the mean — surface the
        # minimum and where it sits, otherwise the single number can hide
        # a problem passage. In project mode the position maps to the
        # concatenated timeline rather than a single source file, so we
        # drop the timestamp there, mirroring the true-peak line.
        worst_time = stereo.min_correlation_time_seconds
        if not project and worst_time is not None:
            lines.append(
                t(
                    "report.stereo.correlation_worst_at",
                    value=fmt_signed(stereo.min_correlation, 2),
                    time=fmt_peak_time(worst_time),
                )
            )
        else:
            lines.append(
                t(
                    "report.stereo.correlation_worst",
                    value=fmt_signed(stereo.min_correlation, 2),
                )
            )
        # A single bad block against a healthy mean and a small mono drop
        # is most likely a short wide passage — phrase it as a check hint
        # rather than dramatising a phase fault.
        calm = (
            stereo.mean_correlation > _STEREO_CALM_MEAN_CORR
            and stereo.mono_drop_db > _STEREO_CALM_MONO_DROP_DB
        )
        lines.append(
            t(
                "report.stereo.correlation_worst_hint.calm"
                if calm
                else "report.stereo.correlation_worst_hint"
            )
        )
    lines.append(
        t("report.stereo.mono_drop", value=fmt_signed(stereo.mono_drop_db))
    )
    lines.append(
        t("report.stereo.side_to_mid", value=fmt_signed(stereo.side_to_mid_db))
    )

    lines.append(
        t_subject(_stereo_correlation_verdict_key(stereo.mean_correlation), project=project)
    )
    lines.append(
        t_subject(_stereo_mono_verdict_key(stereo.mono_drop_db), project=project)
    )
    lines.append(
        t_subject(
            _material_key(
                _stereo_width_verdict_key(stereo.side_to_mid_db), material, tonality
            ),
            project=project,
        )
    )
    return Section(level=level, heading=heading_line, body=tuple(lines))


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


def _recommendations_section(
    result: AnalysisResult,
    *,
    material: str = MATERIAL_MUSIC,
    project: bool = False,
    level: int = 3,
) -> Section:
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
    if material == MATERIAL_MUSIC:
        # These three assume mastered music ("the very low end is almost
        # absent" is no problem at all on a speech take). Without a
        # profile we must not guess the material, so they stay
        # music-only.
        if b.air_db < -22.0:
            recs.append(t("report.rec.air_boost"))
        if b.sub_db < -25.0:
            recs.append(t("report.rec.sub_absent"))
        if b.sub_db > -6.0:
            recs.append(t("report.rec.sub_dominant"))
    elif material == MATERIAL_SPEECH:
        # Speech recommendations mirror the speech findings in the
        # frequency section — same thresholds, so report text and
        # action options never contradict each other.
        mid = b.mid_db
        if b.sub_db > mid + SPEECH_SUB_HIGH_REL_DB:
            recs.append(t("report.rec.speech.rumble"))
        if (
            b.low_mid_db >= mid - SPEECH_MUD_NEAR_MID_DB
            or b.low_mid_db > b.presence_db + SPEECH_MUD_OVER_PRESENCE_DB
        ):
            recs.append(t("report.rec.speech.mud"))
        if (
            b.air_low_db is not None
            and b.air_low_db > mid + SPEECH_SIBILANCE_REL_DB
        ):
            recs.append(t("report.rec.speech.sibilance"))

    stereo = result.stereo
    if stereo.is_stereo:
        if stereo.mean_correlation < 0.3:
            recs.append(
                t(
                    "report.rec.stereo_correlation_low",
                    value=fmt_signed(stereo.mean_correlation, 2),
                )
            )
        if stereo.mono_drop_db < -3.0:
            recs.append(
                t(
                    "report.rec.stereo_mono_drop",
                    value=fmt_signed(stereo.mono_drop_db),
                )
            )

    # When nothing in the analysis needs corrective attention we drop
    # the section heading entirely and just leave the "all good"
    # sentence in the flow. Reading a "Possible Action Options" header
    # followed by "nothing to do" felt like noise — the user asked for
    # the bare sentence instead, which is what every export format now
    # gets. A headingless :class:`Section` carries the sentence through
    # the structured pipeline without contributing an extra <h2>/<h3>.
    if not recs:
        return Section(
            level=level,
            heading=None,
            body=(t_subject("report.rec.none", project=project),),
        )

    return Section(
        level=level,
        heading=heading_text(t("report.heading.recommendations"), level=level),
        body=tuple(recs),
    )


# --------------------------------------------------------------------------- #
# Top-level
# --------------------------------------------------------------------------- #


def build_report(
    result: AnalysisResult,
    extra_sections: Iterable[Section] | None = None,
    sections: ReportSections | None = None,
    project: bool = False,
    *,
    material: str = MATERIAL_MUSIC,
    tonality: str = TONALITY_FULL_RANGE,
    title: str | None = None,
    title_level: int = 1,
    section_level: int = 2,
) -> ReportDoc:
    """Return the full report as a structured :class:`ReportDoc`.

    ``extra_sections`` are :class:`Section` objects appended after
    Overall Assessment and before Recommendations, used for Genre or
    Reference comparison output.

    ``material`` selects how the measurements are interpreted:
    ``"music"`` (default — the historic wording, kept for direct
    callers and old tests), ``"neutral"`` (no profile selected:
    technical facts plus cautious, material-agnostic notes), or
    ``"speech"`` (a speech profile is selected: speech-aware band
    interpretation, no music-style sub-bass judgements). Outside the
    frequency section, the non-music modes also strip the music /
    streaming / broadcast reference points from the loudness, dynamics,
    overall and stereo verdicts (see ``_GENRE_REFERENCING_KEYS``) —
    without a profile those comparisons would be guesses about the
    material. The UI layer derives this via
    ``genre_profiles.material_context_for`` and always passes it
    explicitly.

    ``tonality`` only affects the one-sentence tonal phrasing in the
    Overall Assessment: ``"full_range"`` (default) keeps the historic
    music wording, ``"speech"`` — declared by spoken-word profiles such
    as audio drama, audiobook or podcast — reads the same band numbers
    against the expected speech shape and reserves the "clearly X-heavy"
    wording for profile-atypical extremes. Derived by the UI layer via
    ``genre_profiles.tonality_context_for``.

    ``sections`` controls which top-level blocks are rendered. Default is
    every section, which preserves the historical behaviour. When the
    user picks a slice (for example only loudness and dynamics), the
    skipped sections are simply omitted — the surviving blocks keep
    their wording and order so screen-reader navigation stays the same.

    ``project`` swaps the subject in verdict-style sentences from "the
    file" to "the project" — the project-mode pipeline passes ``True``
    so the report addresses the whole bundle instead of pretending it
    is a single bounced file.

    Heading levels:

      - ``title``: when given, a heading at ``title_level`` (default 1,
        i.e. ``<h1>``) is prepended to the report. Single-file runs
        pass the filename here; the worker's multi-file batch passes a
        per-file "Track X" wrapper at ``title_level=2``; project-mode
        passes ``None`` because the project header is already emitted
        outside this function.
      - ``section_level`` (default 2): heading level used for every
        per-section block (File Info, Loudness, …). Project-mode and
        single-file mode keep this at 2; the worker's multi-file batch
        pushes it down to 3 so each file's sections nest under the
        per-file ``<h2>`` wrapper.
    """
    selected = sections if sections is not None else ReportSections.all()

    out: list[Section] = []
    if title is not None:
        out.append(
            Section(
                level=title_level,
                heading=heading_text(title, level=title_level),
                body=(),
            )
        )
    if selected.file_info:
        out.append(_file_info_section(result.file_info, level=section_level))
    if selected.loudness:
        out.append(
            _loudness_section(
                result.loudness,
                material=material,
                tonality=tonality,
                project=project,
                level=section_level,
            )
        )
    if selected.dynamics:
        out.append(
            _dynamics_section(
                result.dynamics,
                result.loudness,
                material=material,
                tonality=tonality,
                project=project,
                level=section_level,
            )
        )
    if selected.frequency:
        out.append(
            _frequency_section(
                result.spectrum,
                material=material,
                project=project,
                level=section_level,
            )
        )
    if selected.overall:
        out.append(
            _overall_section(
                result,
                material=material,
                tonality=tonality,
                project=project,
                level=section_level,
            )
        )
    if selected.comparison and extra_sections:
        out.extend(s for s in extra_sections if s is not None)
    if selected.stereo:
        out.append(
            _stereo_section(
                result.stereo,
                result.file_info.channels,
                material=material,
                tonality=tonality,
                project=project,
                level=section_level,
            )
        )
    if selected.recommendations:
        out.append(
            _recommendations_section(
                result, material=material, project=project, level=section_level
            )
        )
    return ReportDoc(sections=tuple(out))
