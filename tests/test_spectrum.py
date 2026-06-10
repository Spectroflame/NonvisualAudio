import numpy as np

from nonvisualaudio.analysis.spectrum import compute_spectrum


def _sine(freq: float, seconds: float = 2.0, sr: int = 48000) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _pink_noise(seconds: float, sr: int, seed: int = 0) -> np.ndarray:
    """Pink (1/f) noise via Voss–McCartney. Good stand-in for real material."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    rows = 16
    cols = n
    source = rng.standard_normal((rows, cols))
    # Update each row at geometrically decreasing rates.
    for r in range(rows):
        step = 1 << r
        if step > 1:
            source[r, :] = np.repeat(source[r, ::step], step)[:cols]
    x = source.sum(axis=0)
    x /= np.max(np.abs(x)) + 1e-12
    return (0.3 * x).astype(np.float32)


def test_mid_sine_dominates_mid_band():
    x = _sine(1000.0)
    s = compute_spectrum(x, 48000)
    b = s.bands
    # Mid band should hold almost all energy.
    assert b.mid_db > b.sub_db
    assert b.mid_db > b.bass_db
    assert b.mid_db > b.air_db


def test_low_mid_sine_near_440_produces_peak_in_region():
    x = _sine(440.0)
    s = compute_spectrum(x, 48000)
    # The strongest peak should be within a reasonable distance of 440 Hz.
    assert s.peaks, "expected at least one spectral peak"
    top = s.peaks[0]
    assert 380.0 < top.frequency_hz < 520.0


def test_empty_input_returns_silent_bands():
    s = compute_spectrum(np.zeros(0, dtype=np.float32), 48000)
    assert s.peaks == ()
    assert s.bands.mid_db <= -100.0
    # The internal sub-bands carry the silence sentinel too, not None.
    assert s.bands.bass_low_db <= -100.0
    assert s.bands.air_high_db <= -100.0


def test_100hz_sine_lands_in_bass_low_subband():
    x = _sine(100.0)
    s = compute_spectrum(x, 48000)
    b = s.bands
    assert b.bass_low_db is not None and b.bass_high_db is not None
    # 100 Hz sits in 80-150; that sub-band must clearly beat its sibling.
    assert b.bass_low_db > b.bass_high_db + 10.0
    # And the sub-band cannot be louder than its parent band 80-250.
    assert b.bass_low_db <= b.bass_db + 0.05


def test_8khz_sine_lands_in_air_low_subband():
    x = _sine(8000.0)
    s = compute_spectrum(x, 48000)
    b = s.bands
    assert b.air_low_db is not None and b.air_high_db is not None
    assert b.air_low_db > b.air_high_db + 10.0
    assert b.air_low_db <= b.air_db + 0.05


def test_12khz_sine_lands_in_air_high_subband():
    x = _sine(12000.0)
    s = compute_spectrum(x, 48000)
    b = s.bands
    assert b.air_high_db is not None and b.air_low_db is not None
    assert b.air_high_db > b.air_low_db + 10.0


def test_subbands_default_to_none_for_legacy_construction():
    # Legacy fixtures construct BandEnergies without sub-band values;
    # the dataclass must keep accepting that.
    from nonvisualaudio.analysis.result import BandEnergies

    b = BandEnergies(
        sub_db=-30.0,
        bass_db=-20.0,
        low_mid_db=-10.0,
        mid_db=-5.0,
        presence_db=-15.0,
        air_db=-25.0,
    )
    assert b.bass_low_db is None
    assert b.air_high_db is None


def test_pink_noise_has_no_phantom_low_edge_peak():
    # Pink noise has no narrow resonances. In particular, it must not trigger
    # a phantom peak at the first bin above the analysis floor — the old
    # regression reported an ever-present "~43 Hz" peak at 44.1 kHz.
    # Test multiple seeds to make sure it's not a lucky draw.
    for seed in range(5):
        x = _pink_noise(3.0, 44100, seed=seed)
        s = compute_spectrum(x, 44100)
        low_edge_peaks = [p for p in s.peaks if p.frequency_hz < 80.0]
        assert not low_edge_peaks, (
            f"seed={seed}: pink noise produced phantom low-edge peak(s): "
            f"{[(p.frequency_hz, p.prominence_db) for p in low_edge_peaks]}"
        )


def test_strong_40hz_resonance_is_still_reported():
    # A genuine rumble at 40 Hz sitting on top of pink noise must still be
    # detected. We narrowed the phantom-peak bug, not the analysis band.
    sr = 44100
    seconds = 3.0
    t = np.arange(int(seconds * sr)) / sr
    tone = 0.35 * np.sin(2 * np.pi * 42.0 * t)
    bg = _pink_noise(seconds, sr, seed=4)[: len(tone)]
    x = (tone + 0.2 * bg).astype(np.float32)
    s = compute_spectrum(x, sr)
    assert any(35.0 <= p.frequency_hz <= 55.0 for p in s.peaks), (
        f"real 42 Hz tone was not reported; peaks: "
        f"{[(p.frequency_hz, p.prominence_db) for p in s.peaks]}"
    )


def test_pink_noise_does_not_always_report_four_peaks():
    # Pink noise should report very few peaks (ideally zero), definitely not
    # the old hard-coded "always exactly 4" behaviour.
    x = _pink_noise(3.0, 44100, seed=7)
    s = compute_spectrum(x, 44100)
    assert len(s.peaks) < 4, (
        f"expected fewer than 4 peaks for pink noise, got {len(s.peaks)}: "
        f"{[(p.frequency_hz, p.prominence_db) for p in s.peaks]}"
    )


def test_two_tones_over_pink_noise_surface_both_tones():
    sr = 48000
    t = np.arange(sr * 3) / sr
    tones = (
        0.3 * np.sin(2 * np.pi * 500.0 * t)
        + 0.3 * np.sin(2 * np.pi * 3000.0 * t)
    )
    bg = _pink_noise(3.0, sr, seed=3)[: len(tones)]
    x = (tones + 0.3 * bg).astype(np.float32)
    s = compute_spectrum(x, sr)
    # Both tones must appear; other bins are below-threshold noise.
    freqs = sorted(p.frequency_hz for p in s.peaks)
    assert any(480.0 < f < 520.0 for f in freqs)
    assert any(2900.0 < f < 3100.0 for f in freqs)
    # And we should not invent a bunch of extras.
    assert len(s.peaks) <= 3
