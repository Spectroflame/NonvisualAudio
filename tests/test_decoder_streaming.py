"""Tests for the streaming decoder added in Phase 2.

Two correctness contracts are pinned here:

1. The streaming entry point produces the same PCM samples the legacy
   :func:`decode_and_measure` would have returned — bit-identical when
   we go through the same backend, so the existing analysers see
   exactly the same input as before.

2. The streaming entry point composes cleanly with the Phase 1 streamer
   classes: feeding the decoder's chunks into ``DynamicsStreamer``,
   ``StereoStreamer`` and ``SpectrumStreamer`` yields the same metrics
   the batch pipeline would have computed.

The soundfile path is exercised with WAV files we generate on the fly
inside ``tmp_path``. The ffmpeg path is exercised by calling the new
``_ffmpeg_decode_with_loudness_streaming`` helper directly with the same
WAV (ffmpeg can decode WAV too, so the test does not need a separate
MP3 fixture), and is skipped automatically when no ffmpeg binary is
available on this machine.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from nonvisualaudio.analysis.dynamics import (
    DynamicsStreamer,
    compute_dynamics,
)
from nonvisualaudio.analysis.spectrum import (
    SpectrumStreamer,
    compute_spectrum,
)
from nonvisualaudio.analysis.stereo import (
    StereoStreamer,
    compute_stereo,
)
from nonvisualaudio.audio.decoder import (
    StreamingDecodeInfo,
    StreamingSinks,
    _ffmpeg_decode_with_loudness,
    _ffmpeg_decode_with_loudness_streaming,
    _probe_via_ffmpeg,
    decode,
    decode_and_measure,
    decode_and_measure_streaming,
)


SR = 48000


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _RecordingSink:
    """A test double that captures every chunk a streaming decoder feeds.

    Each chunk is copied before being stored so the recorder's view of
    the stream is not invalidated when the decoder releases the source
    buffer. ``assemble_*`` joins the captured chunks into a single
    contiguous numpy array for bit-exact comparison against the batch
    decoder's output.
    """

    def __init__(self) -> None:
        self.mono_chunks: list[np.ndarray] = []
        self.stereo_chunks: list[np.ndarray] = []

    def feed_mono(self, chunk: np.ndarray) -> None:
        self.mono_chunks.append(np.asarray(chunk, dtype=np.float32).copy())

    def feed_stereo(self, chunk: np.ndarray) -> None:
        self.stereo_chunks.append(np.asarray(chunk, dtype=np.float32).copy())

    def assemble_mono(self) -> np.ndarray:
        if not self.mono_chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.mono_chunks)

    def assemble_stereo(self) -> np.ndarray | None:
        if not self.stereo_chunks:
            return None
        return np.concatenate(self.stereo_chunks, axis=0)


def _ffmpeg_available() -> bool:
    """True iff a working ffmpeg binary can be resolved on this machine."""
    from nonvisualaudio.audio.ffmpeg_runner import find_ffmpeg
    from nonvisualaudio.errors import MissingFFmpegError

    try:
        find_ffmpeg()
    except MissingFFmpegError:
        return False
    return True


_FFMPEG_AVAILABLE = _ffmpeg_available()
_skip_no_ffmpeg = pytest.mark.skipif(
    not _FFMPEG_AVAILABLE, reason="no working ffmpeg on this machine"
)


def _write_wav_mono(path: Path, sr: int, seconds: float, freq: float = 440.0) -> np.ndarray:
    """Write a deterministic mono WAV (PCM_16) and return its float32 samples."""
    import soundfile as sf

    n = int(seconds * sr)
    t = np.arange(n) / sr
    # Mix of three sines so the spectrum analyser has more than one
    # band to talk about, plus a tiny envelope so peak ≠ RMS exactly
    # like real material.
    sig = (
        0.45 * np.sin(2 * np.pi * freq * t)
        + 0.20 * np.sin(2 * np.pi * (freq * 3) * t)
        + 0.10 * np.sin(2 * np.pi * (freq / 4) * t)
    ).astype(np.float32)
    # PCM_16 is what most user-supplied WAV files actually use; the round-
    # trip introduces a tiny quantisation but is the same for batch and
    # streaming so the comparison still resolves to bit-identical.
    sf.write(str(path), sig, sr, subtype="PCM_16")
    # Read back so the comparison reference is what the decoder will see
    # after quantisation — not the unquantised float32 we synthesised.
    return sf.read(str(path), dtype="float32", always_2d=False)[0]


def _write_wav_stereo(
    path: Path, sr: int, seconds: float
) -> tuple[np.ndarray, np.ndarray]:
    """Write a deterministic stereo WAV and return ``(stereo, mono_mixdown)``."""
    import soundfile as sf

    n = int(seconds * sr)
    t = np.arange(n) / sr
    # Two slightly different signals on L/R so the stereo analyser has
    # a non-trivial correlation, and a fixed phase offset on R so mono
    # drop is finite-but-not-zero.
    left = (0.4 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    right = (0.4 * np.sin(2 * np.pi * 440.0 * t + 0.5)).astype(np.float32)
    stereo = np.column_stack((left, right)).astype(np.float32)
    sf.write(str(path), stereo, sr, subtype="PCM_16")
    # Read back through the same PCM_16 round-trip the decoder will see.
    raw = sf.read(str(path), dtype="float32", always_2d=True)[0]
    stereo_round = raw[:, :2].astype(np.float32, copy=False)
    mono_round = stereo_round.mean(axis=1, dtype=np.float32)
    return stereo_round, mono_round


# --------------------------------------------------------------------------- #
# soundfile streaming path
# --------------------------------------------------------------------------- #


def test_streaming_sf_mono_pcm_matches_batch(tmp_path: Path) -> None:
    """Mono WAV: assembled streamed chunks must match the batch decoder's
    mono buffer bit-for-bit. Loudness output must also match exactly —
    both paths invoke the same ffmpeg ebur128 binary."""
    wav = tmp_path / "mono.wav"
    _write_wav_mono(wav, SR, 2.5)

    batch_decoded, batch_loud = decode_and_measure(wav)
    rec = _RecordingSink()
    sinks = StreamingSinks(feed_mono=rec.feed_mono)
    info, streamed_loud = decode_and_measure_streaming(wav, sinks)

    assert isinstance(info, StreamingDecodeInfo)
    assert info.sample_rate == SR
    assert info.channels == 1
    assert info.filename == wav.name
    assert info.duration_seconds == pytest.approx(
        batch_decoded.duration_seconds, abs=1e-9
    )
    assert info.bit_depth == batch_decoded.bit_depth

    streamed_mono = rec.assemble_mono()
    assert streamed_mono.dtype == np.float32
    assert streamed_mono.shape == batch_decoded.samples.shape
    # Bit-identical: same file, same backend (libsndfile), same dtype.
    assert np.array_equal(streamed_mono, batch_decoded.samples)
    # Stereo sink wasn't provided, so nothing should have arrived there.
    assert rec.stereo_chunks == []

    assert streamed_loud == batch_loud


def test_streaming_sf_stereo_pcm_matches_batch(tmp_path: Path) -> None:
    """Stereo WAV: stereo + mono mixdown both have to match the batch
    decoder bit-for-bit. The mono mixdown is computed by averaging
    axis=1 in float32 — same operation in both paths, so bit-exact."""
    wav = tmp_path / "stereo.wav"
    _write_wav_stereo(wav, SR, 2.0)

    batch_decoded, batch_loud = decode_and_measure(wav)
    rec = _RecordingSink()
    sinks = StreamingSinks(
        feed_mono=rec.feed_mono, feed_stereo_optional=rec.feed_stereo
    )
    info, streamed_loud = decode_and_measure_streaming(wav, sinks)

    assert info.channels == 2
    assert batch_decoded.stereo_samples is not None
    streamed_stereo = rec.assemble_stereo()
    assert streamed_stereo is not None
    assert streamed_stereo.shape == batch_decoded.stereo_samples.shape
    assert np.array_equal(streamed_stereo, batch_decoded.stereo_samples)

    streamed_mono = rec.assemble_mono()
    assert streamed_mono.shape == batch_decoded.samples.shape
    assert np.array_equal(streamed_mono, batch_decoded.samples)

    assert streamed_loud == batch_loud


def test_streaming_sf_skips_stereo_when_no_sink_provided(tmp_path: Path) -> None:
    """When ``feed_stereo_optional`` is ``None`` the decoder still has to
    deliver the mono mixdown — but it must not blow up trying to call a
    sink it does not have, and it must not allocate stereo storage we
    are not going to consume."""
    wav = tmp_path / "stereo.wav"
    _, expected_mono = _write_wav_stereo(wav, SR, 1.0)

    rec = _RecordingSink()
    sinks = StreamingSinks(feed_mono=rec.feed_mono)  # no stereo
    info, _loud = decode_and_measure_streaming(wav, sinks)

    assert info.channels == 2  # source is stereo
    assert rec.stereo_chunks == []  # but we opted out of the L/R branch
    streamed_mono = rec.assemble_mono()
    assert np.array_equal(streamed_mono, expected_mono)


def test_streaming_sf_progress_callback_fires(tmp_path: Path) -> None:
    """Decoding and loudness should both produce progress events on the
    sf path. We don't pin the exact percentages — just that the stream
    eventually reaches 100 % in each stage."""
    wav = tmp_path / "mono.wav"
    _write_wav_mono(wav, SR, 2.0)

    events: list[tuple[int, str]] = []
    rec = _RecordingSink()
    sinks = StreamingSinks(feed_mono=rec.feed_mono)
    decode_and_measure_streaming(
        wav, sinks, on_progress=lambda pct, key: events.append((pct, key))
    )

    decoding = [pct for pct, key in events if key == "decoding"]
    loudness = [pct for pct, key in events if key == "loudness"]
    assert decoding, "no decoding progress events"
    assert loudness, "no loudness progress events"
    # Decoding is driven by frames_done / total_frames so it deterministically
    # hits 100 %. Loudness comes from ffmpeg ebur128's per-frame ``t:`` markers
    # which can stop one frame short of the file end — accept anything past 90.
    assert max(decoding) == 100
    assert max(loudness) >= 90


def test_streaming_sf_chunks_arrive_in_order(tmp_path: Path) -> None:
    """The streaming decoder must hand the sinks chunks in time order.
    Verifies that concatenating the chunks produces the same signal,
    not a reshuffled one."""
    wav = tmp_path / "ramp.wav"
    # A monotonic ramp gives us an unambiguous order check: if any chunk
    # arrived out of order, the concatenated buffer would have a step.
    n = SR  # 1 second
    ramp = np.linspace(-0.5, 0.5, n, dtype=np.float32)
    import soundfile as sf

    sf.write(str(wav), ramp, SR, subtype="PCM_16")
    expected = sf.read(str(wav), dtype="float32", always_2d=False)[0]

    rec = _RecordingSink()
    sinks = StreamingSinks(feed_mono=rec.feed_mono)
    decode_and_measure_streaming(wav, sinks)
    assert np.array_equal(rec.assemble_mono(), expected)
    # Strict monotonicity holds for the rounded ramp too — verify the
    # streamed assembly preserves it.
    assert np.all(np.diff(rec.assemble_mono()) >= 0)


# --------------------------------------------------------------------------- #
# ffmpeg streaming path
# --------------------------------------------------------------------------- #


@_skip_no_ffmpeg
def test_streaming_ffmpeg_mono_matches_batch(tmp_path: Path) -> None:
    """ffmpeg path — mono. We call the helper directly with a WAV so the
    test doesn't depend on having an MP3 generator. Both batch and
    streaming variants make the same combined ffmpeg invocation, so
    the produced PCM and loudness must match bit-for-bit."""
    wav = tmp_path / "mono.wav"
    _write_wav_mono(wav, SR, 1.5)

    sr, channels, duration = _probe_via_ffmpeg(wav)
    assert sr > 0

    batch_decoded, batch_loud = _ffmpeg_decode_with_loudness(
        wav, sr, channels, duration, None
    )
    rec = _RecordingSink()
    sinks = StreamingSinks(feed_mono=rec.feed_mono)
    info, streamed_loud = _ffmpeg_decode_with_loudness_streaming(
        wav, sr, channels, duration, sinks, None
    )

    assert isinstance(info, StreamingDecodeInfo)
    assert info.sample_rate == sr
    streamed_mono = rec.assemble_mono()
    assert streamed_mono.shape == batch_decoded.samples.shape
    assert np.array_equal(streamed_mono, batch_decoded.samples)
    assert streamed_loud == batch_loud


@_skip_no_ffmpeg
def test_streaming_ffmpeg_stereo_matches_batch(tmp_path: Path) -> None:
    """ffmpeg path — stereo. Same contract as the mono test, plus the
    stereo (n, 2) buffer must also reassemble identically."""
    wav = tmp_path / "stereo.wav"
    _write_wav_stereo(wav, SR, 1.5)

    sr, channels, duration = _probe_via_ffmpeg(wav)
    batch_decoded, batch_loud = _ffmpeg_decode_with_loudness(
        wav, sr, channels, duration, None
    )

    rec = _RecordingSink()
    sinks = StreamingSinks(
        feed_mono=rec.feed_mono, feed_stereo_optional=rec.feed_stereo
    )
    info, streamed_loud = _ffmpeg_decode_with_loudness_streaming(
        wav, sr, channels, duration, sinks, None
    )

    assert info.channels == 2
    streamed_stereo = rec.assemble_stereo()
    assert streamed_stereo is not None
    assert batch_decoded.stereo_samples is not None
    assert streamed_stereo.shape == batch_decoded.stereo_samples.shape
    assert np.array_equal(streamed_stereo, batch_decoded.stereo_samples)
    assert np.array_equal(rec.assemble_mono(), batch_decoded.samples)
    assert streamed_loud == batch_loud


@_skip_no_ffmpeg
def test_streaming_ffmpeg_progress_callback_fires(tmp_path: Path) -> None:
    """The ebur128 ``t:`` markers must produce ``combined`` progress
    events as the stream runs."""
    wav = tmp_path / "mono.wav"
    _write_wav_mono(wav, SR, 1.5)
    sr, channels, duration = _probe_via_ffmpeg(wav)

    events: list[tuple[int, str]] = []
    rec = _RecordingSink()
    sinks = StreamingSinks(feed_mono=rec.feed_mono)
    _ffmpeg_decode_with_loudness_streaming(
        wav,
        sr,
        channels,
        duration,
        sinks,
        lambda pct, key: events.append((pct, key)),
    )
    combined = [pct for pct, key in events if key == "combined"]
    assert combined, "no combined progress events"


# --------------------------------------------------------------------------- #
# End-to-end: streaming decoder → Phase 1 streamers → metrics
# --------------------------------------------------------------------------- #


def test_streaming_pipeline_matches_batch_pipeline_mono(tmp_path: Path) -> None:
    """File → streaming decoder → Phase 1 streamers must produce the
    same metrics as file → batch decoder → compute_*. This is the
    integration test for Phase 1 + Phase 2 — the whole reason the
    streamers and the streaming decoder exist."""
    wav = tmp_path / "mono.wav"
    _write_wav_mono(wav, SR, 3.0)

    # Reference: batch decode + batch analyzers.
    batch_decoded, _batch_loud = decode_and_measure(wav)
    batch_dyn = compute_dynamics(batch_decoded.samples, batch_decoded.sample_rate)
    batch_spec = compute_spectrum(batch_decoded.samples, batch_decoded.sample_rate)

    # Streaming.
    dyn_streamer = DynamicsStreamer(SR)
    spec_streamer = SpectrumStreamer(SR)

    def feed_mono(chunk: np.ndarray) -> None:
        dyn_streamer.feed(chunk)
        spec_streamer.feed(chunk)

    sinks = StreamingSinks(feed_mono=feed_mono)
    decode_and_measure_streaming(wav, sinks)

    streamed_dyn = dyn_streamer.finalize()
    streamed_spec = spec_streamer.finalize()

    assert streamed_dyn.peak_db == pytest.approx(batch_dyn.peak_db, abs=1e-6)
    assert streamed_dyn.rms_db == pytest.approx(batch_dyn.rms_db, abs=1e-6)
    assert streamed_dyn.crest_factor_db == pytest.approx(
        batch_dyn.crest_factor_db, abs=1e-6
    )
    assert streamed_dyn.dr_score == pytest.approx(batch_dyn.dr_score, abs=1e-6)
    for field in (
        "sub_db",
        "bass_db",
        "low_mid_db",
        "mid_db",
        "presence_db",
        "air_db",
    ):
        assert getattr(streamed_spec.bands, field) == pytest.approx(
            getattr(batch_spec.bands, field), abs=1e-6
        )
    assert len(streamed_spec.peaks) == len(batch_spec.peaks)
    for s_peak, b_peak in zip(streamed_spec.peaks, batch_spec.peaks):
        assert s_peak.frequency_hz == pytest.approx(
            b_peak.frequency_hz, abs=0.5
        )


def test_streaming_pipeline_matches_batch_pipeline_stereo(tmp_path: Path) -> None:
    """Same end-to-end check as the mono case, but with all three Phase 1
    streamers (dynamics, spectrum, stereo) fed from one streaming decode."""
    wav = tmp_path / "stereo.wav"
    _write_wav_stereo(wav, SR, 3.0)

    batch_decoded, _batch_loud = decode_and_measure(wav)
    batch_dyn = compute_dynamics(batch_decoded.samples, SR)
    batch_spec = compute_spectrum(batch_decoded.samples, SR)
    batch_stereo = compute_stereo(batch_decoded.stereo_samples, SR)

    dyn_streamer = DynamicsStreamer(SR)
    spec_streamer = SpectrumStreamer(SR)
    stereo_streamer = StereoStreamer(SR)

    def feed_mono(chunk: np.ndarray) -> None:
        dyn_streamer.feed(chunk)
        spec_streamer.feed(chunk)

    def feed_stereo(chunk: np.ndarray) -> None:
        stereo_streamer.feed(chunk)

    sinks = StreamingSinks(
        feed_mono=feed_mono, feed_stereo_optional=feed_stereo
    )
    decode_and_measure_streaming(wav, sinks)

    streamed_dyn = dyn_streamer.finalize()
    streamed_spec = spec_streamer.finalize()
    streamed_stereo = stereo_streamer.finalize()

    assert streamed_dyn == batch_dyn
    for field in (
        "sub_db",
        "bass_db",
        "low_mid_db",
        "mid_db",
        "presence_db",
        "air_db",
    ):
        assert getattr(streamed_spec.bands, field) == pytest.approx(
            getattr(batch_spec.bands, field), abs=1e-6
        )
    assert streamed_stereo.is_stereo is True
    assert streamed_stereo.mean_correlation == pytest.approx(
        batch_stereo.mean_correlation, abs=1e-6
    )
    assert streamed_stereo.min_correlation == pytest.approx(
        batch_stereo.min_correlation, abs=1e-6
    )
    assert streamed_stereo.mono_drop_db == pytest.approx(
        batch_stereo.mono_drop_db, abs=1e-6
    )
    assert streamed_stereo.side_to_mid_db == pytest.approx(
        batch_stereo.side_to_mid_db, abs=1e-6
    )


# --------------------------------------------------------------------------- #
# Regression / fallback guarantees
# --------------------------------------------------------------------------- #


def test_legacy_decode_still_returns_full_buffer(tmp_path: Path) -> None:
    """The non-streaming :func:`decode` must keep working untouched.
    Phase 2 is strictly additive: anyone still depending on the full
    in-memory ``DecodedAudio`` continues to receive it."""
    wav = tmp_path / "mono.wav"
    expected = _write_wav_mono(wav, SR, 0.5)
    decoded = decode(wav)
    assert decoded.samples.dtype == np.float32
    assert decoded.samples.shape == expected.shape
    assert np.array_equal(decoded.samples, expected)


def test_streaming_path_does_not_break_legacy_decode_and_measure(tmp_path: Path) -> None:
    """Running the streaming path first must not leave any module-level
    state that breaks a subsequent batch call (find_ffmpeg has cached
    state, soundfile keeps its own — neither should be perturbed).
    """
    wav = tmp_path / "mono.wav"
    _write_wav_mono(wav, SR, 0.5)

    rec = _RecordingSink()
    sinks = StreamingSinks(feed_mono=rec.feed_mono)
    decode_and_measure_streaming(wav, sinks)

    batch_decoded, _loud = decode_and_measure(wav)
    assert np.array_equal(rec.assemble_mono(), batch_decoded.samples)


def test_streaming_sinks_only_requires_feed_mono(tmp_path: Path) -> None:
    """``StreamingSinks(feed_mono=...)`` with the default ``None`` for
    the stereo field must be a complete, valid sink. Catches the
    regression where a code path expected the stereo field to exist as
    a callable.
    """
    wav = tmp_path / "stereo.wav"
    _write_wav_stereo(wav, SR, 0.5)
    rec = _RecordingSink()
    sinks = StreamingSinks(feed_mono=rec.feed_mono)
    assert sinks.feed_stereo_optional is None
    info, _ = decode_and_measure_streaming(wav, sinks)
    assert info.channels == 2
    assert rec.stereo_chunks == []
    assert rec.mono_chunks  # mono mixdown was still produced


def test_streaming_decode_info_dataclass_is_frozen() -> None:
    """The metadata return is meant to be immutable so callers cannot
    accidentally mutate it before passing to the report builder.
    """
    info = StreamingDecodeInfo(
        sample_rate=48000,
        channels=2,
        bit_depth=16,
        duration_seconds=1.5,
        filename="x.wav",
    )
    with pytest.raises((AttributeError, Exception)):
        info.channels = 1  # type: ignore[misc]


def test_streaming_mono_does_not_call_stereo_sink(tmp_path: Path) -> None:
    """When the source is mono, the decoder must NEVER call
    ``feed_stereo_optional`` — even if the caller provided one. That
    contract matters for Project mode, which provides a combined-stereo
    sink only when every track in the project is stereo: an accidental
    call with a (n, 1) array would mis-measure the combined stereo
    image."""
    wav = tmp_path / "mono.wav"
    _write_wav_mono(wav, SR, 0.4)

    rec = _RecordingSink()

    def feed_stereo(_chunk: np.ndarray) -> None:
        raise AssertionError(
            "feed_stereo_optional must not be called for a mono source"
        )

    sinks = StreamingSinks(
        feed_mono=rec.feed_mono, feed_stereo_optional=feed_stereo
    )
    info, _ = decode_and_measure_streaming(wav, sinks)
    assert info.channels == 1
    assert rec.mono_chunks  # something arrived on the mono path


def test_streaming_handles_short_under_one_block(tmp_path: Path) -> None:
    """Very short files must not break the streaming decoder. Half a
    second of audio is well below the soundfile blocksize floor, so
    sf.blocks yields a single block. Both the batch and streaming
    paths must still produce the same PCM and loudness."""
    wav = tmp_path / "short.wav"
    _write_wav_mono(wav, SR, 0.25)
    batch_decoded, batch_loud = decode_and_measure(wav)

    rec = _RecordingSink()
    sinks = StreamingSinks(feed_mono=rec.feed_mono)
    _info, loud = decode_and_measure_streaming(wav, sinks)
    assert np.array_equal(rec.assemble_mono(), batch_decoded.samples)
    assert loud == batch_loud


def test_streaming_at_44100_sample_rate_matches_batch(tmp_path: Path) -> None:
    """Sample-rate parametrisation. The blocksize floor only kicks in
    below 65 536 samples per second, so 44.1 kHz exercises a different
    block cadence than the 48 kHz tests above. The same bit-exact
    contract must still hold."""
    sr = 44100
    wav = tmp_path / "mono_441.wav"
    _write_wav_mono(wav, sr, 1.5)
    batch_decoded, batch_loud = decode_and_measure(wav)
    rec = _RecordingSink()
    sinks = StreamingSinks(feed_mono=rec.feed_mono)
    info, loud = decode_and_measure_streaming(wav, sinks)
    assert info.sample_rate == sr
    assert np.array_equal(rec.assemble_mono(), batch_decoded.samples)
    assert loud == batch_loud
