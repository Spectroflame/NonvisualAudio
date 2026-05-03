"""Modal dialog that lets the user choose which report sections to include.

Same accessibility pattern as the genre picker: one plain wx.CheckBox
per section, no list-with-checkboxes composite, so VoiceOver, NVDA, and
Orca all announce the toggles consistently.
"""

from __future__ import annotations

from collections.abc import Iterable

import wx

from nonvisualaudio.localization import t
from nonvisualaudio.reporting.builder import SECTION_ORDER, ReportSections
from nonvisualaudio.ui import a11y, theme


def section_label(key: str) -> str:
    return t(f"ui.sections.label.{key}")


def section_hint(key: str) -> str:
    return t(f"ui.sections.hint.{key}")


class SectionsDialog(wx.Dialog):
    """Pick which top-level report sections to render."""

    def __init__(
        self,
        parent: wx.Window | None,
        selected_keys: Iterable[str] | None = None,
    ) -> None:
        super().__init__(
            parent,
            title=t("ui.sections.title"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetName(t("ui.sections.title"))
        self.SetHelpText(t("ui.sections.help"))

        # Default to "everything on" when no preference exists yet — that
        # is what users get on a fresh install and matches the historical
        # report.
        if selected_keys is None:
            pre_selected: set[str] = set(SECTION_ORDER)
        else:
            pre_selected = {k for k in selected_keys if k in SECTION_ORDER}

        self._checks: dict[str, wx.CheckBox] = {}

        root = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(self, label=t("ui.sections.intro"))
        root.Add(intro, flag=wx.ALL, border=10)

        button_row = wx.BoxSizer(wx.HORIZONTAL)
        all_btn = wx.Button(self, label=t("ui.sections.btn.select_all"))
        all_btn.SetName(t("ui.sections.select_all_name"))
        all_btn.Bind(wx.EVT_BUTTON, self._on_select_all)
        none_btn = wx.Button(self, label=t("ui.sections.btn.select_none"))
        none_btn.SetName(t("ui.sections.select_none_name"))
        none_btn.Bind(wx.EVT_BUTTON, self._on_select_none)
        button_row.Add(all_btn)
        button_row.Add(none_btn, flag=wx.LEFT, border=8)
        root.Add(button_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        first_check: wx.CheckBox | None = None
        for key in SECTION_ORDER:
            check = wx.CheckBox(self, label=section_label(key))
            a11y.set_a11y(check, section_label(key), section_hint(key))
            if key in pre_selected:
                check.SetValue(True)
            self._checks[key] = check
            root.Add(check, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=12)
            if first_check is None:
                first_check = check

        ok_cancel = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        root.Add(ok_cancel, flag=wx.EXPAND | wx.ALL, border=10)

        self.SetSizer(root)
        self.SetInitialSize(wx.Size(480, 460))
        self.CentreOnParent()

        self.Bind(wx.EVT_CHAR_HOOK, self._on_char)

        theme.apply(self)
        if first_check is not None:
            first_check.SetFocus()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _on_select_all(self, event: wx.CommandEvent) -> None:
        for check in self._checks.values():
            check.SetValue(True)

    def _on_select_none(self, event: wx.CommandEvent) -> None:
        for check in self._checks.values():
            check.SetValue(False)

    def _on_char(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def selected_keys(self) -> list[str]:
        return [key for key in SECTION_ORDER if self._checks[key].GetValue()]

    def selected_sections(self) -> ReportSections:
        return ReportSections.from_keys(self.selected_keys())
