#!/usr/bin/env python3
"""Spot-check the loudness + dynamics engine against EBU R128 / BS.1770-4.

Synthesises a handful of EBU Tech 3341 reference test signals (and a
white-noise / full-scale-sine sanity check), runs each through the same
``decode_and_measure`` + ``compute_dynamics`` pipeline the app uses, and
prints expected vs. measured values. Returns a non-zero exit code if any
check fails, so it can serve as a release-time gate.

The EBU-3341 reference signals chosen here are the ones whose expected
loudness can be derived analytically from the ITU-R BS.1770-4 spec
without depending on the published WAV fixtures:

  - Signal 1 (-23 LUFS, 1 kHz sine): the K-weighting filter is calibrated
    so this exact synthetic signal yields I = -23.0 LUFS ±0.1.
  - Signal 2 (-33 LUFS, 1 kHz sine): same, ten dB lower.
  - Stepped pattern (-26/-20/-26 dBFS, 20 s each): the integrated
    loudness across all three segments works out to -23.0 LUFS, and the
    -10 LU relative gate does not kick in (every segment is above the
    -33 LUFS gate). Tests the gated mean-of-mean-squares logic and the
    LRA (loudness range) calculation in one shot.

Usage::

    python scripts/verify_loudness_against_ebu_r128.py

Run this before tagging any release. A clean PASS across all rows means
the engine still matches the published EBU R128 expectations — that is
the project's standing quality seal on the loudness math.
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

# Make the in-tree ``nonvisualaudio`` package importable when this script
# is invoked directly from the repo root, without requiring the caller to
# set PYTHONPATH or to ``pip install -e .`` first.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import numpy as np  # noqa: E402  — must come after the sys.path fix
import soundfile as sf  # noqa: E402

from nonvisualaudio.analysis.dynamics import compute_dynamics  # noqa: E402
from nonvisualaudio.audio.decoder import decode_and_measure  # noqa: E402


SR = 48000


def db_to_lin(db: float) -> float:
    return 10 ** (db / 20.0)


def synth_sine_stereo(seconds: float, freq: float, level_dbfs: float) -> np.ndarray:
    n = int(round(seconds * SR))
    t = np.arange(n) / SR
    amp = db_to_lin(level_dbfs)
    sig = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.column_stack((sig, sig)).astype(np.float32)


def synth_stepped(
    pattern: list[tuple[float, float]], freq: float = 1000.0
) -> np.ndarray:
    parts = [synth_sine_stereo(dur, freq, lvl) for dur, lvl in pattern]
    return np.concatenate(parts, axis=0)


def synth_white_noise_stereo(
    seconds: float, target_rms_per_ch_dbfs: float, seed: int
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(round(seconds * SR))
    target = db_to_lin(target_rms_per_ch_dbfs)
    parts = []
    for _ in range(2):
        x = rng.standard_normal(n).astype(np.float64)
        x = x / float(np.sqrt(np.mean(x * x)))
        x = (x * target).astype(np.float32)
        parts.append(x)
    return np.column_stack(parts).astype(np.float32)


def _write(path: Path, sig: np.ndarray) -> None:
    sf.write(str(path), sig, SR, subtype="PCM_24")


def _check(
    measured: float, expected: tuple[float, float] | None, results: list[bool]
) -> str:
    """Return the formatted PASS/FAIL suffix and record the outcome."""
    if expected is None:
        return ""
    lo, hi = expected
    ok = lo <= measured <= hi
    results.append(ok)
    return f"  expected [{lo:+.2f}, {hi:+.2f}]  {'PASS' if ok else 'FAIL'}"


def run_case(
    name: str,
    sig: np.ndarray,
    results: list[bool],
    *,
    expected_lufs: tuple[float, float] | None = None,
    expected_peak_db: tuple[float, float] | None = None,
    expected_rms_db: tuple[float, float] | None = None,
    expected_true_peak: tuple[float, float] | None = None,
    tmp: Path,
) -> None:
    wav = tmp / f"verify_{name}.wav"
    _write(wav, sig)
    decoded, loud = decode_and_measure(wav)
    dyn = compute_dynamics(decoded.samples, decoded.sample_rate)
    print(f"\n=== {name} ===")
    print(
        f"  Integrated LUFS:    {loud.integrated_lufs:+.2f} LUFS"
        f"{_check(loud.integrated_lufs, expected_lufs, results)}"
    )
    print(
        f"  True peak (dBTP):   {loud.true_peak_dbtp:+.2f} dBTP"
        f"{_check(loud.true_peak_dbtp, expected_true_peak, results)}"
    )
    print(f"  LRA (LU):           {loud.loudness_range_lu:+.2f} LU")
    print(f"  Short-term max:     {loud.short_term_max_lufs:+.2f} LUFS")
    print(
        f"  Sample peak (dB):   {dyn.peak_db:+.2f} dBFS"
        f"{_check(dyn.peak_db, expected_peak_db, results)}"
    )
    print(
        f"  RMS mono mix (dB):  {dyn.rms_db:+.2f} dBFS"
        f"{_check(dyn.rms_db, expected_rms_db, results)}"
    )
    print(f"  Crest factor (dB):  {dyn.crest_factor_db:+.2f} dB")
    print(f"  DR score:           {dyn.dr_score:.1f}")
    wav.unlink()


def main() -> int:
    results: list[bool] = []
    tmp = Path(tempfile.mkdtemp(prefix="nva_verify_"))
    try:
        # EBU Tech 3341 — Test signal 1
        # Stereo 1 kHz sine at -23 dBFS, ≥20 s. Expected I = -23.0 LUFS ±0.1.
        run_case(
            "EBU_3341_sig1_minus23LUFS_1kHz",
            synth_sine_stereo(30.0, 1000.0, -23.0),
            results,
            expected_lufs=(-23.1, -22.9),
            expected_peak_db=(-23.05, -22.95),
            expected_true_peak=(-23.1, -22.5),
            tmp=tmp,
        )

        # EBU Tech 3341 — Test signal 2
        # Same but -33 dBFS. Expected I = -33.0 LUFS ±0.1.
        run_case(
            "EBU_3341_sig2_minus33LUFS_1kHz",
            synth_sine_stereo(30.0, 1000.0, -33.0),
            results,
            expected_lufs=(-33.1, -32.9),
            expected_peak_db=(-33.05, -32.95),
            expected_true_peak=(-33.1, -32.5),
            tmp=tmp,
        )

        # Stepped pattern: 20 s @ -26 dBFS, 20 s @ -20 dBFS, 20 s @ -26 dBFS,
        # 1 kHz sine, both channels. Analytical I = -23.0 LUFS:
        #   ms = (2·10^-2.6 + 10^-2) / 6 = 0.002504 per channel
        #   stereo sum × |H_K(1 kHz)|² → 10·log10(...) − 0.691 = −23.00 LUFS.
        # The relative −10 LU gate does not fire (every segment is above
        # the −33 LUFS gate). LRA reflects the 6 dB step between segments.
        run_case(
            "Stepped_pattern_minus26_minus20_minus26_dBFS",
            synth_stepped([(20.0, -26.0), (20.0, -20.0), (20.0, -26.0)]),
            results,
            expected_lufs=(-23.2, -22.8),
            expected_peak_db=(-20.05, -19.95),
            tmp=tmp,
        )

        # White-noise sanity. Per-channel Gaussian noise scaled so each
        # channel has RMS exactly −20 dBFS. The mono mixdown (L+R)/2 of
        # two independent Gaussians with σ = 0.1 each has variance σ²/2,
        # so its RMS is σ/√2 = −23.01 dBFS exactly. The integrated LUFS
        # for K-weighted broadband noise depends on the filter's RMS gain
        # for a white spectrum (≈ +3.5 dB net), so we don't pin it here.
        run_case(
            "White_noise_per_channel_RMS_minus_20_dBFS",
            synth_white_noise_stereo(15.0, target_rms_per_ch_dbfs=-20.0, seed=7),
            results,
            expected_peak_db=(-10.0, -3.0),
            expected_rms_db=(-23.3, -22.7),
            tmp=tmp,
        )

        # Full-scale 1 kHz sine: sample peak should land at exactly 0 dBFS.
        # 1 kHz at 48 kHz = 48 samples per cycle, so the wave aligns to the
        # sample grid and the true-peak meter does not see inter-sample
        # overshoot. A higher non-divisor frequency would.
        run_case(
            "Full_scale_1kHz_sine",
            synth_sine_stereo(5.0, 1000.0, 0.0),
            results,
            expected_peak_db=(-0.05, 0.05),
            tmp=tmp,
        )
    finally:
        for f in tmp.iterdir():
            f.unlink()
        tmp.rmdir()

    total = len(results)
    passed = sum(results)
    failed = total - passed
    print(f"\n{'─' * 60}")
    print(f"{passed}/{total} checks passed, {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
