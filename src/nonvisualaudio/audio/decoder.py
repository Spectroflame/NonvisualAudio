"""Decode audio files to mono float32 PCM.

Strategy:
1. Try libsndfile (via ``soundfile``). Handles WAV, AIFF, FLAC, OGG natively.
2. On failure, fall back to bundled ffmpeg which handles MP3, M4A/AAC, Opus
   and anything else ffmpeg knows about.

All user-visible errors are raised as :class:`AudioDecodeError` so the UI
can show a human-readable headline with file name and a concrete hint.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from nonvisualaudio.analysis.result import LoudnessMetrics
from nonvisualaudio.audio.ffmpeg_runner import (
    FFmpegError,
    find_ffmpeg,
    run,
    run_split_streams_streaming,
)
from nonvisualaudio.cancellation import Cancellation, CancelledError
from nonvisualaudio.errors import AudioDecodeError, MissingFFmpegError
from nonvisualaudio.localization import t

DecodeProgressCb = Callable[[int, str], None]

log = logging.getLogger("nonvisualaudio.decoder")


@dataclass(frozen=True)
class DecodedAudio:
    samples: np.ndarray  # shape: (n_samples,), dtype float32, range roughly [-1, 1]
    sample_rate: int
    channels: int
    bit_depth: int | None
    duration_seconds: float
    filename: str
    # Two-channel float32 buffer of shape (n_samples, 2) for stereo files,
    # used by the stereo-image analyser. None for mono / multichannel /
    # anything we deliberately chose not to split. The downstream stereo
    # analyser falls back to "no measurement" when this is None.
    stereo_samples: np.ndarray | None = None


def _to_mono(samples: np.ndarray) -> np.ndarray:
    if samples.ndim == 1:
        return samples.astype(np.float32, copy=False)
    return np.mean(samples, axis=1, dtype=np.float32)


def _try_soundfile(path: Path) -> DecodedAudio | None:
    """Return decoded audio on success, None if soundfile can't handle it."""
    try:
        import soundfile as sf
    except ImportError as exc:
        # Surface this loud enough to appear in support logs: a broken
        # libsndfile install means every analysis falls back to ffmpeg
        # decoding, which is noticeably slower on large files.
        log.warning("soundfile not importable; falling back to ffmpeg decoder: %s", exc)
        return None
    try:
        info = sf.info(str(path))
    except Exception as exc:  # noqa: BLE001 — broad on purpose: fall back to ffmpeg
        log.debug("soundfile.info rejected %s: %s", path.name, exc)
        return None
    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001 — same reason as above
        log.debug("soundfile.read rejected %s: %s", path.name, exc)
        return None
    mono = _to_mono(data)
    # Keep the two-channel buffer alongside the mono one when the source is
    # stereo, so the stereo-image analyser can read correlation and mono
    # compatibility from it. We deliberately ignore >2-channel material —
    # the L/R-only metrics do not generalise to surround layouts.
    stereo = (
        np.ascontiguousarray(data[:, :2], dtype=np.float32)
        if data.ndim == 2 and data.shape[1] == 2
        else None
    )
    bit_depth = _bit_depth_from_subtype(info.subtype)
    return DecodedAudio(
        samples=mono,
        sample_rate=int(sr),
        channels=int(info.channels),
        bit_depth=bit_depth,
        duration_seconds=float(len(mono)) / float(sr) if sr else 0.0,
        filename=path.name,
        stereo_samples=stereo,
    )


def _bit_depth_from_subtype(subtype: str | None) -> int | None:
    if not subtype:
        return None
    table = {
        "PCM_S8": 8,
        "PCM_U8": 8,
        "PCM_16": 16,
        "PCM_24": 24,
        "PCM_32": 32,
        "FLOAT": 32,
        "DOUBLE": 64,
    }
    return table.get(subtype)


