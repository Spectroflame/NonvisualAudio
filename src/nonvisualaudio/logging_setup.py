"""Central logging configuration: a rotating file log plus path redaction.

Every module already logs through ``logging.getLogger("nonvisualaudio.*")``.
``app._configure_logging`` wires a stderr handler; this module adds a rotating
file handler on top so support cases can be diagnosed after the fact, even in
a bundled app where stderr is invisible to the user.

Privacy: by default the file log is *redacted* — the user's home directory is
collapsed to ``~`` and any other absolute path is reduced to its final
component, so neither the user name nor a full file path is written. Verbose
logging (full paths) can be turned on by the user in the diagnostics dialog;
the choice persists in ``preferences.json``.
"""

from __future__ import annotations

import logging
import logging.handlers
import platform
import re
import sys
import threading
from pathlib import Path

from nonvisualaudio.paths import user_log_dir

log = logging.getLogger("nonvisualaudio.logging")

LOG_FILENAME = "nonvisualaudio.log"
_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 4

# Toggled at runtime by set_verbose(); read by the RedactingFormatter when it
# formats a record. Past records already written to disk are never rewritten.
_verbose = False

# The user's home directory, the most privacy-sensitive substring because it
# embeds the account name. Captured once at import time.
_HOME = str(Path.home())

# An absolute path: an optional Windows drive, a leading separator, one or
# more intermediate segments, and a final component. The lookbehind keeps it
# from biting into URLs (``https://...``), words, or a path's own inner
# separators. ``~``-relative paths are left untouched on purpose — they carry
# no user name and aid debugging.
_ABS_PATH_RE = re.compile(
    r"(?<![\w:~\\/])"
    r"(?:[A-Za-z]:)?"
    r"[\\/]"
    r"(?:[^\\/\s]+[\\/])+"
    r"[^\\/\s]+"
)


# --------------------------------------------------------------------------- #
# Verbose toggle
# --------------------------------------------------------------------------- #


def set_verbose(enabled: bool) -> None:
    """Enable or disable full-path logging for records formatted from now on."""
    global _verbose
    _verbose = bool(enabled)


def is_verbose() -> bool:
    return _verbose


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #


def _basename(path: str) -> str:
    """Return the final component of a path, splitting on either separator."""
    return re.split(r"[\\/]", path)[-1] or path


def redact(text: str) -> str:
    """Strip the user name and full file paths from a formatted log line.

    Returns ``text`` unchanged when verbose logging is enabled.
    """
    if _verbose:
        return text
    if _HOME and _HOME in text:
        text = text.replace(_HOME, "~")
    return _ABS_PATH_RE.sub(lambda m: _basename(m.group(0)), text)


class RedactingFormatter(logging.Formatter):
    """A formatter that redacts paths from the fully rendered log line.

    Redaction happens on the formatted string — after ``%`` args have been
    interpolated — so paths passed as arguments are caught too. The log record
    itself is never mutated, leaving the stderr handler's output unchanged.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #


def _load_verbose_pref() -> bool:
    try:
        from nonvisualaudio import preferences

        return preferences.load_verbose_logging()
    except Exception:  # noqa: BLE001 — never let logging setup crash the app
        return False


def init_file_logging() -> Path | None:
    """Attach a rotating file handler to the root logger.

    Returns the log file path, or ``None`` if the file could not be opened
    (e.g. no write permission) — the app still starts with stderr logging.

    The stderr handler installed by :func:`logging.basicConfig` keeps its
    original threshold: this function pins it explicitly before lowering the
    root level so the file handler can receive ``INFO`` records.
    """
    set_verbose(_load_verbose_pref())
    try:
        log_dir = user_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / LOG_FILENAME
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
    except OSError as exc:
        print(f"NonvisualAudio: could not open log file: {exc}", file=sys.stderr)
        return None

    handler.setLevel(logging.INFO)
    handler.setFormatter(
        RedactingFormatter(
            fmt="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger()
    if root.level == logging.NOTSET or root.level > logging.INFO:
        # Preserve every existing handler's effective threshold before we
        # lower the root level, so the stderr handler keeps behaving as it
        # did (WARNING by default, DEBUG under NVA_DEBUG).
        for existing in root.handlers:
            if existing.level == logging.NOTSET:
                existing.setLevel(root.level or logging.WARNING)
        root.setLevel(logging.INFO)
    root.addHandler(handler)
    return log_path


def log_session_banner() -> None:
    """Write a one-line banner so each app run is easy to find in the log."""
    from nonvisualaudio import __version__
    from nonvisualaudio.localization import current_lang

    logging.getLogger("nonvisualaudio").info(
        "=== NonvisualAudio %s started — %s %s, Python %s (%s), lang=%s ===",
        __version__,
        platform.system(),
        platform.release(),
        platform.python_version(),
        platform.machine(),
        current_lang(),
    )


def install_excepthooks() -> None:
    """Route otherwise-uncaught exceptions through the log as a safety net.

    The wx main loop already funnels callback exceptions through
    ``NonvisualAudioApp.OnExceptionInMainLoop``; these hooks cover crashes
    outside it — startup, shutdown, and background threads.
    """
    nva_log = logging.getLogger("nonvisualaudio")

    def _hook(exc_type, exc, tb):  # noqa: ANN001
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        nva_log.critical("uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = _hook

    def _thread_hook(args):  # noqa: ANN001
        if issubclass(args.exc_type, KeyboardInterrupt):
            return
        name = args.thread.name if args.thread else "?"
        nva_log.critical(
            "uncaught exception in thread %s",
            name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_hook
