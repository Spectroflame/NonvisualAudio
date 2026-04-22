"""Modal dialog that shows a user-facing error.

A plain ``wx.MessageBox`` would work, but it truncates long text on macOS
VoiceOver and does not separate title, body, and hint semantically. This
dialog presents them as three paragraphs in a read-only text area, which
screen readers walk through line by line.
"""

from __future__ import annotations

import wx

from nonvisualaudio.errors import UserFacingError
from nonvisualaudio.localization import t
from nonvisualaudio.ui import a11y, theme


class ErrorDialog(wx.Dialog):
    """Friendly presentation of a :class:`UserFacingError`."""

    def __init__(self, parent: wx.Window | None, error: UserFacingError) -> None:
        super().__init__(
            parent,
            title=error.title,
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetName(error.title)

        root = wx.BoxSizer(wx.VERTICAL)

        # Title as a bold label so the main window shows it prominently
        # even without the window-title chrome.
        title = wx.StaticText(self, label=error.title)
        title_font = title.GetFont()
        title_font.SetWeight(wx.FONTWEIGHT_BOLD)
        title_font.SetPointSize(title_font.GetPointSize() + 2)
        title.SetFont(title_font)
        title.SetName(t("ui.error_dialog.title_name"))
        root.Add(title, flag=wx.ALL, border=12)

        paragraphs = [error.body]
        if error.hint:
            paragraphs.append(t("ui.error_dialog.hint_prefix") + error.hint)

        body = wx.TextCtrl(
            self,
            value="\n\n".join(paragraphs),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.BORDER_NONE,
        )
        a11y.set_a11y(
            body,
            t("ui.error_dialog.body_name"),
            t("ui.error_dialog.body_hint"),
        )
        root.Add(body, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=12)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer(1)
        close_btn = wx.Button(self, wx.ID_OK, label=t("ui.btn.close"))
        close_btn.SetName(t("ui.error_dialog.close_name"))
        close_btn.SetDefault()
        buttons.Add(close_btn)
        root.Add(buttons, flag=wx.ALL | wx.EXPAND, border=12)

        self.SetSizer(root)
        self.SetInitialSize(wx.Size(520, 360))
        self.CentreOnParent()

        self.Bind(wx.EVT_CHAR_HOOK, self._on_char)
        theme.apply(self)
        body.SetFocus()

    def _on_char(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_OK)
            return
        event.Skip()


def show_error(parent: wx.Window | None, error: UserFacingError) -> None:
    """Display an error dialog and wait for the user to dismiss it."""
    dlg = ErrorDialog(parent, error)
    try:
        dlg.ShowModal()
    finally:
        dlg.Destroy()
