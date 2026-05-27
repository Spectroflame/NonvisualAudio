"""Dynamics measurements: peak, RMS, crest factor, and a simplified DR score.

The DR score here approximates the TT DR method: for each non-overlapping
3-second block, compute the RMS of the block; then take the mean of the
loudest 20 percent of block RMS values, and the overall peak, and report
``peak_db - loud_rms_db``. Values are clamped to a sensible range.

Implementation note — peak RAM:

The signal is walked in chunks (each chunk is an integer number of
3-second blocks). The naive version promotes the entire mono buffer to
float64 (2× the mono float32 size) and then ran ``np.abs(x)`` and
``blocks * blocks`` on top — each a transient another 2× as big. On a
9-hour stereo audiobook (mono buffer ≈ 6 GB) that pushed peak RAM into
the 30+ GB range; on 50-hour material the float64 copy alone would
exceed any normal machine. Chunking bounds the analyser's working set
at ``BLOCKS_PER_CHUNK × block_len × 8`` bytes regardless of input
length, while keeping the peak / RMS / DR-score outputs bit-identical
to the non-chunked reference (the accumulators are sums of non-negative
quantities, ``np.max`` reduces associatively, and block boundaries
never straddle a chunk because chunk size is a multiple of block_len).
"""

from __future__ import annotations

import math

import numpy as np

from nonvisualaudio.analysis.result import DynamicsMetrics

_SILENCE_FLOOR_DB = -120.0

# Each chunk covers this many 3-second blocks. At 44.1 kHz one chunk is
# ~8.5 M samples = 34 MB float32 / 68 MB float64; the largest transient
# allocation inside the chunk loop (``blocks * blocks`` etc.) is the
# same size, so peak chunk RAM stays in the low hundreds of MB. Smaller
# chunks would add Python-loop overhead without measurable benefit;
# larger ones would erode the cache-locality win.
_BLOCKS_PER_CHUNK = 64


def _to_db(value: float) -> float:
    if value <= 0.0 or not math.isfinite(value):
        return _SILENCE_FLOOR_DB
    return 20.0 * math.log10(value)


