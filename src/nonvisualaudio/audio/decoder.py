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
    run_split_streams,
)
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


def _probe_via_ffmpeg(path: Path) -> tuple[int, int, float]:
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
    proc = run(args, timeout=60.0)
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
) -> tuple[DecodedAudio, LoudnessMetrics]:
    """One ffmpeg pass that produces PCM *and* the ebur128 summary.

    ffmpeg's ``asplit`` filter lets us fan the decoded audio out to two
    branches: one is muxed to stdout as raw float32 PCM, the other runs
    through ``ebur128`` whose progress and summary land in stderr. The
    caller's previous two-pass approach (decode + measure_loudness) read
    the source twice; for long files that doubled the work.

    The function is intentionally inside the decoder module so that the
    soundfile fast-path can still skip ffmpeg entirely — the combined
    pass is only reached when ffmpeg is going to decode the file anyway.
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
    state = {"last_pct": -1}

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
        if pct == state["last_pct"]:
            return
        state["last_pct"] = pct
        on_progress(pct, "combined")

    try:
        stdout_data, stderr_text = run_split_streams(
            args, timeout=1200.0, stderr_line_callback=_on_line
        )
    except FFmpegError as exc:
        raise _ffmpeg_error_to_user_error(exc, path) from exc

    interleaved = np.frombuffer(stdout_data, dtype=np.float32)
    if interleaved.size == 0:
        raise AudioDecodeError(
            title=t("error.decoder.empty_output.title", name=path.name),
            body=t("error.decoder.empty_output.body"),
            hint=t("error.decoder.empty_output.hint"),
        )

    if decode_channels == 2:
        usable = (interleaved.size // 2) * 2
        stereo = np.ascontiguousarray(interleaved[:usable].reshape(-1, 2))
        samples = stereo.mean(axis=1, dtype=np.float32)
    else:
        samples = interleaved.copy()
        stereo = None

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


def decode_and_measure(
    path: str | Path,
    on_progress: DecodeProgressCb | None = None,
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
        )
        return sf_result, loudness

    sample_rate, channels, duration = _probe_via_ffmpeg(p)
    if sample_rate == 0:
        log.warning(
            "ffmpeg did not report a sample rate for %s; defaulting to 48000",
            p.name,
        )
        sample_rate = 48000
    try:
        return _ffmpeg_decode_with_loudness(
            p, sample_rate, channels, duration, on_progress
        )
    except MissingFFmpegError:
        raise
    except AudioDecodeError:
        raise
