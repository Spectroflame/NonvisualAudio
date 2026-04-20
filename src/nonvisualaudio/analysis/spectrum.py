"""Frequency-domain analysis using Welch's method.

Produces:
- Energy per frequency band in dB (relative to full scale).
- Notable spectral peaks as (frequency_hz, prominence_db) tuples.

Peaks are detected as bins that exceed a smoothed-neighborhood estimate by
at least ``PEAK_PROMINENCE_DB``. This gives us specific frequencies to cite
in the natural-language report ("a buildup around 480 Hz").
"""

from __future__ import annotations

import math

import numpy as np
from scipy import signal

from nonvisualaudio.analysis.result import BandEnergies, SpectralPeak, SpectrumMetrics

BAND_EDGES: tuple[tuple[str, float, float], ...] = (
    ("sub", 20.0, 80.0),
    ("bass", 80.0, 250.0),
    ("low_mid", 250.0, 500.0),
    ("mid", 500.0, 2000.0),
    ("presence", 2000.0, 6000.0),
    ("air", 6000.0, 20000.0),
)

PEAK_PROMINENCE_DB = 3.5
SMOOTHING_BINS = 9  # odd number, for neighborhood reference
SILENCE_FLOOR_DB = -120.0


def _band_energy_db(freqs: np.ndarray, psd: np.ndarray, f_low: float, f_high: float) -> float:
    mask = (freqs >= f_low) & (freqs < f_high)
    if not np.any(mask):
        return SILENCE_FLOOR_DB
    # Integrate PSD across the band, then convert to dB.
    # np.trapezoid was np.trapz before NumPy 2.0.
    _trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))
    energy = float(_trapz(psd[mask], freqs[mask]))
    if energy <= 0.0 or not math.isfinite(energy):
        return SILENCE_FLOOR_DB
    # Reference energy: total in-band across 20 Hz – Nyquist, so dB is
    # relative to full-spectrum energy. This gives comparable numbers across
    # files regardless of level.
    total_mask = (freqs >= 20.0) & (freqs <= freqs[-1])
    total = float(_trapz(psd[total_mask], freqs[total_mask]))
    if total <= 0.0:
        return SILENCE_FLOOR_DB
    return 10.0 * math.log10(energy / total)


def _smoothed(x: np.ndarray, window: int) -> np.ndarray:
    if window < 3:
        return x
    kernel = np.ones(window, dtype=np.float64) / float(window)
    return np.convolve(x, kernel, mode="same")


def _find_peaks_db(freqs: np.ndarray, psd: np.ndarray) -> tuple[SpectralPeak, ...]:
    # Work in dB so prominence thresholds are perceptually reasonable.
    psd_safe = np.where(psd > 0.0, psd, 1e-20)
    psd_db = 10.0 * np.log10(psd_safe)
    reference = _smoothed(psd_db, SMOOTHING_BINS)
    excess = psd_db - reference

    # Only consider bins between 40 Hz and 8 kHz for peak reporting.
    in_range = (freqs >= 40.0) & (freqs <= 8000.0)
    candidate_idx = np.where(in_range & (excess > PEAK_PROMINENCE_DB))[0]
    if candidate_idx.size == 0:
        return ()

    # Collapse clusters of adjacent candidates into single peaks.
    peaks: list[SpectralPeak] = []
    cluster_start = candidate_idx[0]
    prev = cluster_start
    for i in candidate_idx[1:]:
        if i - prev > 2:
            cluster = np.arange(cluster_start, prev + 1)
            best = cluster[np.argmax(excess[cluster])]
            peaks.append(
                SpectralPeak(
                    frequency_hz=float(freqs[best]),
                    prominence_db=float(round(excess[best], 2)),
                )
            )
            cluster_start = i
        prev = i
    cluster = np.arange(cluster_start, prev + 1)
    best = cluster[np.argmax(excess[cluster])]
    peaks.append(
        SpectralPeak(
            frequency_hz=float(freqs[best]),
            prominence_db=float(round(excess[best], 2)),
        )
    )

    # Keep the 4 most prominent peaks.
    peaks.sort(key=lambda p: p.prominence_db, reverse=True)
    return tuple(peaks[:4])


def compute_spectrum(samples: np.ndarray, sample_rate: int) -> SpectrumMetrics:
    if samples.size == 0 or sample_rate <= 0:
        empty = BandEnergies(
            sub_db=SILENCE_FLOOR_DB,
            bass_db=SILENCE_FLOOR_DB,
            low_mid_db=SILENCE_FLOOR_DB,
            mid_db=SILENCE_FLOOR_DB,
            presence_db=SILENCE_FLOOR_DB,
            air_db=SILENCE_FLOOR_DB,
        )
        return SpectrumMetrics(bands=empty, peaks=())

    nperseg = 4096 if samples.size >= 4096 else samples.size
    freqs, psd = signal.welch(
        samples.astype(np.float64, copy=False),
        fs=sample_rate,
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg // 2,
        scaling="density",
        detrend=False,
    )

    band_values: dict[str, float] = {}
    for name, lo, hi in BAND_EDGES:
        hi_clamped = min(hi, float(freqs[-1]))
        band_values[name] = round(_band_energy_db(freqs, psd, lo, hi_clamped), 2)

    bands = BandEnergies(
        sub_db=band_values["sub"],
        bass_db=band_values["bass"],
        low_mid_db=band_values["low_mid"],
        mid_db=band_values["mid"],
        presence_db=band_values["presence"],
        air_db=band_values["air"],
    )
    peaks = _find_peaks_db(freqs, psd)
    return SpectrumMetrics(bands=bands, peaks=peaks)
