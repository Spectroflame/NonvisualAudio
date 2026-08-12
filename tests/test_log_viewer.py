"""Pure-logic tests for the log viewer's reading and classification.

The wx dialog itself is covered by the manual layout script
(``scripts/verify_log_viewer_layout.py``); everything here runs without
a display: tail reading, truncation, the never-read-backups rule, and
the severity classifier behind the purely visual highlighting.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nonvisualaudio import diagnostics


@pytest.fixture
def log_dir(tmp_path, monkeypatch) -> Path:
    """Point diagnostics at a private, empty log folder."""
    monkeypatch.setattr(diagnostics, "user_log_dir", lambda: tmp_path)
    return tmp_path


def _write_log(log_dir: Path, text: str) -> Path:
    path = log_dir / "nonvisualaudio.log"
    # These tests exercise tail selection, not platform text translation.
    # Fixed bytes keep their fixtures identical on Unix and Windows.
    path.write_bytes(text.encode("utf-8"))
    return path


# --------------------------------------------------------------------------- #
# read_log_tail
# --------------------------------------------------------------------------- #


def test_reads_current_session_log(log_dir):
    _write_log(log_dir, "2026-06-10 12:00:00 INFO  nonvisualaudio: started\n")
    tail = diagnostics.read_log_tail()
    assert tail.status == "ok"
    assert tail.truncated is False
    assert "started" in tail.text


def test_never_reads_rotation_backups(log_dir):
    _write_log(log_dir, "current session\n")
    (log_dir / "nonvisualaudio.log.1").write_text(
        "SENTINEL-OLD-SESSION\n", encoding="utf-8"
    )
    (log_dir / "nonvisualaudio.log.2").write_text(
        "SENTINEL-OLDER-SESSION\n", encoding="utf-8"
    )
    tail = diagnostics.read_log_tail()
    assert "SENTINEL" not in tail.text
    assert tail.text == "current session\n"


def test_missing_log_reports_friendly_status(log_dir):
    tail = diagnostics.read_log_tail()
    assert tail.status == "missing"
    assert tail.text == ""
    assert tail.truncated is False


def test_unreadable_log_reports_friendly_status(log_dir, monkeypatch):
    path = _write_log(log_dir, "something\n")
    monkeypatch.setattr(
        Path, "open", lambda self, *a, **kw: (_ for _ in ()).throw(OSError("boom"))
    )
    tail = diagnostics.read_log_tail(path)
    assert tail.status == "unreadable"
    assert tail.text == ""


def test_long_log_truncated_to_tail_with_complete_first_line(log_dir):
    lines = [f"line number {i:05d}" for i in range(200)]
    _write_log(log_dir, "\n".join(lines) + "\n")
    tail = diagnostics.read_log_tail(limit=1024)
    assert tail.status == "ok"
    assert tail.truncated is True
    shown = tail.text.splitlines()
    # The cut-off partial line was dropped: the first shown line is a
    # complete, known line, and the newest line survived in full.
    assert shown[0] in lines
    assert shown[-1] == "line number 00199"
    assert len(shown) < len(lines)


def test_short_log_is_not_truncated(log_dir):
    _write_log(log_dir, "just a few bytes\n")
    tail = diagnostics.read_log_tail(limit=1024)
    assert tail.truncated is False
    assert tail.text == "just a few bytes\n"


def test_crlf_log_is_preserved(log_dir):
    _write_log(log_dir, "first entry\r\nsecond entry\r\n")
    tail = diagnostics.read_log_tail(limit=1024)
    assert tail.truncated is False
    assert tail.text == "first entry\r\nsecond entry\r\n"


def test_refresh_sees_appended_content(log_dir):
    path = _write_log(log_dir, "first entry\n")
    before = diagnostics.read_log_tail()
    with path.open("a", encoding="utf-8") as fh:
        fh.write("appended entry\n")
    after = diagnostics.read_log_tail()
    assert "appended entry" not in before.text
    assert "appended entry" in after.text


def test_single_giant_line_still_shows_something(log_dir):
    _write_log(log_dir, "x" * 5000)  # one line, no newline at all
    tail = diagnostics.read_log_tail(limit=1024)
    assert tail.truncated is True
    assert tail.text  # the no-newline fallback keeps the chunk


# --------------------------------------------------------------------------- #
# severity_of_log_line / is_log_record
# --------------------------------------------------------------------------- #


STAMP = "2026-06-10 12:34:56"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (f"{STAMP} INFO  nonvisualaudio: app started", "info"),
        (f"{STAMP} WARNING nonvisualaudio.audio: clipped", "warning"),
        (f"{STAMP} ERROR nonvisualaudio: failed", "error"),
        (f"{STAMP} CRITICAL nonvisualaudio: uncaught exception", "error"),
        (f"{STAMP} DEBUG nonvisualaudio: details", None),
        ("Traceback (most recent call last):", None),
        ('  File "app.py", line 1, in <module>', None),
        ("", None),
        ("völlig abwegige Zeile ohne Zeitstempel", None),
    ],
)
def test_severity_of_log_line(line, expected):
    assert diagnostics.severity_of_log_line(line) == expected


def test_is_log_record_separates_debug_from_continuation():
    assert diagnostics.is_log_record(f"{STAMP} DEBUG nonvisualaudio: x") is True
    assert diagnostics.is_log_record("Traceback (most recent call last):") is False
