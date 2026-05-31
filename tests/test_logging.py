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


def test_build_report_without_any_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(diagnostics, "user_log_dir", lambda: tmp_path / "missing")
    assert "(no log files found)" in diagnostics.build_report()


def test_default_report_path_is_a_log_file() -> None:
    path = diagnostics.default_report_path()
    assert path.suffix == ".log"
    assert path.name.startswith("NonvisualAudio-Diagnose-")