# ffmpeg stderr fragments we parse to recover (sample_rate, channels,
# duration) without needing a separate ffprobe binary.
_FFMPEG_STREAM_RE = re.compile(
    r"Audio:\s*[^,]+,\s*(\d+)\s*Hz,\s*([^,]+?)(?:,|$)",
    re.IGNORECASE,
)
_FFMPEG_DURATION_RE = re.compile(
    r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

_CHANNEL_LAYOUT_COUNTS: dict[str, int] = {
    "mono": 1,
    "stereo": 2,
    "2.0": 2,
    "2.1": 3,
    "3.0": 3,
    "4.0": 4,
    "4.1": 5,
    "5.0": 5,
    "5.1": 6,
    "6.0": 6,
    "6.1": 7,
    "7.0": 7,
    "7.1": 8,
    "quad": 4,
    "downmix": 2,
}


def _parse_channel_layout(text: str) -> int:
    text = text.strip().lower()
    m = re.match(r"(\d+)\s*channels?", text)
    if m:
        try:
            return max(1, int(m.group(1)))
        except ValueError:
            pass
    return _CHANNEL_LAYOUT_COUNTS.get(text, 0)


def _probe_via_ffmpeg(
    path: Path, cancel: Cancellation | None = None
) -> tuple[int, int, float]:
    """Return (sample_rate, channels, duration_seconds) using ffmpeg."""
    args = [
        find_ffmpeg(),
        "-hide_banner",
        "-nostats",
        "-nostdin",
        "-i",
        str(path),
        "-t",
        "0.001",
        "-f",
        "null",
        "-",
    ]
    proc = run(args, timeout=60.0, cancel=cancel)
    stderr = proc.stderr.decode("utf-8", errors="replace")

    sample_rate = 0
    channels = 0
    m = _FFMPEG_STREAM_RE.search(stderr)
    if m:
        try:
            sample_rate = int(m.group(1))
        except ValueError:
            sample_rate = 0
        channels = _parse_channel_layout(m.group(2))

    duration = 0.0
    dm = _FFMPEG_DURATION_RE.search(stderr)
    if dm:
        try:
            h = int(dm.group(1))
            mi = int(dm.group(2))
            s = float(dm.group(3))
            duration = h * 3600 + mi * 60 + s
        except ValueError:
            duration = 0.0

    return sample_rate, channels, duration


def _ffmpeg_error_to_user_error(exc: FFmpegError, path: Path) -> AudioDecodeError:
    """Translate an internal FFmpegError into a user-facing AudioDecodeError."""
    raw = str(exc)
    name = path.name
    if raw.startswith("timeout:"):
        return AudioDecodeError(
            title=t("error.decoder.timeout.title", name=name),
            body=t("error.decoder.timeout.body"),
            hint=t("error.decoder.timeout.hint"),
        )
    if raw.startswith("binary_not_found:"):
        return AudioDecodeError(
            title=t("error.decoder.engine.title"),
            body=t("error.decoder.engine.body"),
            hint=t("error.decoder.engine.hint"),
        )
    # Generic ffmpeg failure: include the most informative part of stderr.
    stderr_tail = raw.split("\n", 1)[-1].strip()
    snippet = stderr_tail.splitlines()
    # ffmpeg often ends with the actionable line; take the last three non-empty.
    tail = " ".join(ln.strip() for ln in snippet if ln.strip())[-400:]
    hint = (
        t("error.decoder.generic.hint.tail", tail=tail)
        if tail
        else t("error.decoder.generic.hint.no_tail")
    )
    return AudioDecodeError(
        title=t("error.decoder.generic.title", name=name),
        body=t("error.decoder.generic.body"),
        hint=hint,
    )


def _ffmpeg_decode(path: Path) -> DecodedAudio:
    try:
        sample_rate, channels, duration = _probe_via_ffmpeg(path)
    except FFmpegError as exc:
        raise _ffmpeg_error_to_user_error(exc, path) from exc

    if sample_rate == 0:
        # ffmpeg accepted the input but reported no sample rate — treat as
        # unknown format rather than silently defaulting, so the user gets
        # told something is off.
        log.warning(
            "ffmpeg did not report a sample rate for %s; defaulting to 48000",
            path.name,
        )
        sample_rate = 48000

    # For stereo sources we decode the two channels and derive the mono
    # mixdown in-process. That saves one full ffmpeg pass (vs. running a
    # mono and a stereo decode back-to-back) and keeps RAM bounded to the
    # stereo buffer plus a transient mono copy.
    decode_channels = 2 if channels == 2 else 1
    args = [
        find_ffmpeg(),
        "-hide_banner",
        "-nostats",
        "-nostdin",
        "-i",
        str(path),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-ac",
        str(decode_channels),
        "-ar",
        str(sample_rate),
        "-",
    ]
    try:
        proc = run(args, timeout=600.0)
    except FFmpegError as exc:
        raise _ffmpeg_error_to_user_error(exc, path) from exc

    raw = proc.stdout
    interleaved = np.frombuffer(raw, dtype=np.float32)
    if interleaved.size == 0:
        raise AudioDecodeError(
            title=t("error.decoder.empty_output.title", name=path.name),
            body=t("error.decoder.empty_output.body"),
            hint=t("error.decoder.empty_output.hint"),
        )
    if decode_channels == 2:
        # Drop a stray trailing sample if ffmpeg emitted an odd count.
        usable = (interleaved.size // 2) * 2
        stereo = np.ascontiguousarray(
            interleaved[:usable].reshape(-1, 2)
        )
        samples = stereo.mean(axis=1, dtype=np.float32)
    else:
        samples = interleaved.copy()
        stereo = None
    return DecodedAudio(
        samples=samples,
        sample_rate=sample_rate,
        channels=channels or 1,
        bit_depth=None,
        duration_seconds=float(samples.size) / float(sample_rate),
        filename=path.name,
        stereo_samples=stereo,
    )


def _validate_file(path: str | Path) -> Path:
    """Raise AudioDecodeError when the path is not a usable audio file."""
    p = Path(path)
    if not p.exists():
        raise AudioDecodeError(
            title=t("error.decoder.missing.title", name=p.name),
            body=t("error.decoder.missing.body"),
            hint=t("error.decoder.missing.hint"),
        )
    if not p.is_file():
        raise AudioDecodeError(
            title=t("error.decoder.not_file.title", name=p.name),
            body=t("error.decoder.not_file.body"),
            hint=t("error.decoder.not_file.hint"),
        )
    try:
        if p.stat().st_size == 0:
            raise AudioDecodeError(
                title=t("error.decoder.empty.title", name=p.name),
                body=t("error.decoder.empty.body"),
                hint=t("error.decoder.empty.hint"),
            )
    except OSError as exc:
        raise AudioDecodeError(
            title=t("error.decoder.unreadable.title", name=p.name),
            body=t(
                "error.decoder.unreadable.body", details=exc.strerror or exc
            ),
            hint=t("error.decoder.unreadable.hint"),
        ) from exc
    return p


def decode(path: str | Path) -> DecodedAudio:
    """Decode an audio file to mono float32 PCM.

    Raises :class:`AudioDecodeError` for problems the user can act on
    (missing or unreadable file, unsupported format, empty audio) and
    :class:`MissingFFmpegError` if the audio engine itself is absent.
    """
    p = _validate_file(path)
    result = _try_soundfile(p)
    if result is not None:
        return result
    # Let MissingFFmpegError propagate as-is — it already carries good wording.
    try:
        return _ffmpeg_decode(p)
    except MissingFFmpegError:
        raise
    except AudioDecodeError:
        raise


def _ffmpeg_decode_with_loudness(
    path: Path,
    sample_rate: int,
    channels: int,
    duration: float,
    on_progress: DecodeProgressCb | None,
    cancel: Cancellation | None = None,
) -> tuple[DecodedAudio, LoudnessMetrics]:
    """One ffmpeg pass that produces PCM *and* the ebur128 summary.

    ffmpeg's ``asplit`` filter lets us fan the decoded audio out to two
    branches: one is muxed to stdout as raw float32 PCM, the other runs
    through ``ebur128`` whose progress and summary land in stderr. The
    caller's previous two-pass approach (decode + measure_loudness) read
    the source twice; for long files that doubled the work.

    The decoded PCM is streamed straight into a preallocated numpy
    buffer in chunks so we never materialise the multi-gigabyte stdout
    as a Python ``bytes`` object. The previous implementation built
    that bytes object, wrapped it in a view, *and* made a contiguous
    stereo copy — which roughly tripled peak RAM and pushed long files
    into swap on the way to OOM.
    """
    from nonvisualaudio.analysis.loudness import _parse as _parse_ebur128

    decode_channels = 2 if channels == 2 else 1
    args = [
        find_ffmpeg(),
        "-hide_banner",
        "-nostats",
        "-nostdin",
        "-i",
        str(path),
        "-filter_complex",
        "[0:a]asplit=2[pcm][ana];"
        "[ana]ebur128=peak=true:metadata=1:framelog=info[loud]",
        "-map", "[pcm]",
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        "-ac", str(decode_channels),
        "-ar", str(sample_rate),
        "pipe:1",
        "-map", "[loud]",
        "-f", "null",
        "-",
    ]

    # Live progress: parse the ``t:`` value out of each ebur128 progress
    # line and translate it into a 0..100 percentage against the probed
    # duration. The callback fires on the stderr reader thread, so it
    # must stay cheap.
    progress_state = {"last_pct": -1}

    def _on_line(raw_line: bytes) -> None:
        if on_progress is None or duration <= 0:
            return
        text = raw_line.decode("utf-8", errors="replace")
        # We import the regex lazily so the decoder module does not
        # need to depend on the loudness module at import time.
        from nonvisualaudio.analysis.loudness import _RE_FRAME_T

        m = _RE_FRAME_T.search(text)
        if m is None:
            return
        try:
            t_sec = float(m.group(1))
        except ValueError:
            return
        pct = int(100.0 * min(1.0, max(0.0, t_sec / duration)))
        if pct == progress_state["last_pct"]:
            return
        progress_state["last_pct"] = pct
        on_progress(pct, "combined")

    # Preallocate the PCM buffer based on the probed duration. ffmpeg can
    # emit slightly more or fewer frames than ``duration × sample_rate``
    # (encoder/decoder delay, VBR probe inaccuracy), so we reserve a
    # second of slack plus 1 %. If we still overshoot we grow the buffer
    # by doubling, which costs one extra allocation in the rare case.
    expected_frames = int(round(duration * sample_rate)) if duration > 0 else 0
    slack = max(int(expected_frames * 0.01), sample_rate)
    initial_capacity = max(expected_frames + slack, sample_rate)
    frame_bytes = decode_channels * 4

    if decode_channels == 2:
        buf = np.empty((initial_capacity, 2), dtype=np.float32)
    else:
        buf = np.empty(initial_capacity, dtype=np.float32)

    decode_state: dict[str, object] = {
        "buf": buf,
        "frames": 0,
        "leftover": b"",
    }

    def _on_chunk(chunk: bytes) -> None:
        leftover = decode_state["leftover"]
        if leftover:
            assert isinstance(leftover, bytes)
            data = leftover + chunk
        else:
            data = chunk
        usable_bytes = (len(data) // frame_bytes) * frame_bytes
        decode_state["leftover"] = data[usable_bytes:]
        if usable_bytes == 0:
            return
        arr = np.frombuffer(data, dtype=np.float32, count=usable_bytes // 4)
        if decode_channels == 2:
            arr = arr.reshape(-1, 2)
        n = arr.shape[0]
        current_buf = decode_state["buf"]
        assert isinstance(current_buf, np.ndarray)
        pos = int(decode_state["frames"])  # type: ignore[arg-type]
        needed = pos + n
        if needed > current_buf.shape[0]:
            new_capacity = max(needed, current_buf.shape[0] * 2)
            if decode_channels == 2:
                new_buf = np.empty((new_capacity, 2), dtype=np.float32)
            else:
                new_buf = np.empty(new_capacity, dtype=np.float32)
            new_buf[:pos] = current_buf[:pos]
            decode_state["buf"] = new_buf
            current_buf = new_buf
        current_buf[pos:pos + n] = arr
        decode_state["frames"] = pos + n

    try:
        stderr_text = run_split_streams_streaming(
            args,
            timeout=1200.0,
            stdout_chunk_handler=_on_chunk,
            stderr_line_callback=_on_line,
            cancel=cancel,
        )
    except FFmpegError as exc:
        raise _ffmpeg_error_to_user_error(exc, path) from exc

    n_frames = int(decode_state["frames"])  # type: ignore[arg-type]
    if n_frames == 0:
        raise AudioDecodeError(
            title=t("error.decoder.empty_output.title", name=path.name),
            body=t("error.decoder.empty_output.body"),
            hint=t("error.decoder.empty_output.hint"),
        )
    final_buf = decode_state["buf"]
    assert isinstance(final_buf, np.ndarray)
    # ``final_buf[:n_frames]`` is a contiguous view: numpy allocated the
    # parent as C-contiguous, and slicing the leading axis preserves that.
    # We keep the view (the slack tail is at most ~1 % + 1 second) instead
    # of forcing a copy — copying briefly doubles peak memory, which is
    # exactly what this rewrite is meant to avoid.
    if decode_channels == 2:
        stereo = final_buf[:n_frames]
        samples = stereo.mean(axis=1, dtype=np.float32)
    else:
        stereo = None
        samples = final_buf[:n_frames]

    decoded = DecodedAudio(
        samples=samples,
        sample_rate=sample_rate,
        channels=channels or 1,
        bit_depth=None,
        duration_seconds=float(samples.size) / float(sample_rate),
        filename=path.name,
        stereo_samples=stereo,
    )
    loudness = _parse_ebur128(stderr_text, path.name)
    return decoded, loudness


# --------------------------------------------------------------------------- #
# Streaming variant — Phase 2 of the end-to-end RAM rewrite.
#
# The streaming entry point :func:`decode_and_measure_streaming` mirrors the
# format coverage of :func:`decode_and_measure` (soundfile fast path for
# WAV/AIFF/FLAC/OGG, combined ffmpeg pass for everything else) but never
# materialises the full PCM buffer in Python. Each chunk of decoded audio
# is handed to the caller's :class:`StreamingSinks` and immediately
# released. The non-streaming :func:`decode_and_measure` and :func:`decode`
# functions above stay byte-for-byte unchanged so callers that still need
# a complete :class:`DecodedAudio` keep working.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StreamingSinks:
    """Callbacks the streaming decoder pumps PCM chunks into.

    ``feed_mono`` is invoked for every block of decoded audio. Mono inputs
    pass through unchanged; stereo (and >2-channel) inputs are mixed down
    inline so the sink always sees a 1-D float32 ndarray. Chunk size is
    decided by the underlying reader — ~1 MB for the ffmpeg pipe, ~1
    second for soundfile — and the sink must accept whatever cadence
    arrives.

    ``feed_stereo_optional`` is called only when the source is genuinely
    two-channel and the field is not ``None``. Pass ``None`` to opt out
    of the stereo branch entirely; the decoder then skips the L/R
    forwarding work but still produces the mono mixdown for ``feed_mono``.

    The dataclass exists so callers can hand bound methods of the Phase 1
    streamer classes straight in::

        sinks = StreamingSinks(
            feed_mono=dynamics_streamer.feed,
            feed_stereo_optional=stereo_streamer.feed,
        )
    """

    feed_mono: Callable[[np.ndarray], None]
    feed_stereo_optional: Callable[[np.ndarray], None] | None = None


# Factory variant of :class:`StreamingSinks`: called exactly once with
# ``(sample_rate, channels)`` as soon as the decoder knows the stream
# parameters and before the first chunk is fed. This lets callers build
# their Phase 1 streamer instances with the *actual* sample rate instead
# of probing the file a second time themselves.
SinksFactory = Callable[[int, int], "StreamingSinks"]


class PcmChunkRouter:
    """Route raw f32le PCM bytes from an ffmpeg pipe into :class:`StreamingSinks`.

    The pipe hands us arbitrary byte chunks, so a frame (one float32 per
    channel) can straddle a chunk boundary; the router keeps the sub-frame
    leftover between calls and re-prefixes it so every numpy view is
    frame-aligned. Stereo streams are forwarded to the stereo sink (when
    present) and mixed down inline for the mono sink — the exact behaviour
    the batch decoder implements for fully-materialised buffers.
    """

    def __init__(self, sinks: StreamingSinks, decode_channels: int) -> None:
        self._sinks = sinks
        self._channels = decode_channels
        self._frame_bytes = decode_channels * 4
        self._leftover = b""
        self._do_stereo = (
            decode_channels == 2 and sinks.feed_stereo_optional is not None
        )

    def __call__(self, chunk: bytes) -> None:
        data = self._leftover + chunk if self._leftover else chunk
        usable_bytes = (len(data) // self._frame_bytes) * self._frame_bytes
        self._leftover = data[usable_bytes:]
        if usable_bytes == 0:
            return
        arr = np.frombuffer(data, dtype=np.float32, count=usable_bytes // 4)
        if self._channels == 2:
            stereo_arr = arr.reshape(-1, 2)
            if self._do_stereo:
                feed_stereo = self._sinks.feed_stereo_optional
                assert feed_stereo is not None  # narrowed by _do_stereo
                feed_stereo(stereo_arr)
            self._sinks.feed_mono(stereo_arr.mean(axis=1, dtype=np.float32))
        else:
            self._sinks.feed_mono(arr)


@dataclass(frozen=True)
class StreamingDecodeInfo:
    """Metadata returned from a streaming decode.

    Holds everything :class:`DecodedAudio` carries *except* the PCM
    buffers — those have already been consumed by the sinks by the time
    the call returns. The shape matches :class:`FileInfo` on purpose so
    callers can build a ``FileInfo`` directly from these fields.
    """

    sample_rate: int
    channels: int
    bit_depth: int | None
    duration_seconds: float
    filename: str


# Hard floor on the soundfile streaming block size. 1 second at the
# source's sample rate is the normal cadence; at 8 kHz speech that drops
# to 8000 samples, where Python-side loop overhead starts to show. The
# floor keeps each block in the tens-of-thousands-of-samples range so
# numpy vector work dominates the per-block cost.
_SF_STREAM_BLOCKSIZE_FALLBACK = 65536


def _try_streaming_soundfile(
    p: Path,
    sinks: StreamingSinks | None,
    on_progress: DecodeProgressCb | None,
    sinks_factory: SinksFactory | None = None,
    cancel: Cancellation | None = None,
) -> tuple[StreamingDecodeInfo, LoudnessMetrics] | None:
    """Streaming counterpart of :func:`_try_soundfile`.

    Returns ``(info, loudness)`` on success, or ``None`` when soundfile
    cannot even probe the file (the caller then falls back to the ffmpeg
    path). Once probing succeeds we commit to soundfile: a mid-stream
    read failure raises :class:`AudioDecodeError` rather than rewinding
    silently, because the sinks have already received partial data and
    cannot be made to forget it.

    When ``sinks`` is ``None``, ``sinks_factory`` is called with the
    probed ``(sample_rate, channels)`` right before the first block is
    read. ``cancel`` is polled between blocks, so a cancellation lands
    within one block (~1 second of audio) instead of after the file.
    """
    try:
        import soundfile as sf
    except ImportError as exc:
        log.warning(
            "soundfile not importable; streaming decoder falls back to ffmpeg: %s",
            exc,
        )
        return None
    try:
        info = sf.info(str(p))
    except Exception as exc:  # noqa: BLE001 — fall back to ffmpeg on any sf rejection
        log.debug(
            "soundfile.info rejected %s in streaming path: %s", p.name, exc
        )
        return None

    sample_rate = int(info.samplerate or 0)
    if sample_rate <= 0:
        log.debug(
            "soundfile reported zero sample rate for %s — skipping sf path",
            p.name,
        )
        return None
    channels = int(info.channels or 0)
    total_frames = int(info.frames or 0)
    duration = (
        float(total_frames) / float(sample_rate) if total_frames > 0 else 0.0
    )
    bit_depth = _bit_depth_from_subtype(info.subtype)

    # Block size: ~1 second of audio, with the floor described above.
    blocksize = max(int(sample_rate), _SF_STREAM_BLOCKSIZE_FALLBACK)

    if sinks is None:
        assert sinks_factory is not None  # enforced by the entry point
        sinks = sinks_factory(sample_rate, channels)
    feed_stereo = sinks.feed_stereo_optional
    do_stereo = feed_stereo is not None and channels == 2

    frames_done = 0
    last_pct = -1
    try:
        for block in sf.blocks(
            str(p),
            blocksize=blocksize,
            dtype="float32",
            always_2d=True,
        ):
            if cancel is not None:
                cancel.raise_if_cancelled()
            if block.size == 0:
                continue
            if do_stereo:
                # When channels == 2 the block is already (n, 2). The
                # branching here is for forward-compat with hypothetical
                # 2.1/quad sources that soundfile might one day return
                # with a stereo subset request.
                stereo_block = (
                    np.ascontiguousarray(block[:, :2], dtype=np.float32)
                    if block.shape[1] > 2
                    else block
                )
                assert feed_stereo is not None  # narrowed by do_stereo
                feed_stereo(stereo_block)
                mono_block = stereo_block.mean(axis=1, dtype=np.float32)
            elif block.shape[1] == 1:
                # Mono source: drop the trivial second axis so the sink
                # receives a flat (n,) array, matching the ffmpeg path.
                mono_block = block[:, 0]
            else:
                # Multi-channel input we deliberately do not split. The
                # mono mixdown averages across every channel — matches
                # the batch ``_to_mono`` for the >2-channel case.
                mono_block = block.mean(axis=1, dtype=np.float32)
            sinks.feed_mono(mono_block)

            frames_done += block.shape[0]
            if on_progress is not None and total_frames > 0:
                pct = int(100 * frames_done / total_frames)
                if pct != last_pct:
                    last_pct = pct
                    on_progress(pct, "decoding")
    except CancelledError:
        # A cancel inside the block loop is a clean user action, not a
        # decode failure — let the worker's cancellation handling see it.
        raise
    except Exception as exc:  # noqa: BLE001 — surface as user-facing decode error
        # We already fed partial data to the sinks; falling back to
        # ffmpeg here would double-feed the same audio. Surface as a
        # generic decode error so the worker can show the user.
        raise AudioDecodeError(
            title=t("error.decoder.generic.title", name=p.name),
            body=t("error.decoder.generic.body"),
            hint=t("error.decoder.generic.hint.tail", tail=str(exc)),
        ) from exc

    # Loudness via ffmpeg ebur128 — same call the batch soundfile path
    # makes. This is a second read of the file, but it happens entirely
    # inside ffmpeg, never touches Python memory.
    from nonvisualaudio.analysis.loudness import measure_loudness

    loud_progress = (
        None
        if on_progress is None
        else (lambda pct: on_progress(pct, "loudness"))
    )
    loudness = measure_loudness(
        p, on_progress=loud_progress, duration_seconds=duration, cancel=cancel
    )

    return (
        StreamingDecodeInfo(
            sample_rate=sample_rate,
            channels=channels,
            bit_depth=bit_depth,
            duration_seconds=duration,
            filename=p.name,
        ),
        loudness,
    )


def _ffmpeg_decode_with_loudness_streaming(
    path: Path,
    sample_rate: int,
    channels: int,
    duration: float,
    sinks: StreamingSinks,
    on_progress: DecodeProgressCb | None,
    cancel: Cancellation | None = None,
) -> tuple[StreamingDecodeInfo, LoudnessMetrics]:
    """Streaming combined-pass twin of :func:`_ffmpeg_decode_with_loudness`.

    Same ffmpeg invocation as the batch sibling (one read, asplit fans
    out to stdout PCM and stderr ebur128), but the stdout chunk handler
    routes each frame batch into ``sinks`` and immediately releases it
    instead of growing a preallocated buffer. Peak RAM is bounded by the
    size of one stdout chunk plus whatever state the sinks keep — at
    typical 1 MB ffmpeg chunks and Phase 1 streamer states that means
    a few MB regardless of how many hours of audio go through.
    """
    from nonvisualaudio.analysis.loudness import _parse as _parse_ebur128

    decode_channels = 2 if channels == 2 else 1
    args = [
        find_ffmpeg(),
        "-hide_banner",
        "-nostats",
        "-nostdin",
        "-i",
        str(path),
        "-filter_complex",
        "[0:a]asplit=2[pcm][ana];"
        "[ana]ebur128=peak=true:metadata=1:framelog=info[loud]",
        "-map", "[pcm]",
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        "-ac", str(decode_channels),
        "-ar", str(sample_rate),
        "pipe:1",
        "-map", "[loud]",
        "-f", "null",
        "-",
    ]

    # Live progress from the ebur128 ``t:`` markers — same logic the
    # batch streaming variant uses. The callback fires on the stderr
    # reader thread, so it must stay cheap.
    progress_state = {"last_pct": -1}

    def _on_line(raw_line: bytes) -> None:
        if on_progress is None or duration <= 0:
            return
        text = raw_line.decode("utf-8", errors="replace")
        from nonvisualaudio.analysis.loudness import _RE_FRAME_T

        m = _RE_FRAME_T.search(text)
        if m is None:
            return
        try:
            t_sec = float(m.group(1))
        except ValueError:
            return
        pct = int(100.0 * min(1.0, max(0.0, t_sec / duration)))
        if pct == progress_state["last_pct"]:
            return
        progress_state["last_pct"] = pct
        on_progress(pct, "combined")

    try:
        stderr_text = run_split_streams_streaming(
            args,
            timeout=1200.0,
            stdout_chunk_handler=PcmChunkRouter(sinks, decode_channels),
            stderr_line_callback=_on_line,
            cancel=cancel,
        )
    except FFmpegError as exc:
        raise _ffmpeg_error_to_user_error(exc, path) from exc

    loudness = _parse_ebur128(stderr_text, path.name)
    return (
        StreamingDecodeInfo(
            sample_rate=sample_rate,
            channels=channels or 1,
            bit_depth=None,
            duration_seconds=duration,
            filename=path.name,
        ),
        loudness,
    )


def decode_and_measure_streaming(
    path: str | Path,
    sinks: StreamingSinks | None = None,
    on_progress: DecodeProgressCb | None = None,
    sinks_factory: SinksFactory | None = None,
    cancel: Cancellation | None = None,
) -> tuple[StreamingDecodeInfo, LoudnessMetrics]:
    """Streaming twin of :func:`decode_and_measure`.

    Decodes ``path`` end-to-end without ever materialising the full PCM
    buffer in Python. Each chunk of decoded audio is handed to
    ``sinks.feed_mono`` (always) and ``sinks.feed_stereo_optional``
    (only when the source is genuinely stereo and the field is not
    ``None``). The returned :class:`StreamingDecodeInfo` carries only
    the file's metadata — the samples are already inside the sinks.

    Exactly one of ``sinks`` / ``sinks_factory`` must be given. The
    factory form exists for callers that need the stream's sample rate
    to construct their sinks (the Phase 1 streamer classes take it as a
    constructor argument): it is invoked once with ``(sample_rate,
    channels)`` as soon as the decoder has probed the stream and before
    the first chunk is fed. ``channels`` is the source's channel count
    as probed — ``0`` when the probe could not tell, in which case the
    decode falls back to a mono mixdown and the stereo sink stays unfed.

    ``cancel`` — if supplied — is polled between decoded blocks, so a
    cancellation takes effect mid-file instead of after the decode.

    Format coverage matches :func:`decode_and_measure`: soundfile
    handles WAV / AIFF / FLAC / OGG natively, everything else routes
    through the combined ffmpeg pass. The legacy :func:`decode_and_measure`
    is left untouched as the non-streaming fallback for callers that
    still need a complete :class:`DecodedAudio`.
    """
    if (sinks is None) == (sinks_factory is None):
        raise ValueError(
            "decode_and_measure_streaming needs exactly one of "
            "sinks / sinks_factory"
        )
    p = _validate_file(path)
    sf_result = _try_streaming_soundfile(
        p, sinks, on_progress, sinks_factory=sinks_factory, cancel=cancel
    )
    if sf_result is not None:
        return sf_result

    sample_rate, channels, duration = _probe_via_ffmpeg(p, cancel=cancel)
    if sample_rate == 0:
        log.warning(
            "ffmpeg did not report a sample rate for %s; defaulting to 48000",
            p.name,
        )
        sample_rate = 48000
    if sinks is None:
        assert sinks_factory is not None  # checked at the top
        sinks = sinks_factory(sample_rate, channels)
    try:
        return _ffmpeg_decode_with_loudness_streaming(
            p, sample_rate, channels, duration, sinks, on_progress, cancel=cancel
        )
    except MissingFFmpegError:
        raise
    except AudioDecodeError:
        raise


def decode_and_measure(
    path: str | Path,
    on_progress: DecodeProgressCb | None = None,
    cancel: Cancellation | None = None,
) -> tuple[DecodedAudio, LoudnessMetrics]:
    """Decode the file *and* measure EBU R128 loudness in one go.

    For formats that libsndfile can read (WAV, AIFF, FLAC, OGG) we keep
    the legacy two-call shape — soundfile is fast enough that combining
    buys nothing and stalling the decode behind an ffmpeg pass would
    only make things slower. For everything that has to go through
    ffmpeg anyway (MP3, M4A, AAC, Opus, …) we run a single combined
    ffmpeg pass, which saves one full read of the source file. A 9-hour
    audiobook that previously needed two scans now needs one.

    ``on_progress(percent, stage_key)`` is fired live while the loudness
    scan runs. ``stage_key`` is one of ``"decoding"``, ``"loudness"``,
    or ``"combined"`` so the caller can pick the right label for the
    visible progress line.
    """
    p = _validate_file(path)
    # Local import keeps the decoder module light at import time and
    # avoids a circular import with the loudness module.
    from nonvisualaudio.analysis.loudness import measure_loudness

    sf_result = _try_soundfile(p)
    if sf_result is not None:
        if on_progress is not None:
            on_progress(0, "loudness")
        loud_progress = (
            None
            if on_progress is None
            else (lambda pct: on_progress(pct, "loudness"))
        )
        loudness = measure_loudness(
            p,
            on_progress=loud_progress,
            duration_seconds=sf_result.duration_seconds,
            cancel=cancel,
        )
        return sf_result, loudness

    sample_rate, channels, duration = _probe_via_ffmpeg(p, cancel=cancel)
    if sample_rate == 0:
        log.warning(
            "ffmpeg did not report a sample rate for %s; defaulting to 48000",
            p.name,
        )
        sample_rate = 48000
    try:
        return _ffmpeg_decode_with_loudness(
            p, sample_rate, channels, duration, on_progress, cancel=cancel
        )
    except MissingFFmpegError:
        raise
    except AudioDecodeError:
        raise
