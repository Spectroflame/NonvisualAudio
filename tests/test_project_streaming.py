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


# --------------------------------------------------------------------------- #
# Combined mixed-rate / mixed-channel project vs numeric reference
# --------------------------------------------------------------------------- #


def _bandlimited_noise(n: int, sr: int, seed: int, sigma: float) -> np.ndarray:
    """Deterministic Gaussian noise band-limited to 16 kHz, RMS = sigma.

    The 16 kHz ceiling keeps all signal energy well inside the passband
    of both resamplers involved in the mixed-rate test (ffmpeg's
    swresample in the combined pass, scipy's polyphase in the
    reference), so the comparison measures the pipeline, not the
    resamplers' transition bands near the 44.1 kHz Nyquist.
    """
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    spec[freqs > 16000.0] = 0.0
    x = np.fft.irfft(spec, n=n)
    return x / np.sqrt(np.mean(x * x)) * sigma


def _mixed_tone(n: int, sr: int, freq: float, amp: float) -> np.ndarray:
    t = np.arange(n) / sr
    return amp * np.sin(2.0 * np.pi * freq * t)


def _resample_48k(x: np.ndarray, sr: int) -> np.ndarray:
    """Reference resampler: 44.1 kHz → 48 kHz is the rational 160/147."""
    from scipy.signal import resample_poly

    if sr == SR:
        return x.astype(np.float64)
    assert sr == 44100
    return resample_poly(x.astype(np.float64), 160, 147, axis=0)


