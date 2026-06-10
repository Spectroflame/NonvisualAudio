"""Diagnostics dialog: export a support report and toggle verbose logging.

Mirrors the About dialog's accessibility pattern — a plain ``wx.Dialog`` with
a read-only multi-line ``wx.TextCtrl`` that screen readers walk line by line,
and a focusable button row. This dialog plus its single Help-menu entry are
the only UI additions; the existing analysis flow is untouched.
"""

from __future__ import annotations

import logging

import wx

from nonvisualaudio import diagnostics, logging_setup, preferences
from nonvisualaudio.localization import t
from nonvisualaudio.ui import a11y, theme

log = logging.getLogger("nonvisualaudio.diagnostics")


class DiagnosticsDialog(wx.Dialog):
    """Compact dialog to save a diagnostic report and control logging detail."""

    def __init__(self, parent: wx.Window | None) -> None:
        super().__init__(
            parent,
            title=t("ui.diagnostics.title"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetName(t("ui.diagnostics.title"))
        self.SetHelpText(t("ui.diagnostics.help"))

        root = wx.BoxSizer(wx.VERTICAL)

        info_text = (
            t("ui.diagnostics.description")
            + "\n\n"
            + t("ui.diagnostics.privacy")
        )
        self.info = wx.TextCtrl(
            self,
            value=info_text,
            style=wx.TE_MULTILINE | wx.TE_READONLY,
        )
        a11y.set_a11y(
            self.info, t("ui.diagnostics.info_name"), t("ui.diagnostics.info_hint")
        )
        self.info.SetMinSize(wx.Size(-1, 180))
        root.Add(
            self.info,
            proportion=1,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP,
            border=12,
        )

        # Verbose-logging toggle. Off by default — the privacy-preserving
        # setting — and persisted so a support session keeps it across runs.
        self.verbose_cb = wx.CheckBox(self, label=t("ui.diagnostics.verbose"))
        self.verbose_cb.SetValue(preferences.load_verbose_logging())
        a11y.set_a11y(
            self.verbose_cb,
            t("ui.diagnostics.verbose_name"),
            t("ui.diagnostics.verbose_hint"),
        )
        self.verbose_cb.Bind(wx.EVT_CHECKBOX, self._on_toggle_verbose)
        root.Add(self.verbose_cb, flag=wx.ALL, border=12)

        # Action button row.
        button_row = wx.BoxSizer(wx.HORIZONTAL)
        self.save_btn = wx.Button(self, label=t("ui.diagnostics.btn.save"))
        a11y.set_a11y(
            self.save_btn,
            t("ui.diagnostics.save_name"),
            t("ui.diagnostics.save_hint"),
        )
        self.save_btn.Bind(wx.EVT_BUTTON, self._on_save)
        button_row.Add(self.save_btn)

        self.folder_btn = wx.Button(self, label=t("ui.diagnostics.btn.folder"))
        a11y.set_a11y(
            self.folder_btn,
            t("ui.diagnostics.folder_name"),
            t("ui.diagnostics.folder_hint"),
        )
        self.folder_btn.Bind(wx.EVT_BUTTON, self._on_open_folder)
        button_row.Add(self.folder_btn, flag=wx.LEFT, border=8)

        button_row.AddStretchSpacer(1)

        self.close_btn = wx.Button(self, wx.ID_CLOSE, label=t("ui.btn.close"))
        a11y.set_a11y(
            self.close_btn,
            t("ui.diagnostics.close_name"),
            t("ui.diagnostics.close_hint"),
        )
        self.close_btn.Bind(wx.EVT_BUTTON, lambda _evt: self.EndModal(wx.ID_CLOSE))
        self.close_btn.SetDefault()
        button_row.Add(self.close_btn)

        root.Add(button_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=12)

        self.SetSizer(root)
        self.SetInitialSize(wx.Size(580, 440))
        self.CentreOnParent()

        self.Bind(wx.EVT_CHAR_HOOK, self._on_char)

        theme.apply(self)
        wx.CallAfter(self.save_btn.SetFocus)

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #

    def _on_char(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        event.Skip()

    def _on_toggle_verbose(self, _event: wx.CommandEvent) -> None:
        enabled = self.verbose_cb.GetValue()
        preferences.save_verbose_logging(enabled)
        logging_setup.set_verbose(enabled)
        log.info(
            "verbose logging %s by user", "enabled" if enabled else "disabled"
        )

    def _on_save(self, _event: wx.CommandEvent) -> None:
        save_report(self)

    def _on_open_folder(self, _event: wx.CommandEvent) -> None:
        if diagnostics.open_log_folder():
            return
        wx.MessageBox(
            t("ui.diagnostics.folder_failed.body"),
            t("ui.diagnostics.folder_failed.title"),
            style=wx.OK | wx.ICON_WARNING,
            parent=self,
        )


def save_report(parent: wx.Window | None) -> bool:
    """Write the diagnostic report into the log folder and confirm modally.

    Shared by the diagnostics dialog and the About dialog so both flows
    behave identically: no save-as picker, fixed location next to
    ``nonvisualaudio.log``, then a wx.MessageBox naming the file so a
    screen-reader user gets clear feedback.
    """
    target = diagnostics.default_report_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        report = diagnostics.build_report()
        target.write_text(report, encoding="utf-8")
    except OSError as exc:
        log.error("could not write diagnostic report: %s", exc)
        wx.MessageBox(
            t("ui.diagnostics.save_failed.body"),
            t("ui.diagnostics.save_failed.title"),
            style=wx.OK | wx.ICON_ERROR,
            parent=parent,
        )
        return False
    log.info("diagnostic report saved")
    # Offer to open the log folder right away: the report's whole point is
    # to be attached to a support mail, and hunting for the folder
    # afterwards is the hard part for a screen-reader user.
    choice = wx.MessageBox(
        t("ui.diagnostics.save_ok.body", filename=target.name),
        t("ui.diagnostics.save_ok.title"),
        style=wx.YES_NO | wx.ICON_INFORMATION,
        parent=parent,
    )
    if choice == wx.YES and not diagnostics.open_log_folder():
        wx.MessageBox(
            t("ui.diagnostics.folder_failed.body"),
            t("ui.diagnostics.folder_failed.title"),
            style=wx.OK | wx.ICON_WARNING,
            parent=parent,
        )
    return True


def show_diagnostics(parent: wx.Window | None) -> None:
    """Open the diagnostics dialog modally."""
    dlg = DiagnosticsDialog(parent)
    try:
        dlg.ShowModal()
    finally:
        dlg.Destroy()
    log.info("diagnostics dialog closed")
