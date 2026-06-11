"""Absolute EBU R128 / BS.1770 reference checks through the streaming path.

Every other loudness test in the suite pins *parity* (streaming == batch,
parser == fixture) — which is trivially satisfied because both pipelines
call the same ffmpeg ebur128 binary. These tests pin the *absolute*
numbers against values derivable from ITU-R BS.1770-4 and EBU Tech 3341,
so a drift in ffmpeg, in the parser, or in the pipeline wiring is caught
even when old and new path drift together.

Reference signals (synthesised, no downloads, no fixtures):

* Tech 3341 case 1 — stereo 997 Hz sine, both channels at -23 dBFS peak,
  20 s. BS.1770-4 calibrates the K-weighting so a full-scale 997 Hz sine
  in a single channel reads -3.01 LKFS; the constant -0.691 in the
  loudness formula cancels the filter's +0.691 dB gain at 997 Hz. For
  identical sines on L and R the channel sum doubles the power (+3.01 dB),
  which exactly cancels the sine's mean-square factor of 1/2 (-3.01 dB):
  I = 20·log10(amplitude) = -23.0 LUFS. Expected I = -23.0 ±0.1 (the
  tolerance Tech 3341 itself specifies).
* Tech 3341 case 2 — the same signal at -33 dBFS. Expected -33.0 ±0.1.
* Mono channel weighting — mono 997 Hz sine at -20 dBFS peak. A single
  channel with weight G=1 keeps the sine's -3.01 dB mean-square factor:
  I = -20 - 3.01 = -23.0 LUFS. Pins the mono decode path absolutely.
* Stepped gating pattern — 20 s at -26, 20 s at -20, 20 s at -26 dBFS
  (stereo 997 Hz). Analytic gated mean: per-channel mean square
  (2·10^-2.6 + 10^-2.0)/6 · 2 channels → -23.0 LUFS; the -10 LU relative
  gate stays silent because every segment is louder than the threshold.
  Short-term max is the -20 dBFS plateau; LRA spans the 6 dB step.

All signals are written as float32 WAV so the synthesised values reach
ffmpeg without quantisation. Everything needs a working ffmpeg and is
skipped otherwise. The signals run through ``pipeline.analyze`` — the
production streaming entry point — not through the legacy batch decoder,
so these checks hold independent of any batch-vs-streaming parity.

(The release-gate script ``scripts/verify_loudness_against_ebu_r128.py``
covers similar ground but runs the legacy ``decode_and_measure`` path
and lives outside pytest; this file is the in-suite, streaming-path
equivalent.)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nonvisualaudio.analysis import pipeline
from nonvisualaudio.analysis.project import analyze_project

SR = 48000
# Tech 3341 prescribes 997 Hz: close enough to the 1 kHz calibration
# point of the K-weighting, but deliberately NOT an integer divisor of
# the sample rate, so the waveform does not align to the sample grid and
# the true-peak oversampler does real interpolation work.
FREQ_HZ = 997.0


def _ffmpeg_available() -> bool:
    """True iff a working ffmpeg binary can be resolved on this machine."""
    from nonvisualaudio.audio.ffmpeg_runner import find_ffmpeg
    from nonvisualaudio.errors import MissingFFmpegError

    try:
        find_ffmpeg()
    except MissingFFmpegError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _ffmpeg_available(), reason="no working ffmpeg on this machine"
)


def _sine(seconds: float, level_dbfs: float) -> np.ndarray:
    n = int(seconds * SR)
    t = np.arange(n) / SR
    amp = 10.0 ** (level_dbfs / 20.0)
    return (amp * np.sin(2.0 * np.pi * FREQ_HZ * t)).astype(np.float32)


def _write_stereo(path: Path, mono: np.ndarray) -> None:
    import soundfile as sf

    sf.write(str(path), np.column_stack((mono, mono)), SR, subtype="FLOAT")


def _write_mono(path: Path, mono: np.ndarray) -> None:
    import soundfile as sf

    sf.write(str(path), mono, SR, subtype="FLOAT")


def test_tech3341_case1_stereo_sine_minus23(tmp_path: Path) -> None:
    """Tech 3341 case 1: I = -23.0 LUFS ±0.1, plus the derived side
    readings that are analytically fixed for a constant-level signal."""
    wav = tmp_path / "sig1.wav"
    _write_stereo(wav, _sine(20.0, -23.0))

    loud = pipeline.analyze(wav).loudness

    assert loud.integrated_lufs == pytest.approx(-23.0, abs=0.1)
    # Constant signal: every full short-term window reads the integrated
    # value, so the maximum equals it.
    assert loud.short_term_max_lufs == pytest.approx(-23.0, abs=0.1)
    # Constant level: loudness range collapses to zero.
    assert loud.loudness_range_lu == pytest.approx(0.0, abs=0.1)
    # A 997 Hz sine's inter-sample crest equals its amplitude, so true
    # peak ≈ -23.0 dBTP. ±0.2 leaves room for the oversampler's
    # interpolation error plus ffmpeg's 0.1 dB print resolution.
    assert loud.true_peak_dbtp == pytest.approx(-23.0, abs=0.2)


def test_tech3341_case2_stereo_sine_minus33(tmp_path: Path) -> None:
    """Tech 3341 case 2: the same sine 10 dB lower → -33.0 LUFS ±0.1.
    Confirms the scale is linear-in-dB and the absolute gate (-70 LUFS)
    does not bite at low programme levels."""
    wav = tmp_path / "sig2.wav"
    _write_stereo(wav, _sine(20.0, -33.0))

    loud = pipeline.analyze(wav).loudness

    assert loud.integrated_lufs == pytest.approx(-33.0, abs=0.1)


def test_mono_sine_channel_weighting(tmp_path: Path) -> None:
    """Mono 997 Hz sine at -20 dBFS peak: a single G=1 channel keeps the
    sine's -3.01 dB mean-square factor, so I = -23.0 LUFS. True peak and
    sample peak stay at the -20 dBFS amplitude — pinning that LUFS and
    dBTP scales are 3 dB apart for this signal, exactly per spec."""
    wav = tmp_path / "mono.wav"
    _write_mono(wav, _sine(10.0, -20.0))

    result = pipeline.analyze(wav)

    assert result.file_info.channels == 1
    assert result.loudness.integrated_lufs == pytest.approx(-23.0, abs=0.1)
    assert result.loudness.true_peak_dbtp == pytest.approx(-20.0, abs=0.2)
    assert result.dynamics.peak_db == pytest.approx(-20.0, abs=0.05)


def test_stepped_pattern_gated_mean_and_lra(tmp_path: Path) -> None:
    """-26 / -20 / -26 dBFS, 20 s each: the gated mean works out to
    -23.0 LUFS analytically (see module docstring) and exercises the
    block gating logic with a genuinely non-constant programme. The
    short-term maximum is the -20 dBFS plateau; LRA spans the 6 dB step
    (10th..95th percentile of the short-term distribution: -26 → -20)."""
    wav = tmp_path / "stepped.wav"
    sig = np.concatenate(
        [_sine(20.0, -26.0), _sine(20.0, -20.0), _sine(20.0, -26.0)]
    )
    _write_stereo(wav, sig)

    loud = pipeline.analyze(wav).loudness

    assert loud.integrated_lufs == pytest.approx(-23.0, abs=0.2)
    assert loud.short_term_max_lufs == pytest.approx(-20.0, abs=0.2)
    # Percentile trimming at the segment boundaries can nudge LRA a
    # little across ffmpeg versions; measured exactly 6.0 locally.
    assert loud.loudness_range_lu == pytest.approx(6.0, abs=0.5)


def test_project_combined_pass_absolute(tmp_path: Path) -> None:
    """The project-mode combined concat pass, anchored absolutely: two
    -23 dBFS reference sines concatenate to a -23.0 LUFS project. This
    is independent of any batch reference — if the combined ffmpeg graph
    ever gained an unintended level change (resampler, downmix, split),
    this absolute anchor moves."""
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    _write_stereo(a, _sine(8.0, -23.0))
    _write_stereo(b, _sine(12.0, -23.0))

    project = analyze_project([a, b], project_name="Referenz")
    combined = project.combined.loudness

    assert combined.integrated_lufs == pytest.approx(-23.0, abs=0.1)
    assert combined.true_peak_dbtp == pytest.approx(-23.0, abs=0.2)
    # Provenance: the combined true peak must be the loudest per-track
    # reading, with the owning track attached.
    loudest = max(project.files, key=lambda fr: fr.loudness.true_peak_dbtp)
    assert combined.true_peak_dbtp == loudest.loudness.true_peak_dbtp
    assert (
        combined.true_peak_track_filename == loudest.file_info.filename
    )
