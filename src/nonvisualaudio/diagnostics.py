"""Build a diagnostic report for support requests.

Pure logic, no wx — so it stays unit-testable. The report bundles system
information with the contents of the rotating log files. Because those files
are written through the redacting formatter, the report contains no full
paths and no user name unless verbose logging was enabled when the lines were
written.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from nonvisualaudio import __version__, logging_setup, preferences
from nonvisualaudio.audio import ffmpeg_runner
from nonvisualaudio.localization import current_lang
from nonvisualaudio.logging_setup import LOG_FILENAME
from nonvisualaudio.paths import user_log_dir

log = logging.getLogger("nonvisualaudio.diagnostics")


def _log_files() -> list[Path]:
    """Return existing log files oldest-first: rotated backups, then current.

    Rotation names backups ``nonvisualaudio.log.1`` (newest) … ``.N`` (oldest),
    so a reverse name sort yields oldest-first; the live file goes last.
    """
    log_dir = user_log_dir()
    current = log_dir / LOG_FILENAME
    try:
        backups = sorted(
            log_dir.glob(LOG_FILENAME + ".*"),
            key=lambda p: p.name,
            reverse=True,
        )
    except OSError:
        backups = []
    return [p for p in [*backups, current] if p.is_file()]


def system_info() -> str:
    """Return a short block of environment facts useful for triage."""
    ffmpeg = ffmpeg_runner.active_ffmpeg_info()
    if ffmpeg is None:
        ffmpeg_line = "(not resolved yet — no analysis has run this session)"
    else:
        path, source = ffmpeg
        # Redact the path the same way log lines are redacted, so this
        # field honours the verbose-logging preference too.
        ffmpeg_line = f"{source} ({logging_setup.redact(path)})"
    lines = [
        f"NonvisualAudio version : {__version__}",
        f"Operating system       : {platform.platform()}",
        f"Python                 : {platform.python_version()} ({platform.machine()})",
        f"Interface language     : {current_lang()}",
        f"Theme preference       : {preferences.load_theme() or 'auto'}",
        f"Verbose logging        : {'on' if preferences.load_verbose_logging() else 'off'}",
        f"ffmpeg in use          : {ffmpeg_line}",
    ]
    return "\n".join(lines)


def build_report() -> str:
    """Return the full diagnostic report as plain text."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = [
        "NonvisualAudio diagnostic report",
        f"Generated: {now}",
        "",
        system_info(),
        "",
    ]
    files = _log_files()
    if not files:
        parts.append("(no log files found)")
    else:
        for path in files:
            # One blank line as the visual separator; the filename on a
            # line of its own gives the screen reader a real chunk
            # boundary without the noisy "-----" frame.
            parts.append(f"Logfile: {path.name}")
            try:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                parts.append(f"(could not read {path.name}: {exc})")
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def default_report_path() -> Path:
    """Where the diagnostic report is written: the log folder, timestamped.

    Keeping the report next to ``nonvisualaudio.log`` means the user has one
    obvious place to look for everything support-related, and the "Open Log
    Folder" button reveals both the rotating log and the freshly written
    report side by side.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return user_log_dir() / f"NonvisualAudio-Diagnose-{stamp}.log"


def _open_on_linux(target: str) -> bool:
    """Try the common Linux openers in order, returning success.

    ``xdg-open`` is the de-facto standard but isn't always installed
    (minimal distros, headless boxes). ``gio open`` is the modern
    GNOME/glib equivalent and ships with virtually every desktop Linux
    that has a file manager. Each candidate that's missing raises
    FileNotFoundError; non-zero exits are treated as failure too.
    """
    for argv in (["xdg-open", target], ["gio", "open", target]):
        try:
            subprocess.run(argv, check=True, timeout=5)
            return True
        except FileNotFoundError:
            continue
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("opener %s failed: %s", argv[0], exc)
            continue
    return False


def open_log_folder() -> bool:
    """Open the log directory in the OS file manager. Returns success."""
    log_dir = user_log_dir()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("could not create log folder: %s", exc)
        return False
    try:
        if sys.platform == "darwin":
            subprocess.run(["/usr/bin/open", str(log_dir)], check=False)
        elif sys.platform.startswith("win"):
            os.startfile(str(log_dir))  # type: ignore[attr-defined]  # noqa: S606
        else:
            if not _open_on_linux(str(log_dir)):
                log.error("no usable file-manager opener found on PATH")
                return False
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("could not open log folder: %s", exc)
        return False
    log.info("opened log folder for the user")
    return True