def compute_dynamics(samples: np.ndarray, sample_rate: int) -> DynamicsMetrics:
    if samples.size == 0:
        return DynamicsMetrics(
            peak_db=_SILENCE_FLOOR_DB,
            rms_db=_SILENCE_FLOOR_DB,
            crest_factor_db=0.0,
            dr_score=0.0,
        )

    # DR score: 3-second blocks (same definition as the legacy version).
    block_len = max(1, int(3.0 * sample_rate))
    n_full = samples.size // block_len

    # Streaming accumulators — see module docstring.
    peak_abs_lin = 0.0
    sum_sq = 0.0
    total_samples = 0
    block_rms_arr: np.ndarray | None = (
        np.empty(n_full, dtype=np.float64) if n_full >= 1 else None
    )

    if n_full >= 1:
        for chunk_start in range(0, n_full, _BLOCKS_PER_CHUNK):
            chunk_end = min(chunk_start + _BLOCKS_PER_CHUNK, n_full)
            s0 = chunk_start * block_len
            s1 = chunk_end * block_len

            # float64 working copy of this chunk only — the original
            # mono buffer stays float32 and is shared with downstream
            # analysers, so we never modify it in place.
            x_chunk = samples[s0:s1].astype(np.float64)

            chunk_peak = float(np.max(np.abs(x_chunk)))
            if chunk_peak > peak_abs_lin:
                peak_abs_lin = chunk_peak
            # Per-chunk sum-of-squares is non-negative, so accumulating
            # across chunks introduces no precision loss for audio in
            # [-1, 1] even on multi-hour inputs.
            sum_sq += float(np.sum(x_chunk * x_chunk))
            total_samples += x_chunk.size

            # Per-block RMS: chunk size is a multiple of block_len, so
            # block i never straddles a chunk boundary — its mean and
            # sum-of-squares are computed inside one chunk.
            blocks = x_chunk.reshape(-1, block_len)
            block_rms_arr[chunk_start:chunk_end] = np.sqrt(
                np.mean(blocks * blocks, axis=1)
            )

        # Trailing partial block: contributes to peak and overall RMS
        # but not to the per-block array (the DR score uses block-aligned
        # statistics only, matching the legacy behaviour).
        tail_start = n_full * block_len
        if tail_start < samples.size:
            tail = samples[tail_start:].astype(np.float64)
            if tail.size:
                tail_peak = float(np.max(np.abs(tail)))
                if tail_peak > peak_abs_lin:
                    peak_abs_lin = tail_peak
                sum_sq += float(np.sum(tail * tail))
                total_samples += tail.size
    else:
        # Signal shorter than one 3-second block — process in a single
        # pass; chunking buys nothing at this size.
        x = samples.astype(np.float64)
        peak_abs_lin = float(np.max(np.abs(x)))
        sum_sq = float(np.sum(x * x))
        total_samples = x.size

    rms_lin = math.sqrt(sum_sq / total_samples) if total_samples > 0 else 0.0
    peak_db = _to_db(peak_abs_lin)
    rms_db = _to_db(rms_lin)
    crest = peak_db - rms_db

    if block_rms_arr is not None and block_rms_arr.size >= 1:
        # Loudest 20 % of blocks (at least 1).
        n_blocks = block_rms_arr.size
        k = max(1, int(math.ceil(0.2 * n_blocks)))
        top = np.sort(block_rms_arr)[-k:]
        loud_rms = float(np.mean(top))
        loud_rms_db = _to_db(loud_rms)
        dr_raw = peak_db - loud_rms_db
    else:
        dr_raw = crest

    # DR scores are conventionally positive integers in a 1..20+ range.
    dr_score = max(0.0, min(30.0, round(dr_raw, 1)))

    return DynamicsMetrics(
        peak_db=round(peak_db, 2),
        rms_db=round(rms_db, 2),
        crest_factor_db=round(crest, 2),
        dr_score=dr_score,
    )


# --------------------------------------------------------------------------- #
# Streaming variant — Phase 1 of the end-to-end RAM rewrite.
# --------------------------------------------------------------------------- #


