import math

import numpy as np

from nonvisualaudio.analysis.dynamics import compute_dynamics


def test_sine_crest_factor_is_about_3db():
    sr = 48000
    t = np.arange(sr * 2) / sr
    x = np.sin(2 * np.pi * 1000.0 * t).astype(np.float32)
    d = compute_dynamics(x, sr)
    # A full-scale sine has peak 0 dBFS and RMS -3.01 dBFS => crest ~3 dB.
    assert math.isclose(d.crest_factor_db, 3.01, abs_tol=0.1)
    assert math.isclose(d.peak_db, 0.0, abs_tol=0.05)


def test_silence_is_handled():
    d = compute_dynamics(np.zeros(1000, dtype=np.float32), 48000)
    assert d.peak_db <= -100.0
    assert d.rms_db <= -100.0


def test_half_amplitude_sine():
    sr = 48000
    t = np.arange(sr) / sr
    x = (0.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    d = compute_dynamics(x, sr)
    # Peak should be ~ -6 dBFS, RMS ~ -9 dBFS, crest still ~ 3 dB.
    assert math.isclose(d.peak_db, -6.02, abs_tol=0.1)
    assert math.isclose(d.crest_factor_db, 3.01, abs_tol=0.1)
