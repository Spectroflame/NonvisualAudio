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
