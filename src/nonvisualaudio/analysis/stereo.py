"""Stereo-image measurements: L/R correlation, mono compatibility, width.

Three numbers come out of this module, each addressing a different
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


def _to_db(value: float) -> float:
    if value <= 0.0 or not math.isfinite(value):
        return _SILENCE_FLOOR_DB
    return 20.0 * math.log10(value)


def _block_correlations(
    left: np.ndarray, right: np.ndarray, sample_rate: int
) -> tuple[float, float]:
    """Energy-weighted mean and worst-block Pearson correlation.

    Returns ``(mean, min)`` in [-1, 1]. Silent blocks are skipped from
    both numbers — they are noise floor, not a stereo statement.
    """
    block_len = max(1, int(_BLOCK_SECONDS * sample_rate))
    n_full = left.size // block_len
    if n_full < 1:
        # Too short to block — fall back to a single whole-file correlation.
        return _pearson(left, right), _pearson(left, right)

    L = left[: n_full * block_len].reshape(n_full, block_len).astype(np.float64)
    R = right[: n_full * block_len].reshape(n_full, block_len).astype(np.float64)

    L_zm = L - L.mean(axis=1, keepdims=True)
    R_zm = R - R.mean(axis=1, keepdims=True)
    num = np.sum(L_zm * R_zm, axis=1)
    denom = np.sqrt(np.sum(L_zm * L_zm, axis=1) * np.sum(R_zm * R_zm, axis=1))
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = np.where(denom > 0, num / denom, 0.0)
    corr = np.clip(corr, -1.0, 1.0)

    # Energy mask: drop blocks whose RMS is below the silence threshold.
    block_rms = np.sqrt(
        np.mean(L * L + R * R, axis=1) / 2.0
    )
    silence_threshold = 10.0 ** (_BLOCK_SILENCE_THRESHOLD_DB / 20.0)
    mask = block_rms >= silence_threshold
    if not np.any(mask):
        # Whole file below threshold: fall back to unweighted average.
        return float(np.mean(corr)), float(np.min(corr))

    weights = block_rms[mask] * block_rms[mask]
    weighted = float(np.sum(corr[mask] * weights) / np.sum(weights))
    return weighted, float(np.min(corr[mask]))


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float64, copy=False)
    b = b.astype(np.float64, copy=False)
    a_zm = a - a.mean()
    b_zm = b - b.mean()
    num = float(np.sum(a_zm * b_zm))
    denom = float(math.sqrt(np.sum(a_zm * a_zm) * np.sum(b_zm * b_zm)))
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

    left = np.ascontiguousarray(stereo_samples[:, 0], dtype=np.float32)
    right = np.ascontiguousarray(stereo_samples[:, 1], dtype=np.float32)

    # Whole-file RMS values: needed for mono-drop and M/S width.
    rms_l = float(math.sqrt(float(np.mean(left.astype(np.float64) ** 2))))
    rms_r = float(math.sqrt(float(np.mean(right.astype(np.float64) ** 2))))
    if rms_l == 0.0 and rms_r == 0.0:
        # Silent stereo file: nothing meaningful to say about its image.
        return _empty_stereo()

    mid = (left.astype(np.float64) + right.astype(np.float64)) / 2.0
    side = (left.astype(np.float64) - right.astype(np.float64)) / 2.0
    rms_mid = float(math.sqrt(float(np.mean(mid * mid))))
    rms_side = float(math.sqrt(float(np.mean(side * side))))

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

    mean_corr, min_corr = _block_correlations(left, right, sample_rate)

    return StereoMetrics(
        is_stereo=True,
        mean_correlation=round(mean_corr, 3),
        min_correlation=round(min_corr, 3),
        mono_drop_db=mono_drop_db,
        side_to_mid_db=side_to_mid_db,
    )
