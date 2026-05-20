"""Data classes holding the raw measurements for a single file."""

from __future__ import annotations

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
