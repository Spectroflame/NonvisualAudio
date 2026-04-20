"""EBU R128 loudness measurement via ffmpeg's ebur128 filter.

We parse ffmpeg's stderr for the final Summary block, which looks like:

    [Parsed_ebur128_0 @ 0x...] Summary:

      Integrated loudness:
        I:         -23.0 LUFS
        Threshold: -33.0 LUFS

      Loudness range:
        LRA:         8.7 LU
        Threshold: -43.0 LUFS
        LRA low:   -27.3 LUFS
        LRA high:  -18.6 LUFS

      True peak:
        Peak:       -1.1 dBFS

Short-term max is taken from the last "t:" progress line emitted by the
filter when ``metadata=1`` is passed.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from nonvisualaudio.analysis.result import LoudnessMetrics
from nonvisualaudio.audio.ffmpeg_runner import FFmpegError, find_ffmpeg, run

_RE_I = re.compile(r"\bI:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_RE_LRA = re.compile(r"\bLRA:\s*(-?\d+(?:\.\d+)?)\s*LU\b")
_RE_PEAK = re.compile(r"\bPeak:\s*(-?\d+(?:\.\d+)?)\s*dBFS")
_RE_SHORT_TERM = re.compile(r"\bS:\s*(-?\d+(?:\.\d+)?|-inf)\s*LUFS")


def _parse(stderr_text: str) -> LoudnessMetrics:
    # Split stderr into progress half and summary half.
    summary_idx = stderr_text.rfind("Summary:")
    if summary_idx == -1:
        raise FFmpegError("ebur128 Summary block not found in ffmpeg output.")
    progress = stderr_text[:summary_idx]
    summary = stderr_text[summary_idx:]

    m_i = _RE_I.search(summary)
    m_lra = _RE_LRA.search(summary)
    m_peak = _RE_PEAK.search(summary)
    if not (m_i and m_lra and m_peak):
        raise FFmpegError("Could not parse I/LRA/Peak from ebur128 summary.")

    # Short-term maximum: scan all S: values in progress and take the largest.
    short_max = -math.inf
    for m in _RE_SHORT_TERM.finditer(progress):
        val = m.group(1)
        if val == "-inf":
            continue
        try:
            fval = float(val)
        except ValueError:
            continue
        if fval > short_max:
            short_max = fval
    if not math.isfinite(short_max):
        # Fall back to integrated if no short-term value was seen.
        short_max = float(m_i.group(1))

    return LoudnessMetrics(
        integrated_lufs=float(m_i.group(1)),
        short_term_max_lufs=float(short_max),
        true_peak_dbtp=float(m_peak.group(1)),
        loudness_range_lu=float(m_lra.group(1)),
    )


def measure_loudness(path: str | Path) -> LoudnessMetrics:
    """Run ffmpeg ebur128 on ``path`` and return the parsed metrics."""
    p = Path(path)
    args = [
        find_ffmpeg(),
        "-hide_banner",
        "-nostats",
        "-nostdin",
        "-i",
        str(p),
        "-af",
        "ebur128=peak=true:metadata=1",
        "-f",
        "null",
        "-",
    ]
    # ebur128 writes its summary to stderr, and exits 0 on success. The run
    # helper raises if returncode != 0, so we bypass that by catching and
    # rechecking — but in practice ebur128 is stable.
    try:
        proc = run(args, timeout=600.0)
        stderr = proc.stderr.decode("utf-8", errors="replace")
    except FFmpegError as exc:
        # Some builds emit warnings that still produce valid output; re-raise
        # the original error with context.
        raise FFmpegError(f"Loudness measurement failed: {exc}") from exc
    return _parse(stderr)
