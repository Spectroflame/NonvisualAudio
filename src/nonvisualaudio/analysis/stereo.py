"""Stereo-image measurements: L/R correlation, mono compatibility, width.

Four numbers come out of this module, each addressing a different
concern a mastering engineer would normally read off a goniometer or
correlation meter:

- ``mean_correlation``: energy-weighted Pearson correlation between L
  and R, blocked into ~100 ms windows so a quiet intro does not skew
  the score for the rest of the piece. ``+1`` is fully correlated
  (basically mono), ``0`` is uncorrelated, ``-1`` is perfectly out of
  phase.
- ``min_correlation``: the worst per-block correlation. A piece that
  sits at +0.7 on average but drops to -0.9 for one chorus would look
  fine on the mean alone — the minimum surfaces that single block.
- ``mono_drop_db``: how many dB the level drops when L and R are
  summed to mono compared to the equal-power stereo reference. Close
  to 0 dB means mono playback sounds the same; -3 dB and beyond means
  audible cancellations on Smart Speakers, phones, AM radio.
- ``side_to_mid_db``: M/S ratio. ``M = (L+R)/2``, ``S = (L-R)/2``.
  Strongly negative dB (narrow), around -6 dB (typical pop), 0 dB or
  positive (very wide / out of phase).

Implementation notes — peak RAM:

The naive version materialises full float64 copies of L, R, mid and
side, plus the block-correlation pass keeps four more float64 buffers
the same size as the input alive simultaneously. On multi-hour stereo
inputs that quickly runs into the tens of GB and pushes the analyser
into swap (or OOM on small machines). This module walks the input in
chunks of ~1024 blocks (≈100 s of audio at 44.1 kHz) so the per-chunk
float64 buffers stay in the few-hundred-MB range regardless of how
long the input is.

Each chunk's block boundaries are exact multiples of the global
correlation-block length, so per-block statistics (correlation,
RMS) are unaffected by chunking — they are bit-identical to the
non-chunked reference. The four scalar accumulators (``sum_l2``,
``sum_r2``, ``sum_mid_sq``, ``sum_side_sq``) are also straight sums
of non-negative numpy reductions; no subtraction of large near-equal
quantities, so a centred audiobook with L ≈ R does not lose precision.
The mid/side energies are computed *directly* per chunk rather than
through the algebraic identity ``Σ(L±R)² = ΣL² ± 2·ΣLR + ΣR²``,
because that identity catastrophically cancels for L ≈ R inputs.
"""

from __future__ import annotations

import math

import numpy as np

from nonvisualaudio.analysis.result import StereoMetrics, _empty_stereo

_SILENCE_FLOOR_DB = -120.0
# Blocks below this RMS are dropped from the correlation average — they
# would otherwise pull the mean toward zero just because nothing is
# playing.
_BLOCK_SILENCE_THRESHOLD_DB = -60.0
_BLOCK_SECONDS = 0.1
# Chunk size for the streaming pass, expressed in correlation blocks.
# 1024 blocks ≈ 100 seconds of audio. At 44.1 kHz that is roughly 36 MB
# of float64 per channel — small enough that a handful of intermediates
# stays in the hundreds-of-MB range, large enough that numpy's per-call
# overhead disappears into the vectorised inner ops.
_BLOCKS_PER_CHUNK = 1024


def _to_db(value: float) -> float:
    if value <= 0.0 or not math.isfinite(value):
        return _SILENCE_FLOOR_DB
    return 20.0 * math.log10(value)


def _pearson_full(left_f64: np.ndarray, right_f64: np.ndarray) -> float:
    """Whole-buffer Pearson correlation on already-float64 inputs."""
    a_zm = left_f64 - left_f64.mean()
    b_zm = right_f64 - right_f64.mean()
    num = float(np.sum(a_zm * b_zm))
    denom = float(math.sqrt(float(np.sum(a_zm * a_zm)) * float(np.sum(b_zm * b_zm))))
    if denom <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, num / denom))


