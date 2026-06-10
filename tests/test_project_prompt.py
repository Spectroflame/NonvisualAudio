"""Tests for the multi-file "use project mode?" prompt.

The trigger matrix is covered through the pure decision function in
``nonvisualaudio.ui.project_prompt``; the main-window wiring (dialog
replaced by a recorder), checkbox state, and reset-on-clear run against
a real ``MainWindow`` with a wx.App fixture, like the theme tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nonvisualaudio import localization
from nonvisualaudio.ui.project_prompt import should_offer_project_mode

wx = pytest.importorskip("wx")

from nonvisualaudio.ui.main_window import MainWindow  # noqa: E402


# --------------------------------------------------------------------------- #
# Decision function
# --------------------------------------------------------------------------- #


def test_prompt_fires_from_zero_past_threshold():
    assert should_offer_project_mode(0, 6, False, False)


def test_prompt_fires_on_exact_crossing():
    assert should_offer_project_mode(5, 6, False, False)


def test_no_prompt_at_or_below_threshold():
    assert not should_offer_project_mode(0, 5, False, False)
    assert not should_offer_project_mode(2, 4, False, False)


def test_no_prompt_when_list_was_already_large():
    # The crossing already happened earlier (e.g. while project mode was
    # on); growing a large list further must stay silent.
    assert not should_offer_project_mode(6, 7, False, False)


def test_no_prompt_after_dismissal():
    assert not should_offer_project_mode(5, 7, False, True)


def test_no_prompt_when_project_mode_on():
    assert not should_offer_project_mode(0, 10, True, False)


# --------------------------------------------------------------------------- #
# Main-window wiring
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def wx_app() -> wx.App:
    """Create the wxApp once — MainWindow needs it in scope."""
    app = wx.App(False)
    yield app


@pytest.fixture
def window(wx_app: wx.App) -> MainWindow:
    win = MainWindow()
    yield win
    win.Destroy()


class _PromptRecorder:
    """Stands in for ``_ask_use_project_mode``: records every call and
    returns a fixed answer instead of opening the modal dialog."""

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.calls: list[int] = []

    def __call__(self, count: int) -> bool:
        self.calls.append(count)
        return self.answer


def _wavs(tmp_path: Path, n: int, prefix: str = "track") -> list[str]:
    paths: list[str] = []
    for i in range(n):
        p = tmp_path / f"{prefix}_{i:02d}.wav"
        p.write_bytes(b"")
        paths.append(str(p))
    return paths


def test_add_six_targets_triggers_prompt(window: MainWindow, tmp_path: Path):
    window._ask_use_project_mode = asked = _PromptRecorder(answer=False)
    window._add_paths(_wavs(tmp_path, 6))
    assert asked.calls == [6]


def test_crossing_from_five_triggers_prompt(window: MainWindow, tmp_path: Path):
    window._ask_use_project_mode = asked = _PromptRecorder(answer=False)
    window._add_paths(_wavs(tmp_path, 5))
    assert asked.calls == []
    window._add_paths(_wavs(tmp_path, 1, prefix="extra"))
    assert asked.calls == [6]


def test_no_second_prompt_after_decline(window: MainWindow, tmp_path: Path):
    window._ask_use_project_mode = asked = _PromptRecorder(answer=False)
    window._add_paths(_wavs(tmp_path, 6))
    window._add_paths(_wavs(tmp_path, 1, prefix="extra"))
    assert asked.calls == [6]


def test_no_prompt_when_project_mode_already_on(window: MainWindow, tmp_path: Path):
    window.project_check.SetValue(True)
    window._on_toggle_project(None)
    window._ask_use_project_mode = asked = _PromptRecorder(answer=False)
    window._add_paths(_wavs(tmp_path, 6))
    assert asked.calls == []


def test_references_never_prompt(window: MainWindow, tmp_path: Path):
    window._ask_use_project_mode = asked = _PromptRecorder(answer=False)
    window._set_reference_paths(_wavs(tmp_path, 7))
    assert asked.calls == []
    assert window._project_mode is False


def test_choose_project_enables_mode_and_checkbox(
    window: MainWindow, tmp_path: Path
):
    window._ask_use_project_mode = _PromptRecorder(answer=True)
    window._add_paths(_wavs(tmp_path, 6))
    assert window._project_mode is True
    # The visible/screen-reader state must match the internal flag.
    assert window.project_check.GetValue() is True


def test_choose_separate_keeps_mode_off_and_quiet(
    window: MainWindow, tmp_path: Path
):
    window._ask_use_project_mode = asked = _PromptRecorder(answer=False)
    window._add_paths(_wavs(tmp_path, 6))
    assert window._project_mode is False
    assert window.project_check.GetValue() is False
    window._add_paths(_wavs(tmp_path, 2, prefix="more"))
    assert asked.calls == [6]


def test_clear_targets_rearms_prompt(window: MainWindow, tmp_path: Path):
    window._ask_use_project_mode = asked = _PromptRecorder(answer=False)
    window._add_paths(_wavs(tmp_path, 6))
    window._on_clear_targets(None)
    window._add_paths(_wavs(tmp_path, 7, prefix="fresh"))
    assert asked.calls == [6, 7]


def test_manual_toggle_on_then_off_stays_quiet(
    window: MainWindow, tmp_path: Path
):
    # Enabling project mode by hand counts as "question answered"; turning
    # it off again with a still-large list must not nag immediately.
    window.project_check.SetValue(True)
    window._on_toggle_project(None)
    window._ask_use_project_mode = asked = _PromptRecorder(answer=False)
    window._add_paths(_wavs(tmp_path, 8))
    window.project_check.SetValue(False)
    window._on_toggle_project(None)
    window._add_paths(_wavs(tmp_path, 1, prefix="late"))
    assert asked.calls == []


# --------------------------------------------------------------------------- #
# Localisation and native-dialog button labels
# --------------------------------------------------------------------------- #

_PROMPT_TEXT_KEYS = (
    "ui.project_prompt.title",
    "ui.project_prompt.btn.project",
    "ui.project_prompt.btn.separate",
)


@pytest.mark.parametrize("lang", ["en", "de"])
def test_prompt_strings_localized(lang: str):
    localization.load(lang)
    try:
        for key in _PROMPT_TEXT_KEYS:
            assert localization.t(key) != key, f"missing {lang} text for {key}"
        body = localization.t("ui.project_prompt.body", count=7)
        assert body != "ui.project_prompt.body"
        assert "7" in body, "body must embed the file count"
    finally:
        localization.load("en")


def test_custom_button_labels_supported(window: MainWindow):
    """The platform must honour SetYesNoLabels, otherwise the dialog
    would fall back to bare Yes/No — ambiguous for "as project or
    separately?". wx returns True only when the native dialog will
    really show (and screen readers will announce) the custom labels.
    """
    dlg = wx.MessageDialog(
        window,
        localization.t("ui.project_prompt.body", count=6),
        localization.t("ui.project_prompt.title"),
        style=wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION,
    )
    try:
        supported = dlg.SetYesNoLabels(
            localization.t("ui.project_prompt.btn.project"),
            localization.t("ui.project_prompt.btn.separate"),
        )
        assert supported is True
    finally:
        dlg.Destroy()