class DynamicsStreamer:
    """Streaming twin of :func:`compute_dynamics`.

    The batch function above holds the entire mono buffer in RAM and walks
    it chunk by chunk internally. The streamer flips that around: callers
    push PCM chunks in (any size, any cadence) via :meth:`feed`, and the
    accumulators inside the instance hold only:

      - four scalar reductions (``peak_abs_lin``, ``sum_sq``,
        ``total_samples``, plus a list of per-block RMS arrays), and
      - at most one trailing partial 3-second block as a float64 carry.

    :meth:`finalize` returns a :class:`DynamicsMetrics` that is numerically
    equivalent to ``compute_dynamics(np.concatenate(chunks), sample_rate)``
    regardless of how the input was sliced. The streamer makes one design
    choice deliberately matching the batch path: only whole 3-second
    blocks feed the DR score; any trailing tail contributes to peak / RMS
    but not to DR. See the tests in ``test_dynamics_streamer.py``.
    """

    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self._block_len = max(1, int(3.0 * sample_rate))
        self._peak_abs_lin = 0.0
        self._sum_sq = 0.0
        self._total_samples = 0
        # Each entry is the float64 per-block RMS for one feed call's
        # whole-block portion. We concatenate at finalize time so we never
        # repeatedly resize a single growing array.
        self._block_rms_chunks: list[np.ndarray] = []
        # Trailing tail from the previous feed that did not complete a
        # full 3-second block. Prepended to the next chunk before the
        # block split. Stays bounded by ``_block_len - 1`` samples.
        self._carry: np.ndarray = np.zeros(0, dtype=np.float64)

    def feed(self, chunk: np.ndarray) -> None:
        """Consume one PCM chunk. May be any size; ``chunk.size == 0`` is a no-op.

        ``chunk`` is treated as 1-D mono float32 / float64. Higher dims
        are flattened to 1-D so callers passing a ``(n,)`` view of a
        2-D buffer work without a separate reshape.
        """
        if chunk.size == 0:
            return
        if chunk.ndim != 1:
            chunk = chunk.reshape(-1)
        # float64 promotion is intentional and matches the batch path:
        # ``chunk_peak`` / ``sum_sq`` need the wider exponent on
        # multi-hour material so the running sum does not lose precision.
        x_f64 = chunk.astype(np.float64, copy=False)

        # Each input sample contributes to peak / sum_sq / total_samples
        # exactly once — the carry-over below only affects per-block RMS.
        chunk_peak = float(np.max(np.abs(x_f64)))
        if chunk_peak > self._peak_abs_lin:
            self._peak_abs_lin = chunk_peak
        self._sum_sq += float(np.sum(x_f64 * x_f64))
        self._total_samples += x_f64.size

        # Per-block path: stitch the carry from the previous feed to the
        # new samples, slice off whole 3-second blocks, save the new
        # remainder. Block boundaries are at fixed offsets of the global
        # signal, so they match the batch reference bit-for-bit.
        if self._carry.size:
            buf = np.concatenate([self._carry, x_f64])
        else:
            buf = x_f64
        n_full = buf.shape[0] // self._block_len
        if n_full > 0:
            usable = n_full * self._block_len
            blocks = buf[:usable].reshape(n_full, self._block_len)
            block_rms = np.sqrt(np.mean(blocks * blocks, axis=1))
            self._block_rms_chunks.append(block_rms)
            # ``.copy()`` so the small tail doesn't keep the whole ``buf``
            # alive through a view's base.
            self._carry = buf[usable:].copy()
        else:
            # No new block produced — just hold the accumulated tail.
            # Copy if the carry currently aliases the caller's chunk so
            # we don't keep a reference to memory that's about to be
            # released.
            self._carry = buf.copy() if buf.base is not None else buf

    def finalize(self) -> DynamicsMetrics:
        """Return the metrics for everything fed so far.

        Repeated calls are safe — the accumulators are not consumed.
        Calling ``finalize`` without ever calling ``feed`` (or with only
        empty chunks) returns the same silence sentinel as
        ``compute_dynamics(np.zeros(...), sr)``.
        """
        if self._total_samples == 0:
            return DynamicsMetrics(
                peak_db=_SILENCE_FLOOR_DB,
                rms_db=_SILENCE_FLOOR_DB,
                crest_factor_db=0.0,
                dr_score=0.0,
            )

        rms_lin = math.sqrt(self._sum_sq / self._total_samples)
        peak_db = _to_db(self._peak_abs_lin)
        rms_db = _to_db(rms_lin)
        crest = peak_db - rms_db

        if self._block_rms_chunks:
            block_rms_arr = (
                self._block_rms_chunks[0]
                if len(self._block_rms_chunks) == 1
                else np.concatenate(self._block_rms_chunks)
            )
            n_blocks = block_rms_arr.size
            k = max(1, int(math.ceil(0.2 * n_blocks)))
            top = np.sort(block_rms_arr)[-k:]
            loud_rms = float(np.mean(top))
            loud_rms_db = _to_db(loud_rms)
            dr_raw = peak_db - loud_rms_db
        else:
            # Sub-block input: matches the batch fallback exactly.
            dr_raw = crest

        dr_score = max(0.0, min(30.0, round(dr_raw, 1)))
        return DynamicsMetrics(
            peak_db=round(peak_db, 2),
            rms_db=round(rms_db, 2),
            crest_factor_db=round(crest, 2),
            dr_score=dr_score,
        )
