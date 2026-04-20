"""Decode audio files to mono float32 PCM.

Strategy:
1. Try libsndfile (via ``soundfile``). Handles WAV, AIFF, FLAC, OGG natively.
2. On failure, fall back to bundled ffmpeg which handles MP3, M4A/AAC, Opus
   and anything else ffmpeg knows about.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

import re

from nonvisualaudio.audio.ffmpeg_runner import FFmpegError, find_ffmpeg, run


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
    try:
        import soundfile as sf
    except ImportError:
        return None
    try:
        info = sf.info(str(path))
    except Exception:
        return None
    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception:
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
    # Forms like "2 channels" or "1 channels".
    m = re.match(r"(\d+)\s*channels?", text)
    if m:
        try:
            return max(1, int(m.group(1)))
        except ValueError:
            pass
    return _CHANNEL_LAYOUT_COUNTS.get(text, 0)


def _probe_via_ffmpeg(path: Path) -> tuple[int, int, float]:
    """Return (sample_rate, channels, duration_seconds) using ffmpeg.

    Runs a tiny ffmpeg decode of the first 1 ms and parses the stream
    info from stderr. This avoids the need for a separate ``ffprobe``
    binary in the app bundle.
    """
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


def _ffmpeg_decode(path: Path) -> DecodedAudio:
    sample_rate, channels, duration = _probe_via_ffmpeg(path)
    if sample_rate == 0:
        # Fall back to a sensible default; ffmpeg will still resample to this.
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
    proc = run(args, timeout=600.0)
    raw = proc.stdout
    samples = np.frombuffer(raw, dtype=np.float32).copy()
    if samples.size == 0:
        raise FFmpegError(f"ffmpeg returned no audio samples for {path.name}")
    return DecodedAudio(
        samples=samples,
        sample_rate=sample_rate,
        channels=channels or 1,
        bit_depth=None,
        duration_seconds=float(samples.size) / float(sample_rate),
        filename=path.name,
    )


def decode(path: str | Path) -> DecodedAudio:
    """Decode an audio file to mono float32 PCM."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    result = _try_soundfile(p)
    if result is not None:
        return result
    return _ffmpeg_decode(p)
