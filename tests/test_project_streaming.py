"""End-to-end tests for the Phase 4 streaming project mode.

Pinned contract: ``analyze_project`` produces the same combined metrics
the 2.1 implementation computed by concatenating every decoded track in
numpy — but via one streaming ffmpeg concat pass that never holds a
decoded track in RAM. The reference here is built the obvious way:
concatenate the synthesised samples in the test, run the batch
analysers on the concatenation, and measure loudness on a bounced
concatenation written to disk.

Everything in this file needs ffmpeg (loudness + the combined concat
pass) and is skipped when no working binary is available.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nonvisualaudio.analysis.dynamics import compute_dynamics
from nonvisualaudio.analysis.loudness import measure_loudness
from nonvisualaudio.analysis.project import analyze_project
from nonvisualaudio.analysis.spectrum import compute_spectrum
from nonvisualaudio.analysis.stereo import compute_stereo

SR = 48000


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


def _stereo_signal(seconds: float, sr: int, freq: float, phase: float) -> np.ndarray:
    n = int(seconds * sr)
    t = np.arange(n) / sr
    left = (0.4 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    right = (0.4 * np.sin(2 * np.pi * freq * t + phase)).astype(np.float32)
    return np.column_stack((left, right)).astype(np.float32)


def _write_wav(path: Path, samples: np.ndarray, sr: int = SR) -> np.ndarray:
    """Write PCM_16 and return the quantised samples the decoders will see."""
    import soundfile as sf

    sf.write(str(path), samples, sr, subtype="PCM_16")
    return sf.read(str(path), dtype="float32", always_2d=samples.ndim == 2)[0]


def test_combined_matches_batch_concat_reference(tmp_path: Path) -> None:
    """Two same-rate stereo tracks: combined metrics == batch-on-concat."""
    a_samples = _stereo_signal(1.5, SR, 330.0, 0.4)
    b_samples = _stereo_signal(2.0, SR, 660.0, 0.9)
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    a_round = _write_wav(a, a_samples)
    b_round = _write_wav(b, b_samples)

    concat_stereo = np.concatenate([a_round, b_round], axis=0)
    concat_mono = concat_stereo.mean(axis=1, dtype=np.float32)
    ref_dynamics = compute_dynamics(concat_mono, SR)
    ref_spectrum = compute_spectrum(concat_mono, SR)
    ref_stereo = compute_stereo(concat_stereo, SR)
    bounce = tmp_path / "bounce.wav"
    _write_wav(bounce, concat_stereo)
    ref_loudness = measure_loudness(bounce)

    project = analyze_project([a, b], project_name="Hörspiel")

    assert project.project_name == "Hörspiel"
    assert len(project.files) == 2
    combined = project.combined
    assert combined.file_info.filename == "Hörspiel"
    assert combined.file_info.sample_rate == SR
    assert combined.file_info.channels == 2
    assert combined.file_info.duration_seconds == pytest.approx(3.5, abs=0.05)

    assert combined.dynamics.peak_db == pytest.approx(ref_dynamics.peak_db, abs=0.05)
    assert combined.dynamics.rms_db == pytest.approx(ref_dynamics.rms_db, abs=0.05)
    assert combined.dynamics.dr_score == pytest.approx(ref_dynamics.dr_score, abs=0.2)

    for band in (
        "sub_db", "bass_db", "low_mid_db", "mid_db", "presence_db", "air_db"
    ):
        assert getattr(combined.spectrum.bands, band) == pytest.approx(
            getattr(ref_spectrum.bands, band), abs=0.2
        ), band
    # Both source tones must survive as reported peaks of the combined
    # spectrum, like they did in the batch concat.
    ref_peak_freqs = sorted(p.frequency_hz for p in ref_spectrum.peaks)
    got_peak_freqs = sorted(p.frequency_hz for p in combined.spectrum.peaks)
    assert got_peak_freqs == pytest.approx(ref_peak_freqs, rel=0.02)

    assert combined.stereo.is_stereo is True
    assert combined.stereo.mean_correlation == pytest.approx(
        ref_stereo.mean_correlation, abs=0.01
    )
    assert combined.stereo.mono_drop_db == pytest.approx(
        ref_stereo.mono_drop_db, abs=0.1
    )
    assert combined.stereo.side_to_mid_db == pytest.approx(
        ref_stereo.side_to_mid_db, abs=0.1
    )

    assert combined.loudness.integrated_lufs == pytest.approx(
        ref_loudness.integrated_lufs, abs=0.2
    )
    # True-peak provenance: value + timestamp must come from the loudest
    # per-track scan, with the track name attached.
    loudest = max(project.files, key=lambda fr: fr.loudness.true_peak_dbtp)
    assert combined.loudness.true_peak_dbtp == loudest.loudness.true_peak_dbtp
    assert (
        combined.loudness.true_peak_track_filename
        == loudest.file_info.filename
    )


def test_mixed_mono_stereo_project_has_no_stereo_image(tmp_path: Path) -> None:
    """A mono track in the project: no combined stereo image, channels=0."""
    stereo_samples = _stereo_signal(1.0, SR, 440.0, 0.5)
    n = int(1.0 * SR)
    t = np.arange(n) / SR
    mono_samples = (0.3 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    a = tmp_path / "stereo.wav"
    b = tmp_path / "mono.wav"
    _write_wav(a, stereo_samples)
    _write_wav(b, mono_samples)

    project = analyze_project([a, b])

    assert project.combined.stereo.is_stereo is False
    # Mixed channel counts → no single project channel count.
    assert project.combined.file_info.channels == 0
    # The per-track results keep their own stereo verdicts.
    assert project.files[0].stereo.is_stereo is True
    assert project.files[1].stereo.is_stereo is False


def test_mixed_sample_rates_combined_pass_succeeds(tmp_path: Path) -> None:
    """44.1 k + 48 k inputs: the concat pass resamples instead of failing,
    and the combined result reports the project's highest rate."""
    a = tmp_path / "cd.wav"
    b = tmp_path / "studio.wav"
    _write_wav(a, _stereo_signal(1.0, 44100, 330.0, 0.4), sr=44100)
    _write_wav(b, _stereo_signal(1.0, SR, 660.0, 0.9), sr=SR)

    project = analyze_project([a, b])

    combined = project.combined
    assert combined.file_info.sample_rate == SR
    assert combined.file_info.duration_seconds == pytest.approx(2.0, abs=0.05)
    assert combined.stereo.is_stereo is True
    assert combined.loudness.integrated_lufs < 0.0
    assert combined.dynamics.peak_db > -120.0


