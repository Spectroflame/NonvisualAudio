"""Tests for :class:`SpectrumStreamer`.

The streamer must reproduce ``compute_spectrum``'s output regardless of
how the signal is split into ``feed`` calls. We compare band energies
and peak frequencies/prominences to the batch path on multiple inputs
(sines, mixed tones over pink noise, sub-segment buffers) and across a
range of feed-chunk sizes.
"""

from __future__ import annotations

import numpy as np
import pytest

from nonvisualaudio.analysis.spectrum import (
    SpectrumStreamer,
    compute_spectrum,
)


SR = 48000


def _sine(freq: float, seconds: float = 2.0, sr: int = SR) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _pink_noise(seconds: float, sr: int, seed: int = 0) -> np.ndarray:
    """Pink (1/f) noise via Voss–McCartney. Borrowed from test_spectrum."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    rows = 16
    source = rng.standard_normal((rows, n))
    for r in range(rows):
        step = 1 << r
        if step > 1:
            source[r, :] = np.repeat(source[r, ::step], step)[:n]
    x = source.sum(axis=0)
    x /= np.max(np.abs(x)) + 1e-12
    return (0.3 * x).astype(np.float32)


def _stream(samples: np.ndarray, sample_rate: int, chunk_size: int):
    s = SpectrumStreamer(sample_rate)
    for start in range(0, samples.size, chunk_size):
        s.feed(samples[start : start + chunk_size])
    return s.finalize()


def _assert_bands_match(streamed, batch, *, abs_tol: float = 0.01):
    """Band energies in dB; tolerance is well below the report's
    rounding resolution of 0.01 dB.
    """
    for field in (
        "sub_db",
        "bass_db",
        "low_mid_db",
        "mid_db",
        "presence_db",
        "air_db",
    ):
        assert getattr(streamed.bands, field) == pytest.approx(
            getattr(batch.bands, field), abs=abs_tol
        ), f"{field}: streamed={getattr(streamed.bands, field)} batch={getattr(batch.bands, field)}"


def _assert_peaks_match(streamed, batch, *, freq_tol_hz: float = 0.5):
    """Peak frequencies + prominences must match in order and value.
    Slight float noise in the trailing decimal can flip the rounded
    representation, so we compare numerically.
    """
    assert len(streamed.peaks) == len(batch.peaks), (
        f"peak count differs — streamed={[(p.frequency_hz, p.prominence_db) for p in streamed.peaks]} "
        f"batch={[(p.frequency_hz, p.prominence_db) for p in batch.peaks]}"
    )
    for s_peak, b_peak in zip(streamed.peaks, batch.peaks):
        assert s_peak.frequency_hz == pytest.approx(
            b_peak.frequency_hz, abs=freq_tol_hz
        )
        assert s_peak.prominence_db == pytest.approx(
            b_peak.prominence_db, abs=0.1
        )


def test_streamer_matches_batch_on_pink_noise():
    x = _pink_noise(3.0, SR, seed=3)
    batch = compute_spectrum(x, SR)
    streamed = _stream(x, SR, chunk_size=4096)
    _assert_bands_match(streamed, batch)
    _assert_peaks_match(streamed, batch)


def test_streamer_matches_batch_on_two_tones_over_pink_noise():
    sr = SR
    t = np.arange(sr * 3) / sr
    tones = (
        0.3 * np.sin(2 * np.pi * 500.0 * t)
        + 0.3 * np.sin(2 * np.pi * 3000.0 * t)
    )
    bg = _pink_noise(3.0, sr, seed=3)[: len(tones)]
    x = (tones + 0.3 * bg).astype(np.float32)

    batch = compute_spectrum(x, sr)
    streamed = _stream(x, sr, chunk_size=8192)
    _assert_bands_match(streamed, batch)
    _assert_peaks_match(streamed, batch)


def test_streamer_output_invariant_across_chunk_sizes():
    """The streamer should produce the same output regardless of how the
    feed is sliced. ``nperseg`` is 4096 with 50 % overlap (hop=2048), so
    chunk sizes that don't align with either still have to stitch
    segment boundaries via the carry buffer.
    """
    rng = np.random.default_rng(5)
    n = 5 * SR
    x = (rng.standard_normal(n) * 0.2).astype(np.float32)

    baseline = _stream(x, SR, chunk_size=n)  # one big chunk
    for chunk_size in (1023, 2048, 3000, 4096, 7919, 50_000):
        result = _stream(x, SR, chunk_size=chunk_size)
        _assert_bands_match(result, baseline, abs_tol=1e-6)
        # Peaks: bins are determined by FFT; the same FFT inputs across
        # different feed slicings must produce identical peak frequencies
        # and (within tiny float noise) prominences.
        _assert_peaks_match(result, baseline, freq_tol_hz=0.01)


def test_segment_boundary_split_across_feeds_is_reconstructed():
    """Feed cuts placed *inside* a windowed segment force the carry path
    to stitch the segment back together. The segmentation pattern must
    end up identical to scipy.welch's, so peaks and bands must match
    the batch result bit-for-bit on the displayed precision.
    """
    # 4096 + 2048 = 6144 samples → exactly 2 segments at offsets 0 and 2048
    # with the production nperseg/noverlap. We split the feed inside the
    # second segment so the carry has to glue 1024 samples in.
    n = 6144
    rng = np.random.default_rng(8)
    x = (rng.standard_normal(n) * 0.3).astype(np.float32)

    batch = compute_spectrum(x, SR)
    s = SpectrumStreamer(SR)
    s.feed(x[: 4096 + 1024])  # carry of 1024 samples afterwards
    s.feed(x[4096 + 1024 :])
    streamed = s.finalize()

    _assert_bands_match(streamed, batch, abs_tol=1e-6)
    _assert_peaks_match(streamed, batch, freq_tol_hz=0.01)


def test_mid_sine_dominates_mid_band_via_stream():
    x = _sine(1000.0)
    s = _stream(x, SR, chunk_size=4096)
    b = s.bands
    assert b.mid_db > b.sub_db
    assert b.mid_db > b.bass_db
    assert b.mid_db > b.air_db


def test_low_mid_sine_near_440_produces_peak_in_region():
    x = _sine(440.0)
    s = _stream(x, SR, chunk_size=4096)
    assert s.peaks, "expected at least one spectral peak"
    top = s.peaks[0]
    assert 380.0 < top.frequency_hz < 520.0


def test_empty_input_returns_silent_bands_via_stream():
    s = SpectrumStreamer(SR).finalize()
    assert s.peaks == ()
    assert s.bands.mid_db <= -100.0


def test_sub_segment_input_falls_back_to_batch_path():
    """Input shorter than nperseg (4096) takes the small-buffer branch
    in both paths, and the small-buffer branch in the streamer just
    delegates to scipy.signal.welch — so the outputs must match.
    """
    # 2000 samples is well under nperseg=4096.
    t = np.arange(2000) / SR
    x = (0.5 * np.sin(2 * np.pi * 1000.0 * t)).astype(np.float32)

    batch = compute_spectrum(x, SR)
    streamed = _stream(x, SR, chunk_size=137)
    _assert_bands_match(streamed, batch, abs_tol=1e-6)
    _assert_peaks_match(streamed, batch, freq_tol_hz=0.01)


def test_finalize_is_idempotent():
    rng = np.random.default_rng(21)
    x = (rng.standard_normal(3 * SR) * 0.2).astype(np.float32)
    s = SpectrumStreamer(SR)
    for start in range(0, x.size, 5000):
        s.feed(x[start : start + 5000])
    a = s.finalize()
    b = s.finalize()
    assert a == b


def test_empty_feed_is_a_noop():
    rng = np.random.default_rng(0)
    x = (rng.standard_normal(2 * SR) * 0.2).astype(np.float32)

    s_a = SpectrumStreamer(SR)
    s_a.feed(x)
    a = s_a.finalize()

    s_b = SpectrumStreamer(SR)
    s_b.feed(np.zeros(0, dtype=np.float32))
    s_b.feed(x[: x.size // 2])
    s_b.feed(np.zeros(0, dtype=np.float32))
    s_b.feed(x[x.size // 2 :])
    s_b.feed(np.zeros(0, dtype=np.float32))
    b = s_b.finalize()

    _assert_bands_match(a, b, abs_tol=1e-6)
    _assert_peaks_match(a, b, freq_tol_hz=0.01)


def test_streamer_matches_batch_on_exactly_one_segment_input():
    """Edge case: input is exactly nperseg samples long → exactly one
    segment, no overlap. Both paths should produce the same PSD; the
    streamer takes the multi-segment branch (n_segments=1, no fallback).
    """
    rng = np.random.default_rng(33)
    x = (rng.standard_normal(4096) * 0.2).astype(np.float32)
    batch = compute_spectrum(x, SR)
    streamed = _stream(x, SR, chunk_size=4096)
    _assert_bands_match(streamed, batch, abs_tol=1e-6)
    _assert_peaks_match(streamed, batch, freq_tol_hz=0.01)


def test_streamer_matches_batch_for_44100_sample_rate():
    """Sample-rate parametrisation: the bin layout depends on ``sr``, so
    a non-48k rate exercises a different ``rfftfreq`` and the band
    boundaries land on different bins. Both paths must still agree.
    """
    sr = 44100
    x = _pink_noise(3.0, sr, seed=2)
    batch = compute_spectrum(x, sr)
    streamed = SpectrumStreamer(sr)
    for start in range(0, x.size, 6000):
        streamed.feed(x[start : start + 6000])
    out = streamed.finalize()
    _assert_bands_match(out, batch)
    _assert_peaks_match(out, batch)
