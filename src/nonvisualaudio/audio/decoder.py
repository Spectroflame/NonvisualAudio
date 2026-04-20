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
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from nonvisualaudio.audio.ffmpeg_runner import FFmpegError, find_ffmpeg, run
from nonvisualaudio.errors import AudioDecodeError, MissingFFmpegError

log = logging.getLogger("nonvisualaudio.decoder")


@dataclass(frozen=True)
class DecodedAudio:
    samples: np.ndarray  # shape: (n_samples,), dtype float32, range roughly [-1, 1]
    sample_rate: int
    channels: int
    bit_depth: int | None
    duration_seconds: float
    filename: str


def _to_mono(samples: np.ndarray) -> np.ndarray:
    if samples.ndim == 1:
        return samples.astype(np.float32, copy=False)
    return np.mean(samples, axis=1, dtype=np.float32)


def _try_soundfile(path: Path) -> DecodedAudio | None:
    """Return decoded audio on success, None if soundfile can't handle it."""
    try:
        import soundfile as sf
    except ImportError:
        log.debug("soundfile not importable; skipping fast path")
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
    bit_depth = _bit_depth_from_subtype(info.subtype)
    return DecodedAudio(
        samples=mono,
        sample_rate=int(sr),
        channels=int(info.channels),
        bit_depth=bit_depth,
        duration_seconds=float(len(mono)) / float(sr) if sr else 0.0,
        filename=path.name,
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
            title=f"Reading {name} took too long",
            body=(
                "The audio engine did not finish decoding this file within "
                "the allowed time. The file may be extremely long, corrupt, "
                "or stored on a slow drive."
            ),
            hint=(
                "Try copying the file to your local disk first, or trim it "
                "to a shorter segment."
            ),
        )
    if raw.startswith("binary_not_found:"):
        return AudioDecodeError(
            title="Audio engine could not be started",
            body=(
                "FFmpeg is installed on the system path but the operating "
                "system refused to launch it for decoding this file."
            ),
            hint=(
                "Quit NonvisualAudio, reinstall ffmpeg, and open the app "
                "again. If the problem persists, run NonvisualAudio from a "
                "terminal with NVA_DEBUG=1 set to see the exact error."
            ),
        )
    # Generic ffmpeg failure: include the most informative part of stderr.
    stderr_tail = raw.split("\n", 1)[-1].strip()
    snippet = stderr_tail.splitlines()
    # ffmpeg often ends with the actionable line; take the last three non-empty.
    tail = " ".join(ln.strip() for ln in snippet if ln.strip())[-400:]
    return AudioDecodeError(
        title=f"Could not decode {name}",
        body=(
            "The audio engine rejected this file. It may be corrupt, "
            "encrypted (for example a DRM-protected iTunes purchase), or "
            "in a format this ffmpeg build does not support."
        ),
        hint=(
            "Try re-exporting the file as WAV or FLAC from your editor. "
            f"Technical detail from ffmpeg: {tail}"
            if tail
            else "Try re-exporting the file as WAV or FLAC from your editor."
        ),
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
        "1",
        "-ar",
        str(sample_rate),
        "-",
    ]
    try:
        proc = run(args, timeout=600.0)
    except FFmpegError as exc:
        raise _ffmpeg_error_to_user_error(exc, path) from exc

    raw = proc.stdout
    samples = np.frombuffer(raw, dtype=np.float32).copy()
    if samples.size == 0:
        raise AudioDecodeError(
            title=f"{path.name} contains no audio",
            body=(
                "The audio engine opened the file but produced no samples. "
                "It may be an empty recording, a data-only container, or a "
                "video file without an audio track."
            ),
            hint="Pick a different file, or check the export settings that produced this one.",
        )
    return DecodedAudio(
        samples=samples,
        sample_rate=sample_rate,
        channels=channels or 1,
        bit_depth=None,
        duration_seconds=float(samples.size) / float(sample_rate),
        filename=path.name,
    )


def decode(path: str | Path) -> DecodedAudio:
    """Decode an audio file to mono float32 PCM.

    Raises :class:`AudioDecodeError` for problems the user can act on
    (missing or unreadable file, unsupported format, empty audio) and
    :class:`MissingFFmpegError` if the audio engine itself is absent.
    """
    p = Path(path)
    if not p.exists():
        raise AudioDecodeError(
            title=f"{p.name} is not on disk anymore",
            body=(
                "The file you picked could not be found at the original "
                "location. It was probably moved, renamed, or deleted after "
                "you added it to the list."
            ),
            hint="Clear the file list and add the audio file again.",
        )
    if not p.is_file():
        raise AudioDecodeError(
            title=f"{p.name} is not a regular file",
            body=(
                "The chosen path points to a folder, a device, or some other "
                "non-file object instead of an audio file."
            ),
            hint="Pick an actual audio file such as a WAV, MP3, FLAC, or M4A.",
        )
    try:
        if p.stat().st_size == 0:
            raise AudioDecodeError(
                title=f"{p.name} is empty",
                body="The file has a size of zero bytes and cannot be decoded.",
                hint="Re-export or re-download the file, then try again.",
            )
    except OSError as exc:
        raise AudioDecodeError(
            title=f"Cannot read {p.name}",
            body=(
                "The operating system refused to read this file. Permissions "
                f"may be restricted, or the drive may have disconnected. "
                f"Details: {exc.strerror or exc}."
            ),
            hint="Check that you have read access to the file and that the drive is available.",
        ) from exc

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
