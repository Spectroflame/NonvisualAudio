"""Data classes holding the raw measurements for a single file."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LoudnessMetrics:
    integrated_lufs: float         # I (LUFS)
    short_term_max_lufs: float     # highest short-term LUFS reading
    true_peak_dbtp: float          # maximum true peak across channels
    loudness_range_lu: float       # LRA (LU)
    # Position of the loudest true-peak frame, in seconds from the start.
    # None when ffmpeg emitted no usable per-frame peak readings.
    true_peak_time_seconds: float | None = None
    # Project-mode only: the filename of the track that owns the
    # project-wide loudest true peak. ``true_peak_time_seconds`` then
    # refers to the position *inside that track*, not the concatenated
    # project timeline. ``None`` for single-file analyses.
    true_peak_track_filename: str | None = None


@dataclass(frozen=True)
class DynamicsMetrics:
    peak_db: float                 # sample peak, dBFS
    rms_db: float                  # overall RMS, dBFS
    crest_factor_db: float         # peak - rms (dB)
    dr_score: float                # simplified TT-DR


@dataclass(frozen=True)
class BandEnergies:
    sub_db: float       # below 80 Hz
    bass_db: float      # 80-250 Hz
    low_mid_db: float   # 250-500 Hz
    mid_db: float       # 500-2000 Hz
    presence_db: float  # 2000-6000 Hz
    air_db: float       # above 6000 Hz


@dataclass(frozen=True)
class SpectralPeak:
    frequency_hz: float
    prominence_db: float


@dataclass(frozen=True)
class SpectrumMetrics:
    bands: BandEnergies
    peaks: tuple[SpectralPeak, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class StereoMetrics:
    # ``is_stereo`` is False for mono files, multi-channel files we did not
    # split into L/R, or whenever the upstream decoder could not provide a
    # two-channel buffer. In that case every other field carries a sentinel
    # NaN and the report builder skips the verbal verdicts.
    is_stereo: bool
    mean_correlation: float       # energy-weighted Pearson L/R, -1.0..+1.0
    min_correlation: float        # worst per-block correlation
    mono_drop_db: float           # mono-sum vs stereo, dB. 0 ≈ perfect mono compat
    side_to_mid_db: float         # M/S width, dB. Negative = narrow, 0 dB = very wide


def _empty_stereo() -> StereoMetrics:
    """Return a StereoMetrics that means "no stereo measurement available".

    Used as the default for AnalysisResult so legacy fixtures and callers
    that never touched the stereo pipeline keep working unchanged.
    """
    return StereoMetrics(
        is_stereo=False,
        mean_correlation=math.nan,
        min_correlation=math.nan,
        mono_drop_db=math.nan,
        side_to_mid_db=math.nan,
    )


@dataclass(frozen=True)
class FileInfo:
    filename: str
    duration_seconds: float
    sample_rate: int
    channels: int
    bit_depth: int | None


@dataclass(frozen=True)
class AnalysisResult:
    file_info: FileInfo
    loudness: LoudnessMetrics
    dynamics: DynamicsMetrics
    spectrum: SpectrumMetrics
    stereo: StereoMetrics = field(default_factory=_empty_stereo)
