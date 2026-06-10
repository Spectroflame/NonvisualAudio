"""Build a diagnostic report for support requests.

Pure logic, no wx — so it stays unit-testable. The report bundles system
information with the current session's log file. Because that file is
written through the redacting formatter, the report contains no full paths
and no user name unless verbose logging was enabled when the lines were
written.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nonvisualaudio import __version__, logging_setup, preferences
from nonvisualaudio.audio import ffmpeg_runner
from nonvisualaudio.localization import current_lang
from nonvisualaudio.logging_setup import LOG_FILENAME
from nonvisualaudio.paths import user_log_dir

log = logging.getLogger("nonvisualaudio.diagnostics")

# How much of the session log the in-app viewer shows at most. Logs are
# fresh per session since 2.2, so a quarter megabyte covers all but
# pathological sessions while keeping a single read instant.
TAIL_LIMIT_BYTES = 256 * 1024

# A log line as written by logging_setup's file handler:
# "2026-06-10 12:00:00 LEVEL nonvisualaudio.x: message". Anything that
# does not match (traceback lines, wrapped text) is a continuation line.
_LOG_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (DEBUG|INFO|WARNING|ERROR|CRITICAL)\b"
)

_SEVERITY_BY_LEVEL = {
    "INFO": "info",
    "WARNING": "warning",
    "ERROR": "error",
    "CRITICAL": "error",
}


@dataclass(frozen=True)
class LogTail:
    """The tail of the session log, ready for read-only display."""

    text: str  # decoded tail; empty when missing/unreadable
    truncated: bool  # True when only the last section is included
    status: str  # "ok" | "missing" | "unreadable"


def read_log_tail(
    path: Path | None = None, limit: int = TAIL_LIMIT_BYTES
) -> LogTail:
    """Return the last ``limit`` bytes of the current session log.

    Reads only ``nonvisualaudio.log`` itself — never rotation backups or
    other files in the log folder. Never raises: a missing or unreadable
    file is reported through ``status`` so the caller can show a
    friendly message instead of crashing.
    """
    if path is None:
        path = user_log_dir() / LOG_FILENAME
    if not path.is_file():
        return LogTail("", False, "missing")
    try:
        with path.open("rb") as fh:
            size = path.stat().st_size
            truncated = size > limit
            if truncated:
                fh.seek(size - limit)
            raw = fh.read()
    except OSError as exc:
        log.warning("could not read session log: %s", exc)
        return LogTail("", False, "unreadable")
    text = raw.decode("utf-8", errors="replace")
    if truncated:
        # The byte cut almost certainly split a line (or a multi-byte
        # character); drop the partial first line so the view starts on
        # a complete one. A single line longer than the whole limit is
        # kept as-is — showing something beats showing nothing.
        newline = text.find("\n")
        if newline != -1:
            text = text[newline + 1 :]
    return LogTail(text, truncated, "ok")


def severity_of_log_line(line: str) -> str | None:
    """Classify a log line for the viewer's purely visual highlighting.

    Returns ``"info"``, ``"warning"``, ``"error"`` (covers CRITICAL),
    or ``None`` for DEBUG and for lines that do not start with the file
    handler's timestamp — continuation lines such as tracebacks, whose
    severity the caller may carry over from the preceding line.
    """
    match = _LOG_LINE_RE.match(line)
    if match is None:
        return None
    return _SEVERITY_BY_LEVEL.get(match.group(1))


def is_log_record(line: str) -> bool:
    """True when ``line`` starts a fresh record of the session-log format.

    Lets the viewer tell a DEBUG record (starts a record, but carries no
    highlight severity) apart from a continuation line such as a
    traceback frame (no record start — inherits the previous severity).
    """
    return _LOG_LINE_RE.match(line) is not None


def _log_files() -> list[Path]:
    """Return the current session's log file, if it exists.

    Since 2.2 the log is rewritten on every app start, so the report
    deliberately includes only ``nonvisualaudio.log``: exactly one session,
    never stale history from older runs or versions.
    """
    current = user_log_dir() / LOG_FILENAME
    return [current] if current.is_file() else []


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
    Folder" button reveals both the session log and the freshly written
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
