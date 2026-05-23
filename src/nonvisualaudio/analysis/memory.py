"""Estimate analysis RAM needs and ask the user before risky analyses.

Long audio files decode to large mono float32 arrays — one hour at 48 kHz
is roughly 660 MB before any analysis allocates further temporary buffers.
On modest systems that can be enough to push the process into swap or, on
true low-RAM machines, hit the OOM killer mid-analysis.

This module:

- estimates the peak RAM an analysis pass needs from a file's metadata,
- compares the estimate against the system's available memory, and
- exposes a callback hook the worker uses to ask the user before going
  ahead.

The estimator is intentionally conservative. We never want to wave through
a file the OS would then kill, so the overhead factors err on the high
side; a "you may proceed" answer should always survive in practice.
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

# Each decoded mono sample is stored as float32 = 4 bytes.
BYTES_PER_SAMPLE = 4

# The decoded buffer is the dominant allocation, but spectrum (Welch /
# resample_poly) and dynamics each spawn temporary float64 copies. On
# measured runs the working set peaks at roughly 2.5× the decoded buffer;
# we round up to 3 so the estimate stays on the safe side.
SINGLE_FILE_OVERHEAD = 3.0

# Project mode keeps every per-track buffer AND the concatenated buffer
# alive at the same time (we need both for the per-file numbers and the
# combined dynamics/spectrum). The resampler also temporarily materialises
# a float64 copy of each track. 5× covers both effects with margin.
PROJECT_OVERHEAD = 5.0

# Warn when the estimate is at least half of currently free RAM, or
# when it crosses the absolute threshold below. The absolute threshold
# is the safety net for the case where we can't probe "available" — it
# still catches the obvious "this file is enormous" scenario.
WARN_FRACTION_OF_AVAILABLE = 0.5
WARN_ABSOLUTE_BYTES = 1_000_000_000  # 1 GB


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
        """True when the user should be asked before proceeding."""
        if self.estimated_bytes >= WARN_ABSOLUTE_BYTES:
            return True
        if self.available_bytes is not None and self.available_bytes > 0:
            return (
                self.estimated_bytes
                >= self.available_bytes * WARN_FRACTION_OF_AVAILABLE
            )
        return False


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


def _probe_duration_samplerate(path: Path) -> tuple[float, int] | None:
    """Return ``(duration_seconds, sample_rate)`` without loading samples.

    Cheap path: ``soundfile.info`` for WAV/FLAC/AIFF. Fallback: a short
    ffmpeg probe that already lives in the decoder. We tolerate every
    failure mode and return None so the estimator can fall back to
    file-size heuristics.
    """
    try:
        import soundfile as sf

        info = sf.info(str(path))
        if info.frames and info.samplerate:
            return float(info.frames) / float(info.samplerate), int(info.samplerate)
    except Exception as exc:  # noqa: BLE001 — probe is best-effort
        log.debug("soundfile probe failed for %s: %s", path.name, exc)
    try:
        # Imported lazily because the decoder module imports from us in
        # the wider pipeline; we don't need the import at module load.
        from nonvisualaudio.audio.decoder import _probe_via_ffmpeg

        sample_rate, _channels, duration = _probe_via_ffmpeg(path)
        if sample_rate and duration:
            return float(duration), int(sample_rate)
    except Exception as exc:  # noqa: BLE001 — probe is best-effort
        log.debug("ffmpeg probe failed for %s: %s", path.name, exc)
    return None


def estimate_file_bytes(
    path: str | Path, overhead: float = SINGLE_FILE_OVERHEAD
) -> int:
    """Estimate peak RAM during analysis of one file.

    Returns a conservative byte figure built from the decoded mono
    buffer size times an empirical ``overhead`` factor that covers the
    spectrum and dynamics intermediate arrays. Falls back to
    ``file_size × 4`` when metadata can't be probed — very rough, but
    better than reporting zero on weird inputs.
    """
    p = Path(path)
    probed = _probe_duration_samplerate(p)
    if probed is not None:
        duration, sample_rate = probed
        decoded = int(duration * sample_rate * BYTES_PER_SAMPLE)
        return max(decoded, 0) * max(int(round(overhead)), 1)
    try:
        return p.stat().st_size * 4
    except OSError:
        return 0


def estimate_project_bytes(paths: list[str] | list[str | Path]) -> int:
    """Estimate peak RAM for a project-mode analysis over ``paths``.

    Project mode keeps every decoded track AND the concatenated buffer
    in memory at the same time, plus the resampler holds a transient
    float64 copy of each track. ``PROJECT_OVERHEAD`` covers all three.
    """
    decoded_bytes = 0
    for raw in paths:
        decoded_bytes += estimate_file_bytes(raw, overhead=1.0)
    return decoded_bytes * max(int(round(PROJECT_OVERHEAD)), 1)


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