def test_mixed_rate_mono_stereo_project_matches_reference(tmp_path: Path) -> None:
    """Three tracks — stereo 48 k, mono 44.1 k, stereo 44.1 k — and a full
    numeric comparison of the combined result against an independently
    built reference (scipy resampling + the batch analysers + a bounced
    loudness measurement).

    Semantics under test: the combined pass treats the project as a
    virtual stereo bounce — mono tracks become L=R dual mono (a DAW's
    centre-panned mono track at 0 dB pan law), stereo tracks pass
    through, and the mono feed for dynamics/spectrum is the (L+R)/2
    mean, identical to the single-file decoder convention and to the
    2.1 numpy concat implementation.

    Every track carries 16 kHz-band-limited noise plus two tones, so all
    six public bands and all four 2.2 sub-bands hold real signal energy
    and the resampler comparison is meaningful everywhere. Tolerances:
    the observed ffmpeg-vs-scipy deltas on this material are ≤0.01 dB
    across every compared field; the tolerances below sit 5–10× above
    that for cross-version headroom while staying well inside the 0.1 dB
    the report displays.
    """
    # Track A — stereo, 48 kHz, 2.5 s. Correlated bed + centred tones at
    # 220 Hz (bass) and 2500 Hz (presence).
    n_a = int(2.5 * SR)
    common = _bandlimited_noise(n_a, SR, seed=101, sigma=0.04)
    diff = _bandlimited_noise(n_a, SR, seed=102, sigma=0.010)
    tones_a = _mixed_tone(n_a, SR, 220.0, 0.12) + _mixed_tone(n_a, SR, 2500.0, 0.08)
    a_samples = np.column_stack(
        (common + diff + tones_a, common - diff + tones_a)
    ).astype(np.float32)

    # Track B — mono, 44.1 kHz, 1.5 s. Tones at 440 Hz (low-mid) and
    # 5000 Hz (presence).
    n_b = int(1.5 * 44100)
    b_samples = (
        _bandlimited_noise(n_b, 44100, seed=201, sigma=0.03)
        + _mixed_tone(n_b, 44100, 440.0, 0.10)
        + _mixed_tone(n_b, 44100, 5000.0, 0.07)
    ).astype(np.float32)

    # Track C — stereo, 44.1 kHz, 2.0 s. Loudest track by a clear margin
    # (0.30 amplitude tone at 880 Hz) so the true-peak provenance is
    # unambiguous; second tone at 1250 Hz (mid).
    n_c = int(2.0 * 44100)
    common_c = _bandlimited_noise(n_c, 44100, seed=301, sigma=0.05)
    diff_c = _bandlimited_noise(n_c, 44100, seed=302, sigma=0.015)
    tones_c = _mixed_tone(n_c, 44100, 880.0, 0.30) + _mixed_tone(n_c, 44100, 1250.0, 0.10)
    c_samples = np.column_stack(
        (common_c + diff_c + tones_c, common_c - diff_c + tones_c)
    ).astype(np.float32)

    import soundfile as sf

    a = tmp_path / "a_stereo_48k.wav"
    b = tmp_path / "b_mono_441k.wav"
    c = tmp_path / "c_stereo_441k.wav"
    # FLOAT subtype: the synthesised float32 values reach the decoders
    # bit-exactly, so the reference below needs no quantisation step.
    sf.write(str(a), a_samples, SR, subtype="FLOAT")
    sf.write(str(b), b_samples, 44100, subtype="FLOAT")
    sf.write(str(c), c_samples, 44100, subtype="FLOAT")

    # ---- reference: mono mean-mixdown concat at 48 kHz ---------------- #
    mono_concat = np.concatenate(
        [
            _resample_48k(a_samples.mean(axis=1, dtype=np.float64), SR),
            _resample_48k(b_samples, 44100),
            _resample_48k(c_samples.mean(axis=1, dtype=np.float64), 44100),
        ]
    ).astype(np.float32)
    ref_dynamics = compute_dynamics(mono_concat, SR)
    ref_spectrum = compute_spectrum(mono_concat, SR)

    # ---- reference: loudness of the virtual stereo bounce ------------- #
    # The mono track enters the bounce as L=R dual mono — the combined
    # loudness therefore reads its section ~3 LU louder than the track
    # measured alone, exactly like a DAW bounce at 0 dB pan law would.
    stereo_concat = np.concatenate(
        [
            _resample_48k(a_samples, SR),
            _resample_48k(np.column_stack((b_samples, b_samples)), 44100),
            _resample_48k(c_samples, 44100),
        ],
        axis=0,
    ).astype(np.float32)
    bounce = tmp_path / "bounce.wav"
    sf.write(str(bounce), stereo_concat, SR, subtype="FLOAT")
    ref_loudness = measure_loudness(bounce)

    project = analyze_project([a, b, c], project_name="Mischprojekt")
    combined = project.combined

    # ---- FileInfo ------------------------------------------------------ #
    assert combined.file_info.sample_rate == SR  # highest input rate wins
    # Mixed channel counts → the 0 sentinel, by design (no single number
    # describes a mono+stereo project).
    assert combined.file_info.channels == 0
    assert combined.file_info.duration_seconds == pytest.approx(6.0, abs=0.05)

    # ---- Dynamics ------------------------------------------------------ #
    assert combined.dynamics.peak_db == pytest.approx(ref_dynamics.peak_db, abs=0.05)
    assert combined.dynamics.rms_db == pytest.approx(ref_dynamics.rms_db, abs=0.05)
    assert combined.dynamics.crest_factor_db == pytest.approx(
        ref_dynamics.crest_factor_db, abs=0.05
    )
    assert combined.dynamics.dr_score == pytest.approx(ref_dynamics.dr_score, abs=0.1)

    # ---- Spectrum: 6 public bands + the four 2.2 sub-bands ------------- #
    for band in (
        "sub_db", "bass_db", "low_mid_db", "mid_db", "presence_db", "air_db",
        "bass_low_db", "bass_high_db", "air_low_db", "air_high_db",
    ):
        got = getattr(combined.spectrum.bands, band)
        want = getattr(ref_spectrum.bands, band)
        assert got is not None and want is not None, band
        assert got == pytest.approx(want, abs=0.1), band

    # ---- Spectral peaks: frequencies AND prominences ------------------- #
    got_peaks = sorted(combined.spectrum.peaks, key=lambda p: p.frequency_hz)
    ref_peaks = sorted(ref_spectrum.peaks, key=lambda p: p.frequency_hz)
    assert len(got_peaks) == len(ref_peaks), (
        f"peak count differs — got={[(p.frequency_hz, p.prominence_db) for p in got_peaks]} "
        f"ref={[(p.frequency_hz, p.prominence_db) for p in ref_peaks]}"
    )
    for got_p, ref_p in zip(got_peaks, ref_peaks):
        assert got_p.frequency_hz == pytest.approx(ref_p.frequency_hz, rel=0.01)
        assert got_p.prominence_db == pytest.approx(ref_p.prominence_db, abs=0.2)

    # ---- Stereo -------------------------------------------------------- #
    # A mono track is present, so there is deliberately NO combined
    # stereo image (is_stereo=False sentinel): a correlation between
    # channels that are partly synthesised (L=R upmix) would be
    # meaningless. mean/min_correlation, mono_drop_db and side_to_mid_db
    # of the combined result are therefore NOT compared — but the
    # per-track stereo metrics of both stereo tracks are, against the
    # batch analyser on their own samples (no resampling involved).
    assert combined.stereo.is_stereo is False
    for idx, samples, rate in ((0, a_samples, SR), (2, c_samples, 44100)):
        ref_stereo = compute_stereo(samples, rate)
        got_stereo = project.files[idx].stereo
        assert got_stereo.is_stereo is True
        assert got_stereo.mean_correlation == pytest.approx(
            ref_stereo.mean_correlation, abs=1e-6
        )
        assert got_stereo.min_correlation == pytest.approx(
            ref_stereo.min_correlation, abs=1e-6
        )
        assert got_stereo.mono_drop_db == pytest.approx(
            ref_stereo.mono_drop_db, abs=1e-6
        )
        assert got_stereo.side_to_mid_db == pytest.approx(
            ref_stereo.side_to_mid_db, abs=1e-6
        )
    assert project.files[1].stereo.is_stereo is False

    # ---- Loudness ------------------------------------------------------ #
    assert combined.loudness.integrated_lufs == pytest.approx(
        ref_loudness.integrated_lufs, abs=0.15
    )
    assert combined.loudness.short_term_max_lufs == pytest.approx(
        ref_loudness.short_term_max_lufs, abs=0.15
    )
    assert combined.loudness.loudness_range_lu == pytest.approx(
        ref_loudness.loudness_range_lu, abs=0.2
    )
    # True peak: provenance first (exactly the loudest per-track scan),
    # numeric second (the bounce must agree within resampler headroom).
    loudest = max(project.files, key=lambda fr: fr.loudness.true_peak_dbtp)
    assert loudest.file_info.filename == c.name  # by construction
    assert combined.loudness.true_peak_dbtp == loudest.loudness.true_peak_dbtp
    assert combined.loudness.true_peak_dbtp == pytest.approx(
        ref_loudness.true_peak_dbtp, abs=0.2
    )
    # The combined true-peak TIMESTAMP is track-local by design (it
    # points into the owning track, where the user can actually jump
    # to), so it is asserted against the per-track scan — comparing it
    # to the bounce timeline would be wrong on purpose.
    assert (
        combined.loudness.true_peak_time_seconds
        == loudest.loudness.true_peak_time_seconds
    )
    assert (
        combined.loudness.true_peak_track_filename == loudest.file_info.filename
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
