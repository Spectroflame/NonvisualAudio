"""Tests for the central logging system: log paths, redaction, diagnostics."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from nonvisualaudio import diagnostics, logging_setup, paths


@pytest.fixture(autouse=True)
def _reset_verbose() -> None:
    """Keep the module-level verbose flag from leaking between tests."""
    logging_setup.set_verbose(False)
    yield
    logging_setup.set_verbose(False)


# --------------------------------------------------------------------------- #
# user_log_dir
# --------------------------------------------------------------------------- #


def test_user_log_dir_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", lambda: Path("/Users/test"))
    assert paths.user_log_dir() == Path(
        "/Users/test/Library/Logs/NonvisualAudio"
    )


def test_user_log_dir_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths.platform, "system", lambda: "Windows")
    monkeypatch.setattr(Path, "home", lambda: Path("/home/test"))
    monkeypatch.setenv("LOCALAPPDATA", "/c/Local")
    assert paths.user_log_dir() == Path("/c/Local/NonvisualAudio/Logs")


def test_user_log_dir_linux_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths.platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "home", lambda: Path("/home/test"))
    monkeypatch.setenv("XDG_STATE_HOME", "/home/test/.state")
    assert paths.user_log_dir() == Path(
        "/home/test/.state/NonvisualAudio/logs"
    )


def test_user_log_dir_linux_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths.platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "home", lambda: Path("/home/test"))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert paths.user_log_dir() == Path(
        "/home/test/.local/state/NonvisualAudio/logs"
    )


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #


def test_redact_collapses_absolute_path_to_basename() -> None:
    out = logging_setup.redact("failed on /var/data/secret/song.wav now")
    assert "/var/data/secret" not in out
    assert "song.wav" in out


def test_redact_strips_user_name_under_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging_setup, "_HOME", "/Users/alice")
    out = logging_setup.redact("opened /Users/alice/Music/take one.wav")
    assert "alice" not in out


def test_redact_leaves_urls_intact() -> None:
    url = "https://github.com/Spectroflame/NonvisualAudio/issues/new"
    assert logging_setup.redact(f"opening issues page: {url}") == (
        f"opening issues page: {url}"
    )


def test_redact_verbose_keeps_full_paths() -> None:
    logging_setup.set_verbose(True)
    text = "opened /var/data/secret/song.wav"
    assert logging_setup.redact(text) == text


def test_path_for_log_returns_basename_when_not_verbose() -> None:
    # Default (verbose off): only the file name, no folders, even when
    # the path contains spaces — the case the line-level regex misses.
    assert (
        logging_setup.path_for_log("/Volumes/Field Recordings/Take 1.wav")
        == "Take 1.wav"
    )
    assert logging_setup.path_for_log(Path("/Users/alice/Music/song.wav")) == "song.wav"


def test_path_for_log_returns_full_path_when_verbose() -> None:
    logging_setup.set_verbose(True)
    full = "/Volumes/Field Recordings/Take 1.wav"
    assert logging_setup.path_for_log(full) == full


def test_redacting_formatter_scrubs_path_passed_as_arg() -> None:
    formatter = logging_setup.RedactingFormatter(fmt="%(message)s")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="f",
        lineno=1,
        msg="decoding %s",
        args=("/srv/audio/take/clip.wav",),
        exc_info=None,
    )
    out = formatter.format(record)
    assert "/srv/audio/take" not in out
    assert "clip.wav" in out


# --------------------------------------------------------------------------- #
# Control-character neutralisation (log-injection hardening, finding M-1)
# --------------------------------------------------------------------------- #


def _make_record(msg: str, args=None) -> logging.LogRecord:
    return logging.LogRecord(
        name="nonvisualaudio.test",
        level=logging.INFO,
        pathname="f",
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_path_for_log_strips_newline_from_name() -> None:
    # A filename with an embedded newline must not yield a real line break.
    out = logging_setup.path_for_log(Path("/x/ev\nil.wav"))
    assert "\n" not in out
    assert "\\n" in out
    assert "evil" not in out  # the two halves are joined by the escape, not split


def test_path_for_log_neutralizes_tab_and_carriage_return() -> None:
    out = logging_setup.path_for_log("/x/a\tb\rc.wav")
    assert "\t" not in out
    assert "\r" not in out
    assert "\\t" in out
    assert "\\r" in out


def test_path_for_log_neutralizes_ansi_escape() -> None:
    out = logging_setup.path_for_log("/x/\x1b[31mred\x1b[0m.wav")
    assert "\x1b" not in out
    assert "\\x1b" in out


def test_path_for_log_verbose_also_neutralizes() -> None:
    logging_setup.set_verbose(True)
    out = logging_setup.path_for_log("/Music/ev\nil/Take 1.wav")
    assert "\n" not in out
    # Verbose keeps the full path otherwise — spaces and folders survive.
    assert "Take 1.wav" in out
    assert "/Music/" in out


def test_path_for_log_keeps_umlauts_and_spaces() -> None:
    assert (
        logging_setup.path_for_log("/Volumes/Field Recordings/Lärm Tänze.wav")
        == "Lärm Tänze.wav"
    )


def test_formatter_forged_line_stays_single_physical_line() -> None:
    # The exact attack: a filename argument that embeds a complete,
    # format-conformant ERROR line. After formatting there must be no
    # second physical line and no second timestamped record.
    formatter = logging_setup.RedactingFormatter(
        fmt="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    forged = "take\n2026-06-10 12:00:00 ERROR nonvisualaudio: pwned"
    record = _make_record("analyzed %s", (forged,))
    out = formatter.format(record)
    # One physical line only — the embedded newline is now an inert "\n".
    assert "\n" not in out
    assert "\\n" in out
    assert "pwned" in out  # content is preserved, just inert
    # The single rendered line is the genuine INFO record, never the
    # forged ERROR one: its level field is INFO and the diagnostics
    # classifier reads it as info, not error.
    assert out.lstrip().split(" ", 3)[2] == "INFO"
    assert diagnostics.severity_of_log_line(out) == "info"


def test_formatter_neutralizes_ansi_in_message() -> None:
    formatter = logging_setup.RedactingFormatter(fmt="%(message)s")
    record = _make_record("name=%s", ("\x1b[2J\x1b[31mboom",))
    out = formatter.format(record)
    assert "\x1b" not in out
    assert "boom" in out


def test_formatter_preserves_traceback_newlines() -> None:
    # Multi-line tracebacks must keep their real newlines so the
    # diagnostics viewer can read them; only the message is scrubbed.
    formatter = logging_setup.RedactingFormatter(fmt="%(message)s")
    try:
        raise ValueError("kaboom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="nonvisualaudio.test",
            level=logging.ERROR,
            pathname="f",
            lineno=1,
            msg="failed",
            args=None,
            exc_info=sys.exc_info(),
        )
    out = formatter.format(record)
    assert "Traceback (most recent call last)" in out
    assert "\n" in out  # the traceback's own newlines survive


def test_neutralize_helper_leaves_clean_text_unchanged() -> None:
    clean = "decoded Lärm Tänze.wav in 1.2 s"
    assert logging_setup._neutralize_control_chars(clean) is clean


def test_log_viewer_severity_not_fooled_by_filename_fake_line() -> None:
    # End-to-end: a forged "ERROR" line embedded in a filename, once it
    # passes through the formatter, no longer starts a record the viewer's
    # severity classifier would pick up — it is one inert INFO line.
    formatter = logging_setup.RedactingFormatter(
        fmt="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    forged = "clip\n2026-06-10 12:00:00 ERROR nonvisualaudio: faked"
    record = _make_record("target analyzed: %s", (forged,))
    rendered = formatter.format(record)
    lines = rendered.split("\n")
    assert len(lines) == 1
    # No physical line is classified as an error by the viewer's matcher.
    assert all(
        diagnostics.severity_of_log_line(line) != "error" for line in lines
    )


# --------------------------------------------------------------------------- #
# init_file_logging — fresh log per session, no handler stacking
# --------------------------------------------------------------------------- #


@pytest.fixture()
def _restore_root_logger():
    """Snapshot the root logger and undo whatever init_file_logging added."""
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    yield
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


@pytest.fixture()
def _log_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _restore_root_logger
) -> Path:
    """Point file logging at a temp dir with the verbose preference off."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(logging_setup, "user_log_dir", lambda: log_dir)
    monkeypatch.setattr(logging_setup, "_load_verbose_pref", lambda: False)
    return log_dir