def compute_stereo(
    stereo_samples: np.ndarray | None, sample_rate: int
) -> StereoMetrics:
    """Return stereo-image metrics for a (n, 2) float32 buffer.

    Returns a sentinel ``StereoMetrics(is_stereo=False, ...)`` when the
    buffer is missing, mono, empty, or otherwise unanalysable. Callers
    are expected to check ``is_stereo`` before reading the numbers.
    """
    if (
        stereo_samples is None
        or stereo_samples.ndim != 2
        or stereo_samples.shape[1] < 2
        or stereo_samples.shape[0] == 0
    ):
        return _empty_stereo()

    block_len = max(1, int(_BLOCK_SECONDS * sample_rate))
    n_full = stereo_samples.shape[0] // block_len

    # Accumulators that build up the whole-file statistics one chunk at
    # a time. We never materialise full-length float64 copies of L, R,
    # mid or side — only chunk-sized slices live at any moment.
    sum_l2 = 0.0
    sum_r2 = 0.0
    sum_mid_sq = 0.0
    sum_side_sq = 0.0
    total_samples = 0

    if n_full < 1:
        # Too short for block-level correlation — do one Pearson over
        # the whole (tiny) buffer. Acceptable because the buffer is by
        # definition shorter than ``_BLOCK_SECONDS`` of audio.
        left_full = stereo_samples[:, 0].astype(np.float64)
        right_full = stereo_samples[:, 1].astype(np.float64)
        mean_corr = _pearson_full(left_full, right_full)
        min_corr = mean_corr
        sum_l2 = float(np.sum(left_full * left_full))
        sum_r2 = float(np.sum(right_full * right_full))
        # Compute the mid/side energies directly. Even on tiny buffers
        # this is the right path; the identity-based shortcut would
        # cancel catastrophically when L ≈ R.
        mid_full = (left_full + right_full) * 0.5
        side_full = (left_full - right_full) * 0.5
        sum_mid_sq = float(np.sum(mid_full * mid_full))
        sum_side_sq = float(np.sum(side_full * side_full))
        total_samples = left_full.size
    else:
        corr = np.empty(n_full, dtype=np.float64)
        block_rms_arr = np.empty(n_full, dtype=np.float64)
        chunk_size = max(1, min(_BLOCKS_PER_CHUNK, n_full))

        for chunk_start in range(0, n_full, chunk_size):
            chunk_end = min(chunk_start + chunk_size, n_full)
            s0 = chunk_start * block_len
            s1 = chunk_end * block_len

            # Materialise just this chunk in float64. ``astype`` on the
            # column slice is the copy that lifts a non-contiguous
            # 1D view out of the 2D buffer.
            left_f64 = stereo_samples[s0:s1, 0].astype(np.float64)
            right_f64 = stereo_samples[s0:s1, 1].astype(np.float64)
            left_blocks = left_f64.reshape(-1, block_len)
            right_blocks = right_f64.reshape(-1, block_len)

            # Per-block Pearson correlation. The block boundaries are at
            # fixed sample offsets and a chunk is an integer multiple of
            # ``block_len``, so block i's mean and zero-mean copy never
            # straddle a chunk boundary — block results are bit-identical
            # to the non-chunked reference.
            left_zm = left_blocks - left_blocks.mean(axis=1, keepdims=True)
            right_zm = right_blocks - right_blocks.mean(axis=1, keepdims=True)
            num = np.sum(left_zm * right_zm, axis=1)
            denom = np.sqrt(
                np.sum(left_zm * left_zm, axis=1)
                * np.sum(right_zm * right_zm, axis=1)
            )
            with np.errstate(divide="ignore", invalid="ignore"):
                corr_chunk = np.where(denom > 0, num / denom, 0.0)
            corr[chunk_start:chunk_end] = np.clip(corr_chunk, -1.0, 1.0)

            # Block RMS — used as the silence mask and as the weighting
            # factor when averaging block correlations.
            block_rms_arr[chunk_start:chunk_end] = np.sqrt(
                np.mean(left_blocks * left_blocks + right_blocks * right_blocks, axis=1)
                / 2.0
            )

            # Per-channel sum-of-squares: non-negative reductions, so
            # accumulating across chunks is numerically safe.
            sum_l2 += float(np.sum(left_f64 * left_f64))
            sum_r2 += float(np.sum(right_f64 * right_f64))
            # Mid and side energies, computed directly on the chunk —
            # NOT via the identity ``Σ(L±R)² = ΣL² ± 2ΣLR + ΣR²``. That
            # identity is exact in real arithmetic but catastrophically
            # cancels in float64 for centred / nearly-mono inputs (which
            # is the common audiobook case), where the two big sums
            # nearly equal each other. The temporary chunk-sized buffers
            # below are released as soon as each chunk loop iteration
            # ends, so peak RAM still stays bounded by the chunk.
            mid_chunk = (left_f64 + right_f64) * 0.5
            sum_mid_sq += float(np.sum(mid_chunk * mid_chunk))
            del mid_chunk
            side_chunk = (left_f64 - right_f64) * 0.5
            sum_side_sq += float(np.sum(side_chunk * side_chunk))
            del side_chunk
            total_samples += left_f64.size

        # Silence mask + energy-weighted mean correlation.
        silence_threshold = 10.0 ** (_BLOCK_SILENCE_THRESHOLD_DB / 20.0)
        mask = block_rms_arr >= silence_threshold
        if not np.any(mask):
            mean_corr = float(np.mean(corr))
            min_corr = float(np.min(corr))
        else:
            weights = block_rms_arr[mask] * block_rms_arr[mask]
            mean_corr = float(np.sum(corr[mask] * weights) / np.sum(weights))
            min_corr = float(np.min(corr[mask]))

    if total_samples == 0 or (sum_l2 == 0.0 and sum_r2 == 0.0):
        return _empty_stereo()

    rms_l = math.sqrt(max(0.0, sum_l2 / total_samples))
    rms_r = math.sqrt(max(0.0, sum_r2 / total_samples))
    rms_mid = math.sqrt(max(0.0, sum_mid_sq / total_samples))
    rms_side = math.sqrt(max(0.0, sum_side_sq / total_samples))

    # mono_drop_db: mono-sum level vs. equal-power stereo reference.
    # Equal-power reference is the quadratic mean of the two channel RMSs;
    # at zero phase, mono sum equals that reference (drop = 0 dB).
    stereo_ref = math.sqrt((rms_l * rms_l + rms_r * rms_r) / 2.0)
    if stereo_ref <= 0.0:
        mono_drop_db = _SILENCE_FLOOR_DB
    else:
        mono_drop_db = round(_to_db(rms_mid) - _to_db(stereo_ref), 2)

    # side_to_mid_db: how much side energy relative to mid energy.
    if rms_mid <= 0.0:
        # Pure side signal (perfectly antiphase): mid is gone entirely.
        side_to_mid_db = 60.0
    else:
        side_to_mid_db = round(_to_db(rms_side) - _to_db(rms_mid), 2)

    return StereoMetrics(
        is_stereo=True,
        mean_correlation=round(mean_corr, 3),
        min_correlation=round(min_corr, 3),
        mono_drop_db=mono_drop_db,
        side_to_mid_db=side_to_mid_db,
    )
