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

import logging
import math
import re
from pathlib import Path

from nonvisualaudio.analysis.result import LoudnessMetrics
from nonvisualaudio.audio.ffmpeg_runner import FFmpegError, find_ffmpeg, run
from nonvisualaudio.errors import LoudnessMeasurementError

log = logging.getLogger("nonvisualaudio.loudness")

_RE_I = re.compile(r"\bI:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_RE_LRA = re.compile(r"\bLRA:\s*(-?\d+(?:\.\d+)?)\s*LU\b")
_RE_PEAK = re.compile(r"\bPeak:\s*(-?\d+(?:\.\d+)?)\s*dBFS")
_RE_SHORT_TERM = re.compile(r"\bS:\s*(-?\d+(?:\.\d+)?|-inf)\s*LUFS")


def _parse(stderr_text: str, filename: str) -> LoudnessMetrics:
    summary_idx = stderr_text.rfind("Summary:")
    if summary_idx == -1:
        raise LoudnessMeasurementError(
            title=f"Loudness measurement failed for {filename}",
            body=(
                "The audio engine finished, but did not emit the EBU R128 "
                "summary that NonvisualAudio reads the numbers from. The "
                "file may be silent or extremely short."
            ),
            hint="Check that the file is longer than a few seconds and actually contains audible audio.",
        )
    progress = stderr_text[:summary_idx]
    summary = stderr_text[summary_idx:]

    m_i = _RE_I.search(summary)
    m_lra = _RE_LRA.search(summary)
    m_peak = _RE_PEAK.search(summary)
    if not (m_i and m_lra and m_peak):
        raise LoudnessMeasurementError(
            title=f"Could not read loudness numbers for {filename}",
            body=(
                "The audio engine produced a summary but NonvisualAudio could "
                "not find one of the expected loudness values in it. This "
                "usually points to a non-standard or partial audio file."
            ),
            hint="Re-export the file as WAV or FLAC from your editor and try again.",
        )

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
        # Fall back to integrated if no short-term reading arrived.
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
    try:
        proc = run(args, timeout=600.0)
    except FFmpegError as exc:
        raw = str(exc)
        if raw.startswith("timeout:"):
            raise LoudnessMeasurementError(
                title=f"Loudness scan of {p.name} took too long",
                body=(
                    "The audio engine did not finish the EBU R128 loudness "
                    "scan within the allowed time. The file may be extremely "
                    "long or stored on a slow drive."
                ),
                hint="Copy the file to a local drive or trim it to a shorter segment.",
            ) from exc
        raise LoudnessMeasurementError(
            title=f"Loudness measurement failed for {p.name}",
            body=(
                "The audio engine exited with an error during the loudness "
                "scan. The file may be corrupt or in a format this ffmpeg "
                "build does not fully support."
            ),
            hint="Re-export the file as WAV or FLAC and try again.",
        ) from exc
    stderr = proc.stderr.decode("utf-8", errors="replace")
    return _parse(stderr, p.name)