def test_init_file_logging_discards_previous_sessions(_log_dir: Path) -> None:
    log_file = _log_dir / logging_setup.LOG_FILENAME
    log_file.write_text(
        "2026-05-02 10:00:00 INFO nonvisualaudio: NonvisualAudio 2.0.4 started\n",
        encoding="utf-8",
    )

    assert logging_setup.init_file_logging() == log_file
    logging.getLogger("nonvisualaudio").info("fresh session line")

    content = log_file.read_text(encoding="utf-8")
    assert "2.0.4" not in content
    assert "fresh session line" in content


def test_init_file_logging_removes_stale_rotation_backups(_log_dir: Path) -> None:
    backup = _log_dir / (logging_setup.LOG_FILENAME + ".1")
    backup.write_text("old rotated session\n", encoding="utf-8")

    logging_setup.init_file_logging()
    assert not backup.exists()


def test_init_file_logging_twice_does_not_stack_handlers(_log_dir: Path) -> None:
    logging_setup.init_file_logging()
    logging_setup.init_file_logging()

    file_handlers = [
        h
        for h in logging.getLogger().handlers
        if getattr(h, "_nva_file_log", False)
    ]
    assert len(file_handlers) == 1

    logging.getLogger("nonvisualaudio").info("logged exactly once")
    content = (_log_dir / logging_setup.LOG_FILENAME).read_text(encoding="utf-8")
    assert content.count("logged exactly once") == 1


