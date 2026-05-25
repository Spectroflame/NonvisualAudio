"""Tests for the stereo-image analysis module."""

from __future__ import annotations

import math

import numpy as np
import pytest

from nonvisualaudio.analysis.stereo import compute_stereo


SR = 48000


def _sine(seconds: float, freq: float = 1000.0, amplitude: float = 0.5) -> np.ndarray:
    n = int(seconds * SR)
    t = np.arange(n, dtype=np.float64) / SR
    return (amplitude * np.sin(2.0 * math.pi * freq * t)).astype(np.float32)


def _stack(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return np.column_stack((left, right)).astype(np.float32)


def test_none_input_returns_not_stereo():
    metrics = compute_stereo(None, SR)
    assert metrics.is_stereo is False


def test_empty_buffer_returns_not_stereo():
    empty = np.zeros((0, 2), dtype=np.float32)
    metrics = compute_stereo(empty, SR)
    assert metrics.is_stereo is False


def test_mono_input_returns_not_stereo():
    # Single-column buffer is not a stereo file.
    mono = np.zeros((SR, 1), dtype=np.float32)
    metrics = compute_stereo(mono, SR)
    assert metrics.is_stereo is False


def test_silent_stereo_returns_not_stereo():
    # All zeros: no meaningful stereo statement, must not pretend to
    # measure correlation. The whole-file RMS guard kicks in.
    metrics = compute_stereo(np.zeros((SR, 2), dtype=np.float32), SR)
    assert metrics.is_stereo is False


def test_identical_channels_give_full_correlation_and_zero_drop():
    sig = _sine(1.0)
    metrics = compute_stereo(_stack(sig, sig), SR)
    assert metrics.is_stereo is True
    assert metrics.mean_correlation == pytest.approx(1.0, abs=0.01)
    assert metrics.min_correlation == pytest.approx(1.0, abs=0.01)
    # L == R: summing to mono does not change level → 0 dB drop.
    assert metrics.mono_drop_db == pytest.approx(0.0, abs=0.1)
    # Side energy is zero → side/mid ratio dives to the silence floor.
    assert metrics.side_to_mid_db < -60.0


def test_antiphase_signal_is_phase_critical():
    sig = _sine(1.0)
    metrics = compute_stereo(_stack(sig, -sig), SR)
    assert metrics.is_stereo is True
    assert metrics.mean_correlation == pytest.approx(-1.0, abs=0.01)
    # Mono sum cancels almost completely → very deep negative drop.
    assert metrics.mono_drop_db < -40.0
    # Side energy dominates: mid is silent.
    assert metrics.side_to_mid_db > 0.0


def test_uncorrelated_noise_lands_near_zero_correlation():
    rng = np.random.default_rng(42)
    left = rng.normal(0.0, 0.3, size=SR).astype(np.float32)
    right = rng.normal(0.0, 0.3, size=SR).astype(np.float32)
    metrics = compute_stereo(_stack(left, right), SR)
    assert metrics.is_stereo is True
    assert abs(metrics.mean_correlation) < 0.2
    # Equal-power uncorrelated noise sums to roughly the same level as
    # the equal-power reference (within a couple dB on a 1-second slice).
    assert -5.0 < metrics.mono_drop_db < 1.0


def test_narrow_signal_with_quiet_side_yields_narrow_width():
    # L = main tone; R = same tone scaled to 0.95 → barely-different
    # side signal → narrow width.
    sig = _sine(1.0)
    metrics = compute_stereo(_stack(sig, sig * 0.95), SR)
    assert metrics.is_stereo is True
    # mostly the same → narrow side/mid ratio, well below -3 dB.
    assert metrics.side_to_mid_db < -12.0
    # correlation should still be ~1.
    assert metrics.mean_correlation > 0.99


# --------------------------------------------------------------------------- #
# Chunking correctness — verifies the streaming pass does not introduce
# artefacts at chunk boundaries. The user specifically asked about this
# because long audiobooks (9 h+) cross hundreds of chunks.
# --------------------------------------------------------------------------- #


def _force_chunk_size(monkeypatch, blocks_per_chunk):
    """Patch the module-level chunk size so tests can exercise the
    multi-chunk path without generating gigabytes of synthetic audio."""
    from nonvisualaudio.analysis import stereo as stereo_mod

    monkeypatch.setattr(stereo_mod, "_BLOCKS_PER_CHUNK", blocks_per_chunk)


def test_chunking_invariant_across_chunk_sizes(monkeypatch):
    """Metrics must not depend on how the signal is sliced into chunks.

    We run the same input with several different ``_BLOCKS_PER_CHUNK``
    values; every chunk size partitions the signal at exact block
    boundaries, so all four output metrics must match to within float
    rounding plus the final 2-decimal display rounding.
    """
    rng = np.random.default_rng(1234)
    # 5 s × 48 kHz = 240 000 samples = 50 correlation blocks. Plenty of
    # room for chunk_size=1 (50 chunks), 7 (8 chunks), 1024 (single chunk).
    n = 5 * SR
    left = (rng.standard_normal(n) * 0.1).astype(np.float32)
    right = (rng.standard_normal(n) * 0.1).astype(np.float32)
    stereo = _stack(left, right)

    results = []
    for chunk_size in (1, 3, 7, 17, 1024):
        _force_chunk_size(monkeypatch, chunk_size)
        results.append(compute_stereo(stereo, SR))

    base = results[0]
    for chunk_size, r in zip((1, 3, 7, 17, 1024), results):
        # All four metrics are rounded to 2-3 decimal places before
        # being returned, so cross-chunk-size differences in the
        # underlying float64 accumulation are well below the display
        # resolution.
        assert r.mean_correlation == pytest.approx(base.mean_correlation, abs=1e-6), (
            f"mean_correlation diverged at chunk_size={chunk_size}"
        )
        assert r.min_correlation == pytest.approx(base.min_correlation, abs=1e-6), (
            f"min_correlation diverged at chunk_size={chunk_size}"
        )
        assert r.mono_drop_db == pytest.approx(base.mono_drop_db, abs=0.02), (
            f"mono_drop_db diverged at chunk_size={chunk_size}"
        )
        assert r.side_to_mid_db == pytest.approx(base.side_to_mid_db, abs=0.02), (
            f"side_to_mid_db diverged at chunk_size={chunk_size}"
        )


def test_centred_voice_does_not_lose_side_energy_to_cancellation(monkeypatch):
    """L ≈ R + tiny noise (centred audiobook speaker) must report the
    actual M/S ratio, not a floating-point artefact.

    Earlier drafts of this module computed ``rms_side² = ΣL² − 2·ΣLR + ΣR²``
    via algebraic identity; for nearly-mono inputs that subtraction
    cancels catastrophically in float64. This test pins the regression:
    we craft L = V + D and R = V − D with std(V) ≫ std(D), so side
    energy is dominated by std(D). The expected side/mid ratio is
    20·log10(std(D)/std(V)) ≈ -40 dB; if cancellation kicks in, the
    measured value drifts toward 0 dB or worse.
    """
    _force_chunk_size(monkeypatch, 4)  # force multi-chunk traversal
    rng = np.random.default_rng(42)
    duration_s = 5.0
    n = int(duration_s * SR)
    voice = rng.standard_normal(n).astype(np.float32) * 0.1
    diff = rng.standard_normal(n).astype(np.float32) * 0.001
    left = voice + diff
    right = voice - diff
    metrics = compute_stereo(_stack(left, right), SR)
    assert metrics.is_stereo
    assert metrics.mean_correlation > 0.99
    # 20 log10(0.001 / 0.1) = -40 dB; allow ±3 dB for finite-sample
    # variance on the random generators.
    assert -43.0 < metrics.side_to_mid_db < -37.0


def test_block_at_chunk_boundary_keeps_its_correlation(monkeypatch):
    """A single anomalous block sitting at the seam between two chunks
    must surface in ``min_correlation``. Chunking is per-block, so the
    block's local mean and Pearson are computed inside one chunk — the
    boundary block's stats must not be averaged with the adjacent
    chunk's by mistake.
    """
    _force_chunk_size(monkeypatch, 4)  # block 4 lands on the first seam
    sr = SR
    block_len = int(0.1 * sr)
    # Build 20 blocks (5 chunks at chunk_size=4). Most blocks are
    # perfectly correlated; the block at index 4 (first sample of
    # chunk 2) is anti-phase.
    n_blocks = 20
    sig = _sine(n_blocks * 0.1)  # 20 blocks × 0.1 s
    left = sig.copy()
    right = sig.copy()
    # Flip the sign of the boundary block on the right channel only.
    flip_block = 4
    right[flip_block * block_len : (flip_block + 1) * block_len] *= -1
    metrics = compute_stereo(_stack(left, right), sr)
    # 19 blocks at +1, 1 block at -1: mean stays high, min should be -1.
    assert metrics.min_correlation == pytest.approx(-1.0, abs=0.01), (
        "the anti-phase boundary block did not register as min_correlation"
    )
    assert metrics.mean_correlation > 0.7  # 19/20 of energy is correlated


def test_long_multi_chunk_signal_matches_short_reference(monkeypatch):
    """A signal long enough to span many chunks (with the production
    chunk size) yields the same metrics as the same signal computed in
    a single chunk. Catches any drift introduced by repeated chunk
    handover.
    """
    rng = np.random.default_rng(99)
    # 16 blocks → with chunk_size=4 we get 4 chunks, with chunk_size=64
    # we get a single chunk. Same buffer in both runs.
    n = 16 * int(0.1 * SR)
    left = rng.standard_normal(n).astype(np.float32) * 0.2
    right = (left * 0.9 + rng.standard_normal(n).astype(np.float32) * 0.05)
    stereo = _stack(left, right)

    _force_chunk_size(monkeypatch, 64)
    one_chunk = compute_stereo(stereo, SR)
    _force_chunk_size(monkeypatch, 4)
    multi_chunk = compute_stereo(stereo, SR)

    assert multi_chunk.mean_correlation == pytest.approx(
        one_chunk.mean_correlation, abs=1e-6
    )
    assert multi_chunk.min_correlation == pytest.approx(
        one_chunk.min_correlation, abs=1e-6
    )
    assert multi_chunk.mono_drop_db == pytest.approx(
        one_chunk.mono_drop_db, abs=0.01
    )
    assert multi_chunk.side_to_mid_db == pytest.approx(
        one_chunk.side_to_mid_db, abs=0.01
    )


def test_anomalous_loud_block_in_late_chunk(monkeypatch):
    """The energy-weighted mean correlation must give heavier weight to
    a loud block that lands in a late chunk, not be diluted by the
    chunk's other blocks. Verifies the silence mask and weighting still
    work when the loud-block index does not sit in the first chunk.
    """
    _force_chunk_size(monkeypatch, 4)
    sr = SR
    block_len = int(0.1 * sr)
    n_blocks = 20  # 5 chunks
    # Quiet but correlated background for most of the signal …
    sig = _sine(n_blocks * 0.1, amplitude=0.001)
    left = sig.copy()
    right = sig.copy()
    # … and a single LOUD anti-phase block in chunk 4 (block index 14).
    loud_block = 14
    loud_sig = _sine(0.1, amplitude=0.5)
    left[loud_block * block_len : (loud_block + 1) * block_len] = loud_sig
    right[loud_block * block_len : (loud_block + 1) * block_len] = -loud_sig
    metrics = compute_stereo(_stack(left, right), sr)
    # The loud anti-phase block dominates the energy-weighted mean.
    assert metrics.mean_correlation < -0.5
    # min_correlation must equal the anti-phase block's -1.
    assert metrics.min_correlation == pytest.approx(-1.0, abs=0.01)
