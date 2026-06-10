"""Estimate analysis RAM needs and ask the user before risky analyses.

Since 2.2 the analysis pipeline is streaming end to end: the decoder
hands each PCM chunk to the analyser accumulators and releases it, so
peak RAM no longer scales with the decoded file size. What remains is a
small, mostly duration-independent working set (see the constants
below). The 2.1 batch pipeline needed 5-6× the decoded buffer — about
4 GB per stereo hour at 48 kHz — which is what this guard was built to
warn about.

This module:

- estimates the peak RAM an analysis pass needs from a file's metadata,
- compares the estimate against the system's available memory, and
- exposes a callback hook the worker uses to ask the user before going
  ahead.

With streaming estimates the warning gate effectively never fires on a
healthy system — that is the intended outcome of the RAM hardening, not
a gap. The gate machinery stays in place as a safety net: it still
catches genuinely starved machines (hundreds of MB free), and any
future analysis stage that re-introduces a duration-dependent buffer
only has to raise the estimate to get the warning back.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("nonvisualaudio.memory")


# --------------------------------------------------------------------------- #
# Tuning constants
# --------------------------------------------------------------------------- #

# Fixed working set of one streaming pass, independent of file length:
#
#   - one decoder chunk in flight (1 MiB ffmpeg pipe chunk, or one
#     ~1-second soundfile block — ≈1.5 MiB float32 at 192 kHz stereo),
#   - its float64 promotions inside the three streamers (a few ×),
#   - the streamers' carries (≤3 s dynamics block + ≤4096-sample
#     spectrum segment + ≤0.1 s stereo block, all float64),
#   - numpy allocator slack on top.
#
# Measured peaks sit well under 16 MiB; 64 MiB keeps the published
# number conservative without ever looking scary in the dialog.
STREAMING_BASE_BYTES = 64 * 1024 * 1024

# The only duration-dependent state: the per-block reduction arrays the
# dynamics streamer (one float64 per 3 s) and the stereo streamer (two
# float64 per 0.1 s) accumulate for their finalize percentiles. That is
# ≈0.6 MB per audio hour; 4 MB/h leaves room for list-of-chunks
# overhead and keeps the estimate monotonic in duration.
STREAMING_BYTES_PER_HOUR = 4 * 1024 * 1024

# Warning gate (see ``MemoryEstimate.is_concerning``). We require *both*
# signals to look tight before bothering the user:
#
#   - estimate ≥ ``WARN_FRACTION_OF_AVAILABLE`` of currently-free RAM, AND
#   - estimate ≥ ``WARN_FRACTION_OF_TOTAL`` of total physical RAM.
#
# Available alone is unreliable on macOS, where ``vm_stat`` excludes
# evictable "active" pages and can underreport by tens of gigabytes on a
# roomy box. Total alone is too coarse — a 30-minute analysis on a busy
# 16 GB machine looks fine by total but might still push the working set
# into swap. Demanding both keeps the warning rare on 64 GB systems and
# still useful on 8 / 16 GB ones.
#
# ``WARN_ABSOLUTE_BYTES`` is the absolute last-resort fallback for the
# case where neither probe worked. Modern macOS, Windows and Linux all
# return at least one of them, so this path is essentially unreachable.
WARN_FRACTION_OF_AVAILABLE = 0.5
WARN_FRACTION_OF_TOTAL = 0.4
WARN_ABSOLUTE_BYTES = 4_000_000_000  # 4 GB


# --------------------------------------------------------------------------- #
# Public dataclass + exception
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MemoryEstimate:
    """A single RAM forecast for the next analysis pass.

    ``available_bytes`` and ``total_bytes`` may be ``None`` when the
    platform probe fails. The UI should display "unknown" in that case
    rather than pretending it has the data.
    """

    label: str
    estimated_bytes: int
    available_bytes: int | None
    total_bytes: int | None

    @property
    def is_concerning(self) -> bool:
        """True when the user should be asked before proceeding.

        Strategy: warn only when *both* the available-memory signal and
        the total-RAM signal agree the analysis is tight. Available
        alone is unreliable on macOS — ``vm_stat`` treats active pages
        as unavailable even when the kernel would happily evict them,
        so a 64 GB Mac with most pages "active" can report 5-10 GB free
        while still having plenty of headroom. Crossing the available
        fraction is therefore necessary but not sufficient: we sanity-
        check against total before warning. The absolute threshold only
        fires when both system probes failed (a true edge case).
        """
        if self.available_bytes is not None and self.available_bytes > 0:
            if (
                self.estimated_bytes
                < self.available_bytes * WARN_FRACTION_OF_AVAILABLE
            ):
                return False
            if self.total_bytes is not None and self.total_bytes > 0:
                return (
                    self.estimated_bytes
                    >= self.total_bytes * WARN_FRACTION_OF_TOTAL
                )
            return True
        if self.total_bytes is not None and self.total_bytes > 0:
            return (
                self.estimated_bytes
                >= self.total_bytes * WARN_FRACTION_OF_TOTAL
            )
        return self.estimated_bytes >= WARN_ABSOLUTE_BYTES


ConfirmMemoryCb = Callable[[MemoryEstimate], bool]


class RamCheckCancelled(Exception):
    """Raised when the RAM-guard confirm callback returns ``False``.

    The worker turns this into a clean, user-friendly cancellation
    message instead of letting it surface as a generic error.
    """


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #


def format_bytes(n: int | None) -> str:
    """Human-friendly size string. Returns ``?`` when ``n`` is None.

    The decimal point is localised so a German UI reads "1,2 GB" while
    English reads "1.2 GB" — matching the rest of the report formatter.
    """
    if n is None:
        return "?"
    # Local import: avoids a circular import at module load time and
    # keeps this helper usable from tests that don't load i18n.
    from nonvisualaudio.localization import decimal_sep

    units = ("B", "kB", "MB", "GB", "TB")
    value = float(n)
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    if unit == "B":
        return f"{int(value)} {unit}"
    if value >= 100:
        formatted = f"{value:.0f}"
    elif value >= 10:
        formatted = f"{value:.1f}".replace(".", decimal_sep())
    else:
        formatted = f"{value:.2f}".replace(".", decimal_sep())
    return f"{formatted} {unit}"


def format_seconds(seconds: float | None) -> str:
    """Compact duration: ``12,3 s`` or ``2 min 05 s``."""
    if seconds is None:
        return "?"
    from nonvisualaudio.localization import decimal_sep

    if seconds < 60:
        formatted = f"{seconds:.1f}".replace(".", decimal_sep())
        return f"{formatted} s"
    minutes = int(seconds // 60)
    rest = int(round(seconds - minutes * 60))
    return f"{minutes} min {rest:02d} s"


# --------------------------------------------------------------------------- #
# Estimation
# --------------------------------------------------------------------------- #


def _probe_audio_info(path: Path) -> tuple[float, int, int] | None:
    """Return ``(duration_seconds, sample_rate, channels)`` without loading samples.

    Cheap path: ``soundfile.info`` for WAV/FLAC/AIFF. Fallback: a short
    ffmpeg probe that already lives in the decoder. We tolerate every
    failure mode and return None so the estimator can fall back to
    file-size heuristics.

    Channel count drives the stereo branch of the size estimate, so a
    probe that succeeds at duration/sample-rate but cannot extract a
    channel count returns ``channels=0``; the estimator then defaults
    to the conservative stereo multiplier.
    """
    try:
        import soundfile as sf

        info = sf.info(str(path))
        if info.frames and info.samplerate:
            return (
                float(info.frames) / float(info.samplerate),
                int(info.samplerate),
                int(info.channels or 0),
            )
    except Exception as exc:  # noqa: BLE001 — probe is best-effort
        log.debug("soundfile probe failed for %s: %s", path.name, exc)
    try:
        # Imported lazily because the decoder module imports from us in
        # the wider pipeline; we don't need the import at module load.
        from nonvisualaudio.audio.decoder import _probe_via_ffmpeg

        sample_rate, channels, duration = _probe_via_ffmpeg(path)
        if sample_rate and duration:
            return float(duration), int(sample_rate), int(channels or 0)
    except Exception as exc:  # noqa: BLE001 — probe is best-effort
        log.debug("ffmpeg probe failed for %s: %s", path.name, exc)
    return None


def _streaming_estimate(duration_seconds: float) -> int:
    """Peak working set of one streaming pass over ``duration_seconds``."""
    hours = max(0.0, duration_seconds) / 3600.0
    return STREAMING_BASE_BYTES + int(hours * STREAMING_BYTES_PER_HOUR)


def estimate_file_bytes(path: str | Path) -> int:
    """Estimate peak RAM during the streaming analysis of one file.

    The streaming pipeline never materialises the decoded file, so the
    estimate is a flat base plus a small per-hour accumulator term —
    duration only matters through the latter. When the probe fails we
    return the base alone: an unprobeable file still streams chunk by
    chunk, so its peak is no different.
    """
    probed = _probe_audio_info(Path(path))
    if probed is None:
        return _streaming_estimate(0.0)
    duration, _sample_rate, _channels = probed
    return _streaming_estimate(duration)


def estimate_project_bytes(paths: list[str] | list[str | Path]) -> int:
    """Estimate peak RAM for a project-mode analysis over ``paths``.

    Tracks are analysed sequentially (each one a streaming pass) and
    the combined measurement is one more streaming pass over the
    concatenation, so the peak is a single pass whose accumulators span
    the total project duration — not a sum of per-track buffers.
    """
    total_seconds = 0.0
    for raw in paths:
        probed = _probe_audio_info(Path(raw))
        if probed is not None:
            total_seconds += probed[0]
    return _streaming_estimate(total_seconds)


def build_estimate(label: str, estimated_bytes: int) -> MemoryEstimate:
    """Bundle an estimate with the current system memory snapshot."""
    return MemoryEstimate(
        label=label,
        estimated_bytes=estimated_bytes,
        available_bytes=available_memory_bytes(),
        total_bytes=total_memory_bytes(),
    )


# --------------------------------------------------------------------------- #
# System memory probes
# --------------------------------------------------------------------------- #


def _read_meminfo() -> dict[str, int]:
    """Parse ``/proc/meminfo`` into a dict of byte values. Linux-only."""
    try:
        text = Path("/proc/meminfo").read_text()
    except OSError:
        return {}
    out: dict[str, int] = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z()_]+):\s+(\d+)\s*(\S+)?", line)
        if not m:
            continue
        key = m.group(1)
        value = int(m.group(2))
        unit = (m.group(3) or "").lower()
        if unit == "kb":
            value *= 1024
        elif unit == "mb":
            value *= 1024 * 1024
        out[key] = value
    return out


def _macos_available() -> int | None:
    try:
        proc = subprocess.run(
            ["/usr/bin/vm_stat"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    page_match = re.search(r"page size of (\d+) bytes", proc.stdout)
    page_size = int(page_match.group(1)) if page_match else 4096
    pages = 0
    for label in ("Pages free", "Pages inactive", "Pages speculative"):
        m = re.search(rf"{label}:\s+(\d+)", proc.stdout)
        if m:
            pages += int(m.group(1))
    if pages == 0:
        return None
    return pages * page_size


def _windows_memory_status() -> tuple[int, int] | None:
    """Return ``(total_bytes, available_bytes)`` via GlobalMemoryStatusEx."""
    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return int(stat.ullTotalPhys), int(stat.ullAvailPhys)
    except Exception as exc:  # noqa: BLE001 — Win32 call is best-effort
        log.debug("GlobalMemoryStatusEx failed: %s", exc)
    return None


def available_memory_bytes() -> int | None:
    """Best-effort: return the bytes currently free for new allocations."""
    if sys.platform.startswith("linux"):
        info = _read_meminfo()
        if "MemAvailable" in info:
            return info["MemAvailable"]
        if "MemFree" in info:
            return info["MemFree"]
    if sys.platform == "darwin":
        avail = _macos_available()
        if avail is not None:
            return avail
    if sys.platform.startswith("win"):
        status = _windows_memory_status()
        if status is not None:
            return status[1]
    # POSIX fallback — works on some Linux variants when /proc isn't there.
    try:
        return int(os.sysconf("SC_AVPHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (ValueError, OSError, AttributeError):
        return None


def total_memory_bytes() -> int | None:
    """Best-effort total physical RAM."""
    if sys.platform == "darwin":
        try:
            proc = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if proc.returncode == 0 and proc.stdout.strip().isdigit():
                return int(proc.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    if sys.platform.startswith("linux"):
        info = _read_meminfo()
        if "MemTotal" in info:
            return info["MemTotal"]
    if sys.platform.startswith("win"):
        status = _windows_memory_status()
        if status is not None:
            return status[0]
    try:
        return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (ValueError, OSError, AttributeError):
        return None


# --------------------------------------------------------------------------- #
# Process RSS — used for the per-analysis "RAM peak" log line
# --------------------------------------------------------------------------- #


def peak_rss_bytes() -> int | None:
    """Return this process's peak resident set size since launch.

    On Linux ``ru_maxrss`` is reported in kibibytes, on macOS in bytes —
    a long-standing platform quirk. We normalise to bytes so callers
    don't have to care. Returns None when the API isn't available.
    """
    try:
        import resource
    except ImportError:
        return None
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
    except OSError:
        return None
    if sys.platform == "darwin":
        return int(usage.ru_maxrss)
    # Linux + most other Unixes.
    return int(usage.ru_maxrss) * 1024
