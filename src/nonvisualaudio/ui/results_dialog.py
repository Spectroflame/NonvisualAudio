"""Modal dialog that shows the analysis report.

The main window stays focused on setup (file pickers, genre choice,
analyze button). Each analysis opens this dialog so the user can read
the report, copy it, then close the dialog and start another analysis.
"""

from __future__ import annotations

import logging
from pathlib import Path

import wx

from nonvisualaudio.localization import current_lang, t
from nonvisualaudio.reporting.export import to_html, to_markdown, to_plain_text
from nonvisualaudio.ui import a11y, theme
from nonvisualaudio.ui.error_dialog import show_error
from nonvisualaudio.errors import UserFacingError

log = logging.getLogger("nonvisualaudio.results_dialog")


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
        self.export_btn = wx.Button(self, label=t("ui.btn.export_report"))
        a11y.set_a11y(
            self.export_btn,
            t("ui.label.export_results"),
            t("ui.hint.export_results"),
        )
        self.export_btn.Bind(wx.EVT_BUTTON, self._on_export)
        button_row.Add(self.export_btn, flag=wx.LEFT, border=10)
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
        # Ctrl/Cmd+S as a screen-reader-friendly shortcut for Export...
        # The text control swallows plain Ctrl+S without a default
        # binding, so this hook gives users a keyboard path that does
        # not require Tab-walking to the Export button.
        if (
            not event.ShiftDown()
            and (event.CmdDown() or event.ControlDown())
            and chr(code).upper() == "S"
        ):
            self._on_export(None)
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

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #

    def _on_export(self, event) -> None:
        """Save the report as TXT, HTML, or Markdown.

        The format is picked from the chosen file extension rather than
        a separate format dropdown: the native Save dialog already has
        a filter row, and users pick once instead of once for the
        filter and once for the extension.
        """
        text = self.results.GetValue()
        if not text:
            return
        with wx.FileDialog(
            self,
            message=t("ui.results.export.dialog_title"),
            defaultFile=t("ui.results.export.default_filename"),
            wildcard=t("ui.results.export.wildcard"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            chosen = Path(dialog.GetPath())
            # The filter index decides the extension if the user did
            # not type one — wx returns the chosen filter as an int
            # (0 = txt, 1 = html, 2 = md) matching the wildcard order.
            if not chosen.suffix:
                chosen = chosen.with_suffix(
                    {0: ".txt", 1: ".html", 2: ".md"}.get(
                        dialog.GetFilterIndex(), ".txt"
                    )
                )
        rendered = self._render_for_extension(chosen.suffix.lower(), text)
        try:
            chosen.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            log.exception("export failed for %s", chosen)
            show_error(
                self,
                UserFacingError(
                    title=t("ui.results.export.failed.title"),
                    body=t(
                        "ui.results.export.failed.body",
                        filename=chosen.name,
                        details=exc.strerror or exc,
                    ),
                    hint=t("ui.results.export.failed.hint"),
                ),
            )
            return
        log.info("exported report to %s (%d bytes)", chosen, len(rendered))
        a11y.update_help(
            self.export_btn,
            t("ui.results.export.saved_hint", filename=chosen.name),
        )

    def _render_for_extension(self, suffix: str, text: str) -> str:
        """Map a file extension to the matching export rendering."""
        if suffix in (".html", ".htm"):
            return to_html(text, lang=current_lang())
        if suffix in (".md", ".markdown"):
            return to_markdown(text)
        return to_plain_text(text)
