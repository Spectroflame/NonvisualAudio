import math

import numpy as np
import pytest

from nonvisualaudio.analysis.dynamics import compute_dynamics


def test_sine_crest_factor_is_about_3db():
    sr = 48000
    t = np.arange(sr * 2) / sr
    x = np.sin(2 * np.pi * 1000.0 * t).astype(np.float32)
    d = compute_dynamics(x, sr)
    # A full-scale sine has peak 0 dBFS and RMS -3.01 dBFS => crest ~3 dB.
    assert math.isclose(d.crest_factor_db, 3.01, abs_tol=0.1)
    assert math.isclose(d.peak_db, 0.0, abs_tol=0.05)


def test_silence_is_handled():
    d = compute_dynamics(np.zeros(1000, dtype=np.float32), 48000)
    assert d.peak_db <= -100.0
    assert d.rms_db <= -100.0


def test_half_amplitude_sine():
    sr = 48000
    t = np.arange(sr) / sr
    x = (0.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    d = compute_dynamics(x, sr)
    # Peak should be ~ -6 dBFS, RMS ~ -9 dBFS, crest still ~ 3 dB.
    assert math.isclose(d.peak_db, -6.02, abs_tol=0.1)
    assert math.isclose(d.crest_factor_db, 3.01, abs_tol=0.1)


# --------------------------------------------------------------------------- #
# Chunking correctness — peak, RMS, and DR score must be independent of
# how the signal is sliced into chunks. The user asked to confirm
# correctness across boundaries for very long audiobooks (9 h+, ideally
# the 54 h Stephen King case), so these tests exercise the multi-chunk
# path explicitly and pin block-boundary edge cases.
# --------------------------------------------------------------------------- #


def _force_chunk_size(monkeypatch, blocks_per_chunk):
    """Patch the module-level chunk size so tests exercise the
    multi-chunk path without needing gigabytes of synthetic audio."""
    from nonvisualaudio.analysis import dynamics as dyn

    monkeypatch.setattr(dyn, "_BLOCKS_PER_CHUNK", blocks_per_chunk)


def test_chunking_invariant_across_chunk_sizes(monkeypatch):
    """Output must not depend on how many blocks fit in one chunk.

    Each chunk boundary lies on an exact block boundary (chunk size is a
    multiple of block_len), so peak, RMS and DR must be identical for
    every chunk size we try.
    """
    sr = 48000
    # 30 seconds → 10 three-second blocks. Plenty of room for chunk
    # sizes 1, 3, 7, 64 to give different partitions of the same signal.
    n = 30 * sr
    rng = np.random.default_rng(2024)
    x = (rng.standard_normal(n) * 0.3).astype(np.float32)

    results = []
    for chunk in (1, 3, 7, 64):
        _force_chunk_size(monkeypatch, chunk)
        results.append(compute_dynamics(x, sr))

    base = results[0]
    for chunk_size, d in zip((1, 3, 7, 64), results):
        # All four metrics round to 1-2 decimals on output, so the
        # underlying float64 accumulation can differ by orders of
        # magnitude below the display resolution without affecting the
        # reported value.
        assert d.peak_db == pytest.approx(base.peak_db, abs=1e-6), (
            f"peak_db diverged at chunk_size={chunk_size}"
        )
        assert d.rms_db == pytest.approx(base.rms_db, abs=1e-6), (
            f"rms_db diverged at chunk_size={chunk_size}"
        )
        assert d.crest_factor_db == pytest.approx(base.crest_factor_db, abs=1e-6), (
            f"crest_factor_db diverged at chunk_size={chunk_size}"
        )
        assert d.dr_score == pytest.approx(base.dr_score, abs=0.05), (
            f"dr_score diverged at chunk_size={chunk_size}"
        )


def test_peak_at_chunk_boundary_is_detected(monkeypatch):
    """A single loud sample sitting at the seam between two chunks must
    still register as the global peak. The chunked path computes max
    per chunk and updates a running global max — if a chunk boundary
    drops the spike, the peak would silently fall back to the
    background level.
    """
    _force_chunk_size(monkeypatch, 2)
    sr = 48000
    block_len = 3 * sr
    # 8 blocks total → chunks 0-1, 2-3, 4-5, 6-7.
    n_blocks = 8
    x = np.full(n_blocks * block_len, 0.1, dtype=np.float32)
    # Place a loud spike at the very first sample of chunk 2 (block 4).
    boundary_sample = 4 * block_len
    x[boundary_sample] = 0.99
    d = compute_dynamics(x, sr)
    assert d.peak_db == pytest.approx(20 * math.log10(0.99), abs=0.05)


def test_peak_in_tail_after_last_full_block(monkeypatch):
    """The trailing partial block (samples beyond ``n_full * block_len``)
    still has to contribute to peak and RMS — only the per-block DR
    statistics are restricted to whole blocks. A loud sample in the
    tail must surface in the peak value.
    """
    _force_chunk_size(monkeypatch, 4)
    sr = 48000
    block_len = 3 * sr
    # 5 full blocks plus a short tail of 1000 samples.
    n_blocks = 5
    tail_len = 1000
    x = np.full(n_blocks * block_len + tail_len, 0.05, dtype=np.float32)
    # Spike lives inside the tail, well past the last full block.
    spike_index = n_blocks * block_len + tail_len // 2
    x[spike_index] = 0.8
    d = compute_dynamics(x, sr)
    assert d.peak_db == pytest.approx(20 * math.log10(0.8), abs=0.05)


def test_loud_block_in_late_chunk_drives_dr(monkeypatch):
    """The DR score is driven by the top-20 % loudest blocks. If a loud
    block lives in a late chunk, it must still rank correctly against
    the quiet earlier blocks.
    """
    _force_chunk_size(monkeypatch, 4)
    sr = 48000
    block_len = 3 * sr
    n_blocks = 10
    # Quiet background, then a single very loud block at index 8.
    x = np.full(n_blocks * block_len, 0.001, dtype=np.float32)
    loud_start = 8 * block_len
    loud_end = 9 * block_len
    t = np.arange(loud_end - loud_start) / sr
    # Full-scale sine for one block → block RMS ≈ -3 dBFS, peak 0 dBFS.
    x[loud_start:loud_end] = np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
    d = compute_dynamics(x, sr)
    # Peak comes from the loud block (0 dBFS sine).
    assert d.peak_db == pytest.approx(0.0, abs=0.1)
    # DR = peak − mean(top-20 % block RMS). Top 20 % of 10 blocks = 2:
    # the loud block (RMS ≈ 0.707) and one quiet block (RMS ≈ 0.001).
    # Mean of those two = 0.354 → −9 dB, so DR ≈ 9.
    #
    # The point of this test is that the loud block is INCLUDED in the
    # top-k selection (otherwise DR would clamp to the silence-driven
    # maximum of 30, since all remaining blocks are at the noise floor).
    assert 7.0 < d.dr_score < 12.0
    assert d.dr_score < 30.0  # clamped value would mean the loud block was dropped


def test_long_signal_multi_chunk_matches_single_chunk(monkeypatch):
    """Crafted signal split four ways vs. computed in one chunk must
    produce identical metrics — catches any drift from repeated chunk
    handover.
    """
    sr = 48000
    rng = np.random.default_rng(7)
    # 16 three-second blocks; with chunk=4 → 4 chunks, with chunk=64 → 1.
    n = 16 * 3 * sr
    x = (rng.standard_normal(n) * 0.2).astype(np.float32)
    # Plant a deterministic loud sample so peak comes from a known spot.
    x[7 * 3 * sr + 10000] = 0.95

    _force_chunk_size(monkeypatch, 64)
    one_chunk = compute_dynamics(x, sr)
    _force_chunk_size(monkeypatch, 4)
    multi_chunk = compute_dynamics(x, sr)

    assert multi_chunk.peak_db == pytest.approx(one_chunk.peak_db, abs=1e-6)
    assert multi_chunk.rms_db == pytest.approx(one_chunk.rms_db, abs=1e-6)
    assert multi_chunk.crest_factor_db == pytest.approx(
        one_chunk.crest_factor_db, abs=1e-6
    )
    assert multi_chunk.dr_score == pytest.approx(one_chunk.dr_score, abs=0.05)


def test_loud_tail_after_last_full_block_lifts_peak_and_rms_only(monkeypatch):
    """File is whisper-quiet for five full 3-second blocks, then a half-
    second 0 dBFS burst lives entirely inside the trailing partial block.

    Expected behaviour:
    - Peak and overall RMS register the burst — both are computed across
      every sample in the input, including the tail.
    - The DR score does *not* see the burst, by design. DR follows the
      TT-DR convention of running on whole 3-second blocks; samples
      after ``n_full * block_len`` never enter the per-block selection.
      With five quiet blocks at −60 dB block-RMS the loudest-20%
      average is also −60 dB, so ``peak − loud_rms`` ≈ 60 dB and the
      output clamps to the 30-ceiling.

    This pins the documented behaviour: a loud tail moves peak/RMS but
    must NOT silently make the DR score change just because the burst
    arrived a few seconds before the file ended.
    """
    _force_chunk_size(monkeypatch, 4)
    sr = 48000
    block_len = 3 * sr
    n_full_blocks = 5
    tail_len = sr  # 1-second tail, less than a full 3-second block
    x = np.full(n_full_blocks * block_len + tail_len, 0.001, dtype=np.float32)
    # Half-second 0 dBFS sine sitting inside the tail.
    tail_start = n_full_blocks * block_len
    burst_start = tail_start + tail_len // 4
    burst_len = tail_len // 2
    t = np.arange(burst_len) / sr
    x[burst_start : burst_start + burst_len] = (
        np.sin(2 * np.pi * 1000.0 * t).astype(np.float32)
    )

    d = compute_dynamics(x, sr)
    # Peak picks up the 0 dBFS burst.
    assert d.peak_db == pytest.approx(0.0, abs=0.1)
    # Overall RMS: 24 000 burst samples (mean square 0.5) plus 744 000
    # quiet samples (mean square 1e-6) over 768 000 total → mean square
    # ≈ 0.01563 → RMS ≈ 0.125 → −18 dB.
    assert d.rms_db == pytest.approx(-18.06, abs=1.0)
    # DR is block-based: the burst lives entirely after the last full
    # block, so it is invisible to the per-block statistics. All five
    # quiet blocks give block_rms ≈ 0.001 → loud_rms_db ≈ −60 → DR
    # clamps at the 30-ceiling. If a future change started folding the
    # tail into the DR computation, this assertion would fail and force
    # the docstring + report wording to be revisited consciously.
    assert d.dr_score == pytest.approx(30.0, abs=0.5)


def test_sub_block_file_does_not_crash_and_falls_back_to_crest():
    """Buffer shorter than one full 3-second dynamics block.

    Requirements:
    - No crash. ``compute_dynamics`` must return a finite metric for
      every field.
    - Peak / RMS / crest are still well-defined and computed from the
      available samples.
    - DR is undefined for sub-block inputs (no whole block to anchor
      on). Rather than returning a sentinel or raising, we fall back
      to the crest factor — a conservative surrogate that equals the
      crest for sub-block inputs. The point is that DR does NOT
      silently report the clamped 30-ceiling, which would falsely
      advertise "huge dynamic range" on what might be a 1-second tone.

    If the API ever grows a ``dr_score: float | None`` so the report
    can say "nicht verfügbar (Datei zu kurz)" instead, this test is the
    one to update.
    """
    sr = 48000
    # 1-second half-amplitude sine — about a third of a single block.
    t = np.arange(sr) / sr
    x = (0.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    d = compute_dynamics(x, sr)
    assert math.isfinite(d.peak_db)
    assert math.isfinite(d.rms_db)
    assert math.isfinite(d.crest_factor_db)
    assert math.isfinite(d.dr_score)
    # Half-amplitude sine: peak −6 dB, RMS −9 dB, crest ≈ 3 dB.
    assert d.peak_db == pytest.approx(-6.02, abs=0.1)
    assert d.rms_db == pytest.approx(-9.03, abs=0.1)
    assert d.crest_factor_db == pytest.approx(3.01, abs=0.1)
    # Sub-block fallback: DR collapses onto the crest factor (~3 dB for
    # a sine), not the clamped 30-ceiling.
    assert d.dr_score == pytest.approx(d.crest_factor_db, abs=0.2)
    assert d.dr_score < 5.0  # explicit guard against the clamp path
