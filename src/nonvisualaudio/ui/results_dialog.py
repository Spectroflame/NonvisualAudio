"""Modal dialog that shows the analysis report.

The main window stays focused on setup (file pickers, genre choice,
analyze button). Each analysis opens this dialog so the user can read
the report, copy it, then close the dialog and start another analysis.
"""

from __future__ import annotations

import wx

from nonvisualaudio.localization import t
from nonvisualaudio.ui import a11y, theme


class ResultsDialog(wx.Dialog):
    """Shows a finished analysis report in its own window."""

    def __init__(self, parent: wx.Window | None, report_text: str = "") -> None:
        super().__init__(
            parent,
            title=t("ui.results.title"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetName(t("ui.label.results"))
        self.SetHelpText(t("ui.results.help"))

        root = wx.BoxSizer(wx.VERTICAL)

        # Read-only multi-line text control: screen readers on all three
        # platforms read this line by line reliably, which is exactly what
        # we want for a plain-text report.
        self.results = wx.TextCtrl(
            self,
            value=report_text,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP | wx.HSCROLL,
        )
        a11y.set_a11y(self.results, t("ui.label.results"), t("ui.hint.results"))
        # Place the cursor at the very top so the screen reader starts on
        # line one when it picks up the text control.
        self.results.SetInsertionPoint(0)
        self.results.ShowPosition(0)
        root.Add(
            self.results,
            proportion=1,
            flag=wx.EXPAND | wx.ALL,
            border=10,
        )

        # Button row.
        button_row = wx.BoxSizer(wx.HORIZONTAL)
        self.copy_btn = wx.Button(self, label=t("ui.btn.copy_report"))
        a11y.set_a11y(
            self.copy_btn,
            t("ui.label.copy_results"),
            t("ui.hint.copy_results"),
        )
        self.copy_btn.Bind(wx.EVT_BUTTON, self._on_copy)
        button_row.Add(self.copy_btn)
        button_row.AddStretchSpacer(1)
        self.close_btn = wx.Button(self, wx.ID_CLOSE, label=t("ui.btn.close"))
        a11y.set_a11y(
            self.close_btn,
            t("ui.results.close_name"),
            t("ui.results.close_hint"),
        )
        self.close_btn.Bind(wx.EVT_BUTTON, lambda _evt: self.EndModal(wx.ID_CLOSE))
        button_row.Add(self.close_btn)
        root.Add(button_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        self.SetSizer(root)
        self.SetInitialSize(wx.Size(760, 620))
        self.CentreOnParent()

        # Shortcuts: Escape to close, Ctrl/Cmd+Shift+C to copy the report.
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char)

        theme.apply(self)

        # Make sure the screen reader starts inside the report text, not on
        # a button. ``CallAfter`` runs after the show event so the focus
        # change is not swallowed by the dialog's default focus logic.
        wx.CallAfter(self.results.SetFocus)

    # ------------------------------------------------------------------ #
    # Keyboard shortcuts
    # ------------------------------------------------------------------ #

    def _on_char(self, event: wx.KeyEvent) -> None:
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        # Ctrl/Cmd+Shift+C to copy the whole report at once. The default
        # Ctrl+C still works inside the text control for partial copies.
        if (
            event.ShiftDown()
            and (event.CmdDown() or event.ControlDown())
            and chr(code).upper() == "C"
        ):
            self._on_copy(None)
            return
        event.Skip()

    # ------------------------------------------------------------------ #
    # Copy
    # ------------------------------------------------------------------ #

    def _on_copy(self, event) -> None:
        text = self.results.GetValue()
        if not text:
            return
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(text))
            finally:
                wx.TheClipboard.Close()
            a11y.update_help(self.copy_btn, t("ui.results.copied_hint"))
