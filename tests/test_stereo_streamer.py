"""Tests for :class:`StereoStreamer`.

Same correctness contract as :class:`DynamicsStreamer`: output must be
chunk-invariant and numerically equivalent to ``compute_stereo`` on the
same (n, 2) float32 input. The tests also pin the catastrophic-
cancellation guard for centred-voice material — that bug was the
hardest one to find on the batch path and a chunked rewrite is the
easiest place to reintroduce it.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from nonvisualaudio.analysis.stereo import (
    StereoStreamer,
    compute_stereo,
)


SR = 48000


def _sine(seconds: float, freq: float = 1000.0, amplitude: float = 0.5) -> np.ndarray:
    n = int(seconds * SR)
    t = np.arange(n, dtype=np.float64) / SR
    return (amplitude * np.sin(2.0 * math.pi * freq * t)).astype(np.float32)


def _stack(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return np.column_stack((left, right)).astype(np.float32)


def _stream(stereo: np.ndarray, sample_rate: int, chunk_size: int):
    s = StereoStreamer(sample_rate)
    for start in range(0, stereo.shape[0], chunk_size):
        s.feed(stereo[start : start + chunk_size])
    return s.finalize()


def test_streamer_matches_batch_on_uncorrelated_noise():
    rng = np.random.default_rng(42)
    left = rng.normal(0.0, 0.3, size=5 * SR).astype(np.float32)
    right = rng.normal(0.0, 0.3, size=5 * SR).astype(np.float32)
    stereo = _stack(left, right)

    batch = compute_stereo(stereo, SR)
    streamed = _stream(stereo, SR, chunk_size=8192)

    assert streamed.is_stereo is True
    assert streamed.mean_correlation == pytest.approx(
        batch.mean_correlation, abs=1e-6
    )
    assert streamed.min_correlation == pytest.approx(
        batch.min_correlation, abs=1e-6
    )
    assert streamed.mono_drop_db == pytest.approx(batch.mono_drop_db, abs=1e-6)
    assert streamed.side_to_mid_db == pytest.approx(
        batch.side_to_mid_db, abs=1e-6
    )


def test_streamer_output_invariant_across_chunk_sizes():
    """Several chunk sizes, including ones that don't align with the
    100 ms block length, must all produce the same metrics. Block_len
    at 48 kHz is 4800 samples — none of the chunk sizes below divide it
    evenly.
    """
    rng = np.random.default_rng(99)
    n = 6 * SR  # 60 stereo blocks
    left = (rng.standard_normal(n) * 0.2).astype(np.float32)
    right = (left * 0.9 + rng.standard_normal(n).astype(np.float32) * 0.05)
    stereo = _stack(left, right)

    baseline = _stream(stereo, SR, chunk_size=n)  # one big chunk
    for chunk_size in (1, 333, 4800, 7919, 50_000, 144_001):
        result = _stream(stereo, SR, chunk_size=chunk_size)
        assert result.mean_correlation == pytest.approx(
            baseline.mean_correlation, abs=1e-6
        ), f"mean_correlation diverged at chunk_size={chunk_size}"
        assert result.min_correlation == pytest.approx(
            baseline.min_correlation, abs=1e-6
        ), f"min_correlation diverged at chunk_size={chunk_size}"
        assert result.min_correlation_time_seconds == pytest.approx(
            baseline.min_correlation_time_seconds, abs=1e-6
        ), f"min_correlation_time diverged at chunk_size={chunk_size}"
        assert result.mono_drop_db == pytest.approx(
            baseline.mono_drop_db, abs=1e-6
        ), f"mono_drop_db diverged at chunk_size={chunk_size}"
        assert result.side_to_mid_db == pytest.approx(
            baseline.side_to_mid_db, abs=1e-6
        ), f"side_to_mid_db diverged at chunk_size={chunk_size}"


def test_identical_channels_full_correlation_and_zero_drop():
    sig = _sine(1.0)
    streamed = _stream(_stack(sig, sig), SR, chunk_size=4096)
    assert streamed.is_stereo is True
    assert streamed.mean_correlation == pytest.approx(1.0, abs=0.01)
    assert streamed.min_correlation == pytest.approx(1.0, abs=0.01)
    assert streamed.mono_drop_db == pytest.approx(0.0, abs=0.1)
    assert streamed.side_to_mid_db < -60.0


def test_antiphase_signal_is_phase_critical():
    sig = _sine(1.0)
    streamed = _stream(_stack(sig, -sig), SR, chunk_size=4096)
    assert streamed.is_stereo is True
    assert streamed.mean_correlation == pytest.approx(-1.0, abs=0.01)
    assert streamed.mono_drop_db < -40.0
    assert streamed.side_to_mid_db > 0.0


def test_centred_voice_no_catastrophic_cancellation():
    """Streamer must inherit the batch path's direct mid/side accumulation.
    If a future change ever rewrote the streamer in terms of the
    algebraic identity, this test would fail for the same reason the
    batch test does — and that is the whole point of pinning it.
    """
    rng = np.random.default_rng(42)
    duration_s = 5.0
    n = int(duration_s * SR)
    voice = rng.standard_normal(n).astype(np.float32) * 0.1
    diff = rng.standard_normal(n).astype(np.float32) * 0.001
    left = voice + diff
    right = voice - diff
    streamed = _stream(_stack(left, right), SR, chunk_size=4096)
    assert streamed.is_stereo
    assert streamed.mean_correlation > 0.99
    assert -43.0 < streamed.side_to_mid_db < -37.0


def test_block_at_feed_boundary_keeps_its_correlation():
    """A 100 ms block sitting exactly at a feed-boundary must still be
    measured as a single block — its mean / Pearson must not be averaged
    across the carry / new-chunk split.
    """
    block_len = int(0.1 * SR)
    n_blocks = 20
    sig = _sine(n_blocks * 0.1)
    left = sig.copy()
    right = sig.copy()
    flip_block = 4
    right[flip_block * block_len : (flip_block + 1) * block_len] *= -1
    stereo = _stack(left, right)

    s = StereoStreamer(SR)
    # Cut the feed in the middle of the flipped block. The streamer's
    # carry has to stitch the two halves back together so the block's
    # Pearson is -1.0 again, not a diluted average.
    cut = flip_block * block_len + block_len // 2
    s.feed(stereo[:cut])
    s.feed(stereo[cut:])
    result = s.finalize()

    assert result.min_correlation == pytest.approx(-1.0, abs=0.01)
    assert result.mean_correlation > 0.7


def test_none_input_returns_not_stereo_via_empty_stream():
    """Empty stream → finalize() should mirror compute_stereo(None) by
    returning is_stereo=False, not raise.
    """
    result = StereoStreamer(SR).finalize()
    assert result.is_stereo is False


def test_silent_stereo_returns_not_stereo():
    s = StereoStreamer(SR)
    s.feed(np.zeros((SR, 2), dtype=np.float32))
    result = s.finalize()
    assert result.is_stereo is False


def test_sub_block_streamed_matches_batch():
    """When the entire input is shorter than one 100 ms block the batch
    path falls back to a one-shot Pearson on the whole buffer. The
    streamer should do the same — even when the input arrives in many
    tiny feeds.
    """
    # 50 ms of audio → half a stereo block.
    n = int(0.05 * SR)
    t = np.arange(n) / SR
    left = (0.4 * np.sin(2 * np.pi * 800.0 * t)).astype(np.float32)
    right = (0.4 * np.sin(2 * np.pi * 800.0 * t + 0.2)).astype(np.float32)
    stereo = _stack(left, right)

    batch = compute_stereo(stereo, SR)
    streamed = _stream(stereo, SR, chunk_size=137)

    assert streamed.is_stereo is True
    assert streamed.mean_correlation == pytest.approx(
        batch.mean_correlation, abs=1e-6
    )
    assert streamed.min_correlation == pytest.approx(
        batch.min_correlation, abs=1e-6
    )
    assert streamed.mono_drop_db == pytest.approx(batch.mono_drop_db, abs=1e-6)
    assert streamed.side_to_mid_db == pytest.approx(
        batch.side_to_mid_db, abs=1e-6
    )


def test_mono_input_rejected_loud():
    """Feeding a 1-D mono chunk would mis-measure correlation entirely,
    so the streamer should refuse rather than silently produce garbage.
    """
    s = StereoStreamer(SR)
    with pytest.raises(ValueError):
        s.feed(np.zeros(SR, dtype=np.float32))


def test_finalize_is_idempotent():
    rng = np.random.default_rng(13)
    n = 3 * SR
    left = (rng.standard_normal(n) * 0.2).astype(np.float32)
    right = (rng.standard_normal(n) * 0.2).astype(np.float32)
    stereo = _stack(left, right)
    s = StereoStreamer(SR)
    for start in range(0, n, 6000):
        s.feed(stereo[start : start + 6000])
    a = s.finalize()
    b = s.finalize()
    assert a == b


def test_anomalous_loud_block_in_late_feed():
    """A single loud anti-phase block arriving in the last feed must
    still drive the energy-weighted mean correlation — same regression
    the batch tests already pin for the chunk loop.
    """
    block_len = int(0.1 * SR)
    n_blocks = 20
    sig = _sine(n_blocks * 0.1, amplitude=0.001)
    left = sig.copy()
    right = sig.copy()
    loud_block = 14
    loud_sig = _sine(0.1, amplitude=0.5)
    left[loud_block * block_len : (loud_block + 1) * block_len] = loud_sig
    right[loud_block * block_len : (loud_block + 1) * block_len] = -loud_sig

    s = StereoStreamer(SR)
    # Feed in three roughly-thirds — the loud block lives in the last one.
    third = (n_blocks * block_len) // 3
    s.feed(_stack(left[:third], right[:third]))
    s.feed(_stack(left[third : 2 * third], right[third : 2 * third]))
    s.feed(_stack(left[2 * third :], right[2 * third :]))
    result = s.finalize()

    assert result.mean_correlation < -0.5
    assert result.min_correlation == pytest.approx(-1.0, abs=0.01)
