"""Dynamics measurements: peak, RMS, crest factor, and a simplified DR score.

The DR score here approximates the TT DR method: for each non-overlapping
3-second block, compute the RMS of the block; then take the mean of the
loudest 20 percent of block RMS values, and the overall peak, and report
``peak_db - loud_rms_db``. Values are clamped to a sensible range.
"""

from __future__ import annotations

import math

import numpy as np

from nonvisualaudio.analysis.result import DynamicsMetrics

_SILENCE_FLOOR_DB = -120.0


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

    # Work in float64 internally for stable statistics.
    x = np.asarray(samples, dtype=np.float64)
    peak_lin = float(np.max(np.abs(x)))
    rms_lin = float(math.sqrt(float(np.mean(x * x))))
    peak_db = _to_db(peak_lin)
    rms_db = _to_db(rms_lin)
    crest = peak_db - rms_db

    # DR score: 3-second blocks.
    block_len = max(1, int(3.0 * sample_rate))
    n_full = x.size // block_len
    if n_full >= 1:
        blocks = x[: n_full * block_len].reshape(n_full, block_len)
        block_rms = np.sqrt(np.mean(blocks * blocks, axis=1))
        # Loudest 20% of blocks (at least 1).
        k = max(1, int(math.ceil(0.2 * n_full)))
        top = np.sort(block_rms)[-k:]
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