def test_single_file_project_reuses_per_track_result(tmp_path: Path) -> None:
    """n == 1: the combined result is the track itself under the project
    label — same metrics, no second measurement pass."""
    a = tmp_path / "only.wav"
    _write_wav(a, _stereo_signal(1.5, SR, 440.0, 0.5))

    project = analyze_project([a], project_name="Solo")

    assert len(project.files) == 1
    track = project.files[0]
    combined = project.combined
    assert combined.file_info.filename == "Solo"
    assert combined.file_info.channels == 2
    assert combined.file_info.bit_depth is None
    assert combined.dynamics == track.dynamics
    assert combined.spectrum == track.spectrum
    assert combined.stereo == track.stereo
    assert combined.loudness.integrated_lufs == track.loudness.integrated_lufs
    assert (
        combined.loudness.true_peak_track_filename == track.file_info.filename
    )


def test_project_progress_is_monotonic_and_completes(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    _write_wav(a, _stereo_signal(1.0, SR, 330.0, 0.4))
    _write_wav(b, _stereo_signal(1.0, SR, 660.0, 0.9))

    seen: list[tuple[int, str]] = []
    analyze_project([a, b], progress_cb=lambda pct, label: seen.append((pct, label)))

    percents = [pct for pct, _ in seen]
    assert percents, "progress callback must fire"
    assert percents == sorted(percents)
    assert percents[-1] == 100
    assert all(label for _, label in seen)
