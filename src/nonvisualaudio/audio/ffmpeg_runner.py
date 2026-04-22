"""Locate and run the bundled (or system) ffmpeg/ffprobe binary.

This wrapper is intentionally minimal and never passes arguments through a
shell — every call uses an argument list so filenames with spaces or unusual
characters are safe.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from nonvisualaudio.errors import MissingFFmpegError
from nonvisualaudio.localization import t

log = logging.getLogger("nonvisualaudio.ffmpeg")


class FFmpegError(RuntimeError):
    """Raised when an ffmpeg invocation fails for a recoverable reason.

    The audio layer re-wraps these into user-facing errors with filename
    context before they reach the UI.
    """


def _platform_dir() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "win"
    return "linux"


def _bundled_binary(name: str) -> Path | None:
    """Return the bundled binary path if present, else None."""
    exe = f"{name}.exe" if sys.platform.startswith("win") else name
    here = Path(__file__).resolve().parent.parent
    candidate = here / "resources" / "bin" / _platform_dir() / exe
    if candidate.is_file():
        return candidate
    return None


def _install_hint() -> str:
    if sys.platform == "darwin":
        return t("error.ffmpeg.install.darwin")
    if sys.platform.startswith("win"):
        return t("error.ffmpeg.install.windows")
    return t("error.ffmpeg.install.linux")


def find_ffmpeg() -> str:
    """Return the path to ffmpeg. Prefer bundled, fall back to PATH."""
    bundled = _bundled_binary("ffmpeg")
    if bundled is not None:
        return str(bundled)
    path = shutil.which("ffmpeg")
    if path:
        return path
    raise MissingFFmpegError(
        title=t("error.ffmpeg.missing.title"),
        body=t("error.ffmpeg.missing.body"),
        hint=_install_hint(),
    )


def run(args: Sequence[str], *, timeout: float = 300.0) -> subprocess.CompletedProcess:
    """Run a command and return the completed process.

    Captures both stdout and stderr as bytes. Raises ``FFmpegError`` on
    non-zero exit or timeout. The caller is responsible for parsing output
    and, if appropriate, re-wrapping the error with filename context before
    it reaches the user.
    """
    t0 = time.time()
    binary = Path(args[0]).name
    log.debug("exec %s %s", binary, " ".join(str(a) for a in args[1:]))
    try:
        proc = subprocess.run(
            list(args),
            check=False,
            capture_output=True,
            timeout=timeout,
            # Explicitly inherit a minimal environment: no proxies, etc.
            env={"PATH": os.environ.get("PATH", "")},
        )
    except FileNotFoundError as exc:
        log.error("%s not found on PATH", binary)
        raise FFmpegError(f"binary_not_found:{args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        log.error("%s timed out after %.1fs", binary, timeout)
        raise FFmpegError(f"timeout:{timeout}") from exc
    elapsed = time.time() - t0
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")
        log.error(
            "%s exited %d after %.2fs: %s",
            binary,
            proc.returncode,
            elapsed,
            stderr[:400],
        )
        raise FFmpegError(
            f"exit:{proc.returncode}\n{stderr}"
        )
    log.debug("%s done in %.2fs (%d bytes stdout)", binary, elapsed, len(proc.stdout))
    return proc
