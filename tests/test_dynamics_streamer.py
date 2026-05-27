"""Tests for :class:`DynamicsStreamer`.

Phase 1 of the streaming-RAM rewrite. The class must be a drop-in
replacement for ``compute_dynamics`` whose output is independent of how
the input is sliced into ``feed`` calls. These tests pin that invariant
across a range of feed patterns and exercise the chunk-boundary and
tail-handling edge cases the batch tests already cover for the
non-streaming chunk loop.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from nonvisualaudio.analysis.dynamics import (
    DynamicsStreamer,
    compute_dynamics,
)


SR = 48000


def _stream(signal: np.ndarray, sample_rate: int, chunk_size: int):
    """Feed ``signal`` to a streamer in ``chunk_size``-sized pieces."""
    s = DynamicsStreamer(sample_rate)
    for start in range(0, signal.size, chunk_size):
        s.feed(signal[start : start + chunk_size])
    return s.finalize()


def test_streamer_matches_batch_on_random_noise():
    """The two paths must produce numerically identical metrics on the
    same input. Random noise of moderate length crosses many 3-second
    blocks and exercises both the per-block path and the trailing tail.
    """
    rng = np.random.default_rng(2026)
    # 30 s = 10 full 3-s blocks + a 5000-sample tail.
    n = 30 * SR + 5000
    x = (rng.standard_normal(n) * 0.3).astype(np.float32)

    batch = compute_dynamics(x, SR)
    streamed = _stream(x, SR, chunk_size=4096)

    assert streamed.peak_db == pytest.approx(batch.peak_db, abs=1e-6)
    assert streamed.rms_db == pytest.approx(batch.rms_db, abs=1e-6)
    assert streamed.crest_factor_db == pytest.approx(
        batch.crest_factor_db, abs=1e-6
    )
    assert streamed.dr_score == pytest.approx(batch.dr_score, abs=1e-6)


def test_streamer_output_invariant_across_chunk_sizes():
    """Streamer fed at chunk sizes coprime with the 3-second block length
    must still produce the same numbers as a single-shot feed. This is
    the main correctness guarantee for the new streaming pipeline.
    """
    rng = np.random.default_rng(7)
    # 20 full blocks + ragged tail; no chunk size below is a divisor of
    # the block length, so segment boundaries land in awkward places.
    n = 20 * 3 * SR + 12345
    x = (rng.standard_normal(n) * 0.2).astype(np.float32)
    # Plant a deterministic loud sample so peak comes from a known spot.
    x[7 * 3 * SR + 10000] = 0.95

    baseline = _stream(x, SR, chunk_size=n)  # one giant chunk
    for chunk_size in (1, 1000, 4096, 7919, 100_000, 144_001):
        result = _stream(x, SR, chunk_size=chunk_size)
        assert result.peak_db == pytest.approx(baseline.peak_db, abs=1e-6), (
            f"peak diverged at chunk_size={chunk_size}"
        )
        assert result.rms_db == pytest.approx(baseline.rms_db, abs=1e-6), (
            f"rms diverged at chunk_size={chunk_size}"
        )
        assert result.crest_factor_db == pytest.approx(
            baseline.crest_factor_db, abs=1e-6
        ), f"crest diverged at chunk_size={chunk_size}"
        assert result.dr_score == pytest.approx(baseline.dr_score, abs=1e-6), (
            f"dr_score diverged at chunk_size={chunk_size}"
        )


def test_peak_at_feed_boundary_is_detected():
    """A loud sample placed exactly on the boundary between two feed
    chunks must surface in the global peak. If the streamer's carry
    logic dropped boundary samples, the peak would silently fall back
    to the background level.
    """
    block_len = 3 * SR
    n_blocks = 8
    x = np.full(n_blocks * block_len, 0.1, dtype=np.float32)
    boundary_sample = 4 * block_len
    x[boundary_sample] = 0.99

    # Feed two halves: first half ends exactly at the spike position.
    s = DynamicsStreamer(SR)
    s.feed(x[:boundary_sample])
    s.feed(x[boundary_sample:])
    result = s.finalize()
    assert result.peak_db == pytest.approx(20 * math.log10(0.99), abs=0.05)


def test_loud_tail_after_last_full_block_matches_batch():
    """A loud burst inside the trailing partial block lifts peak and RMS
    but, per the existing batch contract, does NOT contribute to the
    DR score. The streamer must replicate that exactly.
    """
    block_len = 3 * SR
    n_full_blocks = 5
    tail_len = SR
    x = np.full(n_full_blocks * block_len + tail_len, 0.001, dtype=np.float32)
    tail_start = n_full_blocks * block_len
    burst_start = tail_start + tail_len // 4
    burst_len = tail_len // 2
    t = np.arange(burst_len) / SR
    x[burst_start : burst_start + burst_len] = np.sin(
        2 * np.pi * 1000.0 * t
    ).astype(np.float32)

    batch = compute_dynamics(x, SR)
    # Use a feed pattern that puts the burst across two feed calls.
    s = DynamicsStreamer(SR)
    cut = burst_start + burst_len // 3
    s.feed(x[:cut])
    s.feed(x[cut:])
    streamed = s.finalize()

    assert streamed.peak_db == pytest.approx(batch.peak_db, abs=1e-6)
    assert streamed.rms_db == pytest.approx(batch.rms_db, abs=1e-6)
    assert streamed.dr_score == pytest.approx(batch.dr_score, abs=1e-6)


def test_sub_block_input_falls_back_to_crest_like_batch():
    """Buffer shorter than one 3-second block must take the crest-factor
    fallback (and not the clamped DR-30 ceiling) — same behaviour the
    existing batch tests pin for ``compute_dynamics``.
    """
    t = np.arange(SR) / SR
    x = (0.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)

    batch = compute_dynamics(x, SR)
    # Many tiny feeds to make sure carry-only behaviour reaches finalize.
    streamed = _stream(x, SR, chunk_size=137)

    assert streamed.peak_db == pytest.approx(batch.peak_db, abs=1e-6)
    assert streamed.rms_db == pytest.approx(batch.rms_db, abs=1e-6)
    assert streamed.crest_factor_db == pytest.approx(
        batch.crest_factor_db, abs=1e-6
    )
    assert streamed.dr_score == pytest.approx(batch.dr_score, abs=1e-6)
    assert streamed.dr_score < 5.0  # guard against the DR-30 clamp path


def test_silence_streamed_returns_silence_floor():
    s = DynamicsStreamer(SR)
    s.feed(np.zeros(SR, dtype=np.float32))
    s.feed(np.zeros(SR, dtype=np.float32))
    metrics = s.finalize()
    assert metrics.peak_db <= -100.0
    assert metrics.rms_db <= -100.0


def test_finalize_without_feed_returns_silence():
    """An empty stream must not crash and must return the silence sentinel."""
    metrics = DynamicsStreamer(SR).finalize()
    assert metrics.peak_db <= -100.0
    assert metrics.rms_db <= -100.0
    assert metrics.crest_factor_db == 0.0
    assert metrics.dr_score == 0.0


def test_finalize_is_idempotent():
    """Calling finalize twice must yield the same metrics — useful for
    consumers that want to log an interim summary mid-stream.
    """
    rng = np.random.default_rng(42)
    x = (rng.standard_normal(10 * SR) * 0.3).astype(np.float32)
    s = DynamicsStreamer(SR)
    for start in range(0, x.size, 8000):
        s.feed(x[start : start + 8000])
    a = s.finalize()
    b = s.finalize()
    assert a == b


def test_empty_feed_is_a_noop():
    """``feed(empty_array)`` must not affect state — important because
    the decoder may emit zero-byte chunks at stream boundaries.
    """
    rng = np.random.default_rng(11)
    x = (rng.standard_normal(5 * SR) * 0.2).astype(np.float32)
    s_a = DynamicsStreamer(SR)
    s_a.feed(x)
    a = s_a.finalize()

    s_b = DynamicsStreamer(SR)
    s_b.feed(np.zeros(0, dtype=np.float32))
    s_b.feed(x[: x.size // 2])
    s_b.feed(np.zeros(0, dtype=np.float32))
    s_b.feed(x[x.size // 2 :])
    s_b.feed(np.zeros(0, dtype=np.float32))
    b = s_b.finalize()

    assert a == b


def test_feed_accepts_2d_view_via_flatten():
    """When the decoder hands us a mono view of an (n, 2) stereo buffer,
    the streamer should accept a 1-D ``.mean(axis=1)`` result without
    forcing the caller to reshape — but a 2-D input should be flattened
    rather than mistaken for stereo.
    """
    rng = np.random.default_rng(0)
    x = (rng.standard_normal(2 * SR) * 0.2).astype(np.float32)
    a = DynamicsStreamer(SR)
    a.feed(x)
    flat = a.finalize()

    b = DynamicsStreamer(SR)
    b.feed(x.reshape(-1, 1))  # explicit 2-D mono view
    twod = b.finalize()

    assert flat == twod
