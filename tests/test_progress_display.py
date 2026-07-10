"""Routing tests for the analysis progress display.

The progress UI presents "progress + remaining time" as one element: the
gauge keeps the percent as its value and folds the remaining-time
estimate into its accessible *name*, while the neighbouring read-only
label carries only the current stage. These tests pin that routing down
so a future key rename or format change can't silently send the percent,
the ETA, or the stage to the wrong widget.

Like the project-prompt tests, this drives a real ``MainWindow`` behind a
``wx.App`` fixture and calls the progress handler directly.
"""

from __future__ import annotations

import time

import pytest

from nonvisualaudio import localization
from nonvisualaudio.localization import t

wx = pytest.importorskip("wx")

from nonvisualaudio.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def wx_app() -> wx.App:
    app = wx.App(False)
    yield app


@pytest.fixture
def window(wx_app: wx.App) -> MainWindow:
    # Pin the language so the asserted strings are deterministic.
    localization.load("de")
    win = MainWindow()
    yield win
    win.Destroy()


def _start(win: MainWindow) -> int:
    """Begin a fresh run token the handler will accept."""
    win._run_token += 1
    return win._run_token


def test_early_tick_keeps_gauge_name_plain(window: MainWindow):
    """Before the ETA is computable the gauge name stays the bare label."""
    tok = _start(window)
    window._eta.started_at = None  # too early for an estimate
    window._on_analysis_progress(tok, 3, "Dekodiere Audio")

    assert window.progress.GetValue() == 3
    # Stage goes to the label verbatim — no percent, no ETA mixed in.
    assert window.progress_label.GetValue() == "Dekodiere Audio"
    # No remaining-time estimate folded into the gauge name yet.
    assert window.progress.GetName() == t("ui.label.progress_bar")


def test_eta_rides_in_gauge_name_stage_in_label(window: MainWindow):
    """With an ETA available: percent = gauge value, ETA = gauge name,
    stage = label. The percent must appear once (as the gauge value),
    not duplicated into the name or the label."""
    tok = _start(window)
    # 10 s elapsed at 50 % yields a usable, non-zero estimate.
    window._eta.started_at = time.monotonic() - 10.0
    window._eta.eta_seconds = None
    window._on_analysis_progress(tok, 50, "Messe Lautheit")

    assert window.progress.GetValue() == 50
    assert window.progress_label.GetValue() == "Messe Lautheit"

    base = t("ui.label.progress_bar")
    name = window.progress.GetName()
    # The name extends the bare label with the ETA, and carries no percent.
    assert name.startswith(base)
    assert len(name) > len(base)
    assert "50" not in name
    # The label likewise never carries the percent.
    assert "50" not in window.progress_label.GetValue()


def test_stale_token_is_ignored(window: MainWindow):
    """Progress from a superseded/cancelled run must not touch the UI."""
    tok = _start(window)
    window._eta.started_at = time.monotonic() - 10.0
    window._eta.eta_seconds = None
    window._on_analysis_progress(tok, 50, "Messe Lautheit")

    window._on_analysis_progress(tok + 999, 80, "Analysiere Spektrum")

    assert window.progress.GetValue() == 50
    assert window.progress_label.GetValue() == "Messe Lautheit"


def test_stop_resets_gauge_name_and_label(window: MainWindow):
    """Stopping clears the ETA from the gauge name and empties the label
    so the next run never flashes a stale estimate before its first tick."""
    tok = _start(window)
    window._eta.started_at = time.monotonic() - 10.0
    window._eta.eta_seconds = None
    window._on_analysis_progress(tok, 50, "Messe Lautheit")

    window._stop_running_ui()

    assert window.progress.GetName() == t("ui.label.progress_bar")
    assert window.progress_label.GetValue() == ""
