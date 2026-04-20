import numpy as np

from nonvisualaudio.analysis.spectrum import compute_spectrum


def _sine(freq: float, seconds: float = 2.0, sr: int = 48000) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


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
