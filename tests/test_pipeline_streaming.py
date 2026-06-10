"""End-to-end tests for the Phase 3 streaming pipeline.

The contract pinned here: ``pipeline.analyze`` (streaming since 2.2)
returns the same metrics the 2.1 batch pipeline produced — decode via
``decode_and_measure``, then ``compute_stereo`` / ``compute_dynamics`` /
``compute_spectrum`` on the materialised buffers. The Phase 1 streamer
unit tests already pin streamer-vs-batch equivalence per analyser; this
file covers the wiring: factory construction with the real sample rate,
mono fan-out to two streamers, the stereo sink only for genuinely
two-channel sources, and the FileInfo metadata.

All tests need ffmpeg (loudness runs through ebur128 in both paths) and
are skipped when no working binary is available.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nonvisualaudio.analysis import pipeline
from nonvisualaudio.analysis.dynamics import compute_dynamics
from nonvisualaudio.analysis.spectrum import compute_spectrum
from nonvisualaudio.analysis.stereo import compute_stereo
from nonvisualaudio.audio.decoder import decode_and_measure

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


def _write_wav_mono(path: Path, sr: int = SR, seconds: float = 2.5) -> None:
    import soundfile as sf

    n = int(seconds * sr)
    t = np.arange(n) / sr
    sig = (
        0.45 * np.sin(2 * np.pi * 440.0 * t)
        + 0.20 * np.sin(2 * np.pi * 1320.0 * t)
        + 0.10 * np.sin(2 * np.pi * 110.0 * t)
    ).astype(np.float32)
    sf.write(str(path), sig, sr, subtype="PCM_16")


def _write_wav_stereo(path: Path, sr: int = SR, seconds: float = 2.5) -> None:
    import soundfile as sf

    n = int(seconds * sr)
    t = np.arange(n) / sr
    left = (0.4 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    right = (0.4 * np.sin(2 * np.pi * 440.0 * t + 0.5)).astype(np.float32)
    sf.write(str(path), np.column_stack((left, right)), sr, subtype="PCM_16")


def _batch_reference(path: Path):
    """The 2.1 batch pipeline, reproduced step by step."""
    decoded, loudness = decode_and_measure(path)
    return (
        decoded,
        loudness,
        compute_stereo(decoded.stereo_samples, decoded.sample_rate),
        compute_dynamics(decoded.samples, decoded.sample_rate),
        compute_spectrum(decoded.samples, decoded.sample_rate),
    )


def test_analyze_stereo_matches_batch_pipeline(tmp_path: Path) -> None:
    wav = tmp_path / "stereo.wav"
    _write_wav_stereo(wav)

    decoded, loudness, stereo, dynamics, spectrum = _batch_reference(wav)
    result = pipeline.analyze(wav)

    assert result.file_info.filename == decoded.filename
    assert result.file_info.sample_rate == decoded.sample_rate
    assert result.file_info.channels == 2
    assert result.file_info.duration_seconds == pytest.approx(
        decoded.duration_seconds
    )
    assert result.file_info.bit_depth == 16

    assert result.loudness == loudness
    assert result.dynamics == dynamics
    assert result.spectrum == spectrum
    assert result.stereo.is_stereo is True
    assert result.stereo == stereo


def test_analyze_mono_matches_batch_pipeline(tmp_path: Path) -> None:
    wav = tmp_path / "mono.wav"
    _write_wav_mono(wav)

    decoded, loudness, stereo, dynamics, spectrum = _batch_reference(wav)
    result = pipeline.analyze(wav)

    assert result.file_info.channels == 1
    assert result.loudness == loudness
    assert result.dynamics == dynamics
    assert result.spectrum == spectrum
    # Mono input: same is_stereo=False sentinel the batch path returned
    # for ``compute_stereo(None, sr)``.
    assert result.stereo.is_stereo is False
    assert result.stereo == stereo


def test_analyze_progress_is_monotonic_and_completes(tmp_path: Path) -> None:
    wav = tmp_path / "mono.wav"
    _write_wav_mono(wav, seconds=1.0)

    seen: list[tuple[int, str]] = []
    result = pipeline.analyze(wav, progress_cb=lambda pct, label: seen.append((pct, label)))

    assert result.dynamics.peak_db > -120.0
    percents = [pct for pct, _ in seen]
    assert percents, "progress callback must fire"
    assert percents == sorted(percents)
    assert percents[-1] == 100
    assert all(0 <= pct <= 100 for pct in percents)
    assert all(label for _, label in seen)


def test_analyze_streaming_keeps_no_full_buffer(tmp_path: Path) -> None:
    """The streaming pass must hand chunks to the streamers, not buffers.

    Indirect but robust check: the spectrum streamer drops its
    sub-segment buffer once a full Welch segment has been seen, and the
    dynamics streamer's carry is bounded by one 3-second block. After a
    run on a 2.5-second file both invariants must hold on the streamer
    instances the pipeline built.
    """
    wav = tmp_path / "mono.wav"
    _write_wav_mono(wav, seconds=2.5)

    holder: list[pipeline._StreamerSet] = []
    original = pipeline._StreamerSet

    class _Recording(original):
        def __init__(self, sample_rate: int, channels: int) -> None:
            super().__init__(sample_rate, channels)
            holder.append(self)

    try:
        pipeline._StreamerSet = _Recording
        pipeline.analyze(wav)
    finally:
        pipeline._StreamerSet = original

    assert len(holder) == 1
    streamers = holder[0]
    assert streamers.spectrum._got_full_segment is True
    assert streamers.spectrum._sub_segment_chunks == []
    assert streamers.dynamics._carry.size < 3 * SR
