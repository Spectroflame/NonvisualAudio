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

Short-term max is taken from the per-frame progress lines emitted by the
filter when ``framelog=info`` is passed. Those lines look like:

    [Parsed_ebur128_0 @ ...] t: 1.2  TARGET:-23 LUFS    M:-18.0 S:-18.0     I: -18.0 LUFS ...

Note that "LUFS" only appears once, after the I: value, so the S: regex
must not require "LUFS" as a suffix.

The same per-frame lines carry ``FTPK:`` (this frame's true peak) and
``TPK:`` (the running maximum). We scan ``FTPK`` to find *when* the
loudest true peak occurs and report that timestamp, which gives the
user a position to jump to in their DAW.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from pathlib import Path

from nonvisualaudio.analysis.result import LoudnessMetrics
from nonvisualaudio.audio.ffmpeg_runner import (
    FFmpegError,
    find_ffmpeg,
    run_split_streams,
)
from nonvisualaudio.errors import LoudnessMeasurementError
from nonvisualaudio.localization import t

LoudnessProgressCb = Callable[[int], None]

log = logging.getLogger("nonvisualaudio.loudness")

_RE_I = re.compile(r"\bI:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_RE_LRA = re.compile(r"\bLRA:\s*(-?\d+(?:\.\d+)?)\s*LU\b")
_RE_PEAK = re.compile(r"\bPeak:\s*(-?\d+(?:\.\d+)?)\s*dBFS")
_RE_SHORT_TERM = re.compile(r"\bS:\s*(-?\d+(?:\.\d+)?|-inf)")
# Per-frame readings used to locate *when* the loudest true peak occurs:
# ``t:`` is the frame timestamp, ``FTPK:`` the frame's true peak. ``\bFTPK``
# will not match the cumulative ``TPK:`` field — there is no word boundary
# inside "FTPK".
_RE_FRAME_T = re.compile(r"\bt:\s*(\d+(?:\.\d+)?)")
_RE_FRAME_TPK = re.compile(r"\bFTPK:\s*(-?\d+(?:\.\d+)?|-inf)")


def _peak_time(progress: str) -> float | None:
    """Return the timestamp of the loudest true-peak frame, in seconds.

    Scans the per-frame progress lines for the highest ``FTPK`` reading
    and returns the ``t:`` of that frame. The first frame wins on a tie,
    so the timestamp points at the onset of the loudest moment. Returns
    None when no frame carried a finite peak reading.
    """
    best_peak = -math.inf
    best_time: float | None = None
    for line in progress.splitlines():
        m_peak = _RE_FRAME_TPK.search(line)
        if m_peak is None or m_peak.group(1) == "-inf":
            continue
        try:
            peak = float(m_peak.group(1))
        except ValueError:
            continue
        if peak <= best_peak:
            continue
        m_time = _RE_FRAME_T.search(line)
        if m_time is None:
            continue
        try:
            best_time = float(m_time.group(1))
        except ValueError:
            continue
        best_peak = peak
    return best_time


def _parse(stderr_text: str, filename: str) -> LoudnessMetrics:
    summary_idx = stderr_text.rfind("Summary:")
    if summary_idx == -1:
        raise LoudnessMeasurementError(
            title=t("error.loudness.no_summary.title", name=filename),
            body=t("error.loudness.no_summary.body"),
            hint=t("error.loudness.no_summary.hint"),
        )
    progress = stderr_text[:summary_idx]
    summary = stderr_text[summary_idx:]

    m_i = _RE_I.search(summary)
    m_lra = _RE_LRA.search(summary)
    m_peak = _RE_PEAK.search(summary)
    if not (m_i and m_lra and m_peak):
        raise LoudnessMeasurementError(
            title=t("error.loudness.incomplete.title", name=filename),
            body=t("error.loudness.incomplete.body"),
            hint=t("error.loudness.incomplete.hint"),
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
        true_peak_time_seconds=_peak_time(progress),
    )


def _make_progress_line_callback(
    duration_seconds: float | None,
    on_progress: LoudnessProgressCb | None,
):
    """Build a stderr line handler that emits 0..100 percent live.

    ffmpeg's ebur128 filter writes a ``t:`` value in seconds on every
    progress line. With the file's duration known, we map that into a
    percentage and call back the caller. Returns ``None`` when there is
    nothing to wire up so :func:`run_split_streams` can skip the
    callback machinery entirely.
    """
    if on_progress is None or duration_seconds is None or duration_seconds <= 0:
        return None
    duration = float(duration_seconds)
    state = {"last": -1}

    def _handle(raw_line: bytes) -> None:
        text = raw_line.decode("utf-8", errors="replace")
        m = _RE_FRAME_T.search(text)
        if m is None:
            return
        try:
            t_sec = float(m.group(1))
        except ValueError:
            return
        pct = int(100.0 * min(1.0, max(0.0, t_sec / duration)))
        # Drop duplicates: ffmpeg emits roughly one progress line per
        # second of audio, so the UI gets a smooth tick without
        # hammering wx.CallAfter for unchanged values.
        if pct == state["last"]:
            return
        state["last"] = pct
        on_progress(pct)

    return _handle


def measure_loudness(
    path: str | Path,
    *,
    on_progress: LoudnessProgressCb | None = None,
    duration_seconds: float | None = None,
) -> LoudnessMetrics:
    """Run ffmpeg ebur128 on ``path`` and return the parsed metrics.

    ``on_progress`` is fired live while ffmpeg scans the file when
    ``duration_seconds`` is known — useful for very long inputs where
    the loudness pass would otherwise look frozen for minutes.
    """
    p = Path(path)
    args = [
        find_ffmpeg(),
        "-hide_banner",
        "-nostats",
        "-nostdin",
        "-i",
        str(p),
        "-af",
        # framelog=info is required for per-frame "S:" lines in ffmpeg 7.x;
        # metadata=1 alone no longer triggers them.
        "ebur128=peak=true:metadata=1:framelog=info",
        "-f",
        "null",
        "-",
    ]
    progress_handler = _make_progress_line_callback(duration_seconds, on_progress)
    try:
        _stdout, stderr = run_split_streams(
            args, timeout=1200.0, stderr_line_callback=progress_handler
        )
    except FFmpegError as exc:
        raw = str(exc)
        if raw.startswith("timeout:"):
            raise LoudnessMeasurementError(
                title=t("error.loudness.timeout.title", name=p.name),
                body=t("error.loudness.timeout.body"),
                hint=t("error.loudness.timeout.hint"),
            ) from exc
        raise LoudnessMeasurementError(
            title=t("error.loudness.generic.title", name=p.name),
            body=t("error.loudness.generic.body"),
            hint=t("error.loudness.generic.hint"),
        ) from exc
    return _parse(stderr, p.name)