def test_init_file_logging_keeps_redaction(_log_dir: Path) -> None:
    logging_setup.init_file_logging()
    logging.getLogger("nonvisualaudio").info(
        "decoding %s", "/srv/audio/take/clip.wav"
    )

    content = (_log_dir / logging_setup.LOG_FILENAME).read_text(encoding="utf-8")
    assert "/srv/audio/take" not in content
    assert "clip.wav" in content


def test_init_file_logging_survives_unwritable_log_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _restore_root_logger
) -> None:
    # A file where the log directory should be makes mkdir fail with
    # NotADirectoryError; the app must fall back to stderr, not crash.
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(logging_setup, "user_log_dir", lambda: blocker / "logs")
    monkeypatch.setattr(logging_setup, "_load_verbose_pref", lambda: False)

    assert logging_setup.init_file_logging() is None
    assert not any(
        getattr(h, "_nva_file_log", False) for h in logging.getLogger().handlers
    )


# --------------------------------------------------------------------------- #
# Diagnostic report
# --------------------------------------------------------------------------- #


def test_build_report_includes_system_info_and_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / logging_setup.LOG_FILENAME).write_text(
        "hello from the log\n", encoding="utf-8"
    )
    monkeypatch.setattr(diagnostics, "user_log_dir", lambda: log_dir)

    report = diagnostics.build_report()
    assert "NonvisualAudio diagnostic report" in report
    assert "NonvisualAudio version" in report
    assert "hello from the log" in report


def test_build_report_ignores_old_rotation_backups(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / logging_setup.LOG_FILENAME).write_text(
        "current session line\n", encoding="utf-8"
    )
    (log_dir / (logging_setup.LOG_FILENAME + ".1")).write_text(
        "old session from 2.0.4\n", encoding="utf-8"
    )
    monkeypatch.setattr(diagnostics, "user_log_dir", lambda: log_dir)

    report = diagnostics.build_report()
    assert "current session line" in report
    assert "old session from 2.0.4" not in report
    assert logging_setup.LOG_FILENAME + ".1" not in report


def test_build_report_without_any_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(diagnostics, "user_log_dir", lambda: tmp_path / "missing")
    assert "(no log files found)" in diagnostics.build_report()


def test_default_report_path_is_a_log_file() -> None:
    path = diagnostics.default_report_path()
    assert path.suffix == ".log"
    assert path.name.startswith("NonvisualAudio-Diagnose-")
