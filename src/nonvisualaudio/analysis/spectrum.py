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
from scipy import fft as scipy_fft
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

PEAK_PROMINENCE_DB = 4.0
# Reference smoothing spans ~1/3 octave on a log-frequency axis; at the bottom
# of the analysis range that means roughly ±5 bins, at the top many more.
SMOOTHING_OCTAVE_FRACTION = 1.0 / 3.0
# Lower limit for peak reporting. Real low-frequency issues (rumble around
# 40 Hz, AC hum at 50/60 Hz) must still surface — the phantom "always 43 Hz"
# bug was caused by zero-padded mean smoothing at the FFT edge, which is
# already fixed by the log-frequency median reference below.
PEAK_FREQ_LOW_HZ = 40.0
PEAK_FREQ_HIGH_HZ = 8000.0
# Minimum spacing between two reported peaks, in octaves. Peaks closer than
# this to a stronger one are suppressed so we don't list three variants of the
# same resonance.
PEAK_MIN_SEPARATION_OCTAVES = 1.0 / 3.0
# Upper bound on how many peaks we ever report, to keep the natural-language
# report readable. This is NOT a default — if only two peaks clear the
# prominence threshold, only two are reported.
MAX_REPORTED_PEAKS = 6
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


def _log_frequency_reference(freqs: np.ndarray, psd_db: np.ndarray) -> np.ndarray:
    """Estimate the local spectral baseline on a log-frequency axis.

    For each bin i above the analysis floor, take the median of all bins
    within ±SMOOTHING_OCTAVE_FRACTION octaves. Median (not mean) keeps narrow
    peaks from contaminating their own reference, so a real resonance shows
    up with the full excess we want to detect.
    """
    ref = np.copy(psd_db)
    # Everything below PEAK_FREQ_LOW_HZ stays at its own psd_db — we don't
    # evaluate peaks there anyway, and using it as context would be fine.
    lo_factor = 2.0 ** (-SMOOTHING_OCTAVE_FRACTION)
    hi_factor = 2.0 ** (+SMOOTHING_OCTAVE_FRACTION)
    for i in range(len(freqs)):
        f = freqs[i]
        if f <= 0.0:
            continue
        # Window in Hz, converted to the bin range via searchsorted.
        lo = f * lo_factor
        hi = f * hi_factor
        start = int(np.searchsorted(freqs, lo, side="left"))
        end = int(np.searchsorted(freqs, hi, side="right"))
        if end - start < 3:
            start = max(0, i - 1)
            end = min(len(freqs), i + 2)
        ref[i] = np.median(psd_db[start:end])
    return ref


def _find_peaks_db(freqs: np.ndarray, psd: np.ndarray) -> tuple[SpectralPeak, ...]:
    # Work in dB so prominence thresholds are perceptually reasonable.
    psd_safe = np.where(psd > 0.0, psd, 1e-20)
    psd_db = 10.0 * np.log10(psd_safe)
    reference = _log_frequency_reference(freqs, psd_db)
    excess = psd_db - reference

    in_range_mask = (freqs >= PEAK_FREQ_LOW_HZ) & (freqs <= PEAK_FREQ_HIGH_HZ)

    # scipy.signal.find_peaks enforces "local maximum" semantics, so a plateau
    # of adjacent bins above threshold collapses to one peak naturally.
    # `distance` is in bins; pick enough to span ~1/6 octave near the low
    # end of the analysis range.
    bin_width = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 1.0
    min_distance_bins = max(
        2,
        int(round((PEAK_FREQ_LOW_HZ * (2.0 ** (1.0 / 6.0) - 1.0)) / bin_width)),
    )
    peak_idx, _ = signal.find_peaks(
        excess,
        height=PEAK_PROMINENCE_DB,
        distance=min_distance_bins,
    )
    peak_idx = peak_idx[in_range_mask[peak_idx]]
    if peak_idx.size == 0:
        return ()

    # Sort candidates by prominence (strongest first), then apply a
    # 1/3-octave exclusion zone around each kept peak. This prevents two
    # bins of the same resonance from both being reported.
    ordered = sorted(
        ((float(freqs[i]), float(excess[i])) for i in peak_idx),
        key=lambda fe: fe[1],
        reverse=True,
    )
    sep = 2.0 ** PEAK_MIN_SEPARATION_OCTAVES
    kept: list[tuple[float, float]] = []
    for freq, prom in ordered:
        if all(
            (max(freq, kf) / min(freq, kf)) >= sep for kf, _ in kept
        ):
            kept.append((freq, prom))
        if len(kept) >= MAX_REPORTED_PEAKS:
            break

    return tuple(
        SpectralPeak(
            frequency_hz=round(freq, 1),
            prominence_db=round(prom, 2),
        )
        for freq, prom in kept
    )


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
    # Welch runs many independent FFTs; pocketfft can parallelise them
    # across CPU cores when we hand it ``workers=-1`` via the context
    # manager. For a 9-hour file that is hundreds of thousands of FFTs,
    # so this is a real speed-up on multi-core machines. The output is
    # bit-identical to the single-worker run — only the wall time
    # changes.
    with scipy_fft.set_workers(-1):
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
