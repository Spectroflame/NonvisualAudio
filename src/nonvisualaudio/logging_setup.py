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
import os
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
# from biting into URLs (``https://...``), words, dates (``12/06/2026``), or
# a path's own inner separators. ``~``-relative paths are left untouched on
# purpose — they carry no user name and aid debugging.
#
# Intermediate segments may contain single spaces (``/Volumes/My Drive/…``)
# as long as each space sits between words and the segment ends directly at
# its separator — so folder names with spaces are redacted too, while a
# lone ``/`` surrounded by spaces in prose never starts a match. A segment
# that contains a space must not contain a colon: real folder names cannot
# carry ``:`` on macOS or Windows, but prose like ``belegt: 1/2`` would
# otherwise be swallowed as a fake segment. The final component stays
# space-free: it is kept (as the basename) anyway, so absorbing trailing
# prose into it would gain nothing.
_ABS_PATH_RE = re.compile(
    r"(?<![\w:~\\/])"
    r"(?:[A-Za-z]:)?"
    r"[\\/]"
    r"(?:(?:[^\\/\s]+|[^\\/\s:]+(?: [^\\/\s:]+)*)[\\/])+"
    r"[^\\/\s]+"
)

# Any C0 control byte (includes \t \n \r and ESC, the start of an ANSI
# escape sequence), DEL, or a C1 control byte. Printable Unicode —
# umlauts, CJK, ordinary spaces — sits outside these ranges and is never
# touched. Used as a cheap guard so clean lines skip the per-character
# rewrite entirely.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _neutralize_control_chars(text: str) -> str:
    """Replace control characters with visible, inert escapes.

    A log line is one physical line. A filename — or any other logged
    value — that carries a newline, carriage return, tab, ANSI escape, or
    other C0/C1 control byte must not be able to forge a second,
    fully-formed log line, nor drive a terminal that later renders the
    log. ``\\n`` / ``\\r`` / ``\\t`` collapse to their two-character
    backslash escapes; every other control code (ESC included, so an ANSI
    sequence's leading byte becomes literal text, plus the C1 range)
    becomes ``\\xHH``. Printable Unicode is returned unchanged, so umlauts
    and spaces in a path stay readable.
    """
    if not text or not _CONTROL_CHAR_RE.search(text):
        return text
    out: list[str] = []
    for ch in text:
        if ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        else:
            cp = ord(ch)
            if cp < 0x20 or cp == 0x7F or 0x80 <= cp <= 0x9F:
                out.append(f"\\x{cp:02x}")
            else:
                out.append(ch)
    return "".join(out)


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


def path_for_log(path: str | Path) -> str:
    """Render a filesystem path for a log line, honouring the verbose toggle.

    With verbose logging off (the default) only the final path component
    is returned — e.g. ``Take 1.wav`` — so neither the user name nor any
    folder names ever reach the log. Unlike the line-level :func:`redact`
    regex, which is a heuristic safety net, this is exact regardless of
    spacing or unusual characters in the path.

    With verbose logging on, the full, unredacted path is returned. That
    switch is opt-in: the user turns it on themselves in the diagnostics
    dialog, typically right before sending a support log, so logging the
    complete path is exactly what they asked for in that case.

    Call this at the log site for import/export paths instead of passing
    the raw path; it is independent of, and complementary to, the
    formatter-level redaction applied to the file handler.
    """
    text = str(path)
    if _verbose:
        return _neutralize_control_chars(text)
    basename = os.path.basename(text.rstrip("/\\")) or text
    return _neutralize_control_chars(basename)


class RedactingFormatter(logging.Formatter):
    """A formatter that redacts paths from the fully rendered log line.

    Redaction happens on the formatted string — after ``%`` args have been
    interpolated — so paths passed as arguments are caught too. The log record
    itself is never mutated, leaving the stderr handler's output unchanged.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Neutralise control characters in the *interpolated message*
        # before the base formatter runs, so a crafted value (a filename
        # with an embedded newline, an ANSI escape, …) cannot forge an
        # extra, fully-formed log line or drive a terminal. We scrub the
        # message only — never the timestamp/level prefix the base
        # formatter prepends, nor a multi-line traceback it appends, whose
        # real newlines must survive for the diagnostics viewer to read
        # them line by line. record.msg/args are restored afterwards so
        # the shared record stays untouched for the stderr handler.
        original_msg = record.msg
        original_args = record.args
        record.msg = _neutralize_control_chars(record.getMessage())
        record.args = None
        try:
            rendered = super().format(record)
        finally:
            record.msg = original_msg
            record.args = original_args
        return redact(rendered)


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

    # No "===" decoration around the banner: the log file also ends up
    # inside the user-facing diagnostic report, where a screen reader
    # would otherwise read the equals signs character by character.
    logging.getLogger("nonvisualaudio").info(
        "NonvisualAudio %s started — %s %s, Python %s (%s), lang=%s",
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
