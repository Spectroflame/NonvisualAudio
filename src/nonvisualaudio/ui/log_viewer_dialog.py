"""Read-only viewer for the current session's log file.

Mirrors the results dialog's accessibility pattern: a read-only
multi-line ``wx.TextCtrl`` that screen readers walk line by line, a
copy button backed by the clipboard, and Escape to close. The log is
re-read only when the user presses Refresh — never on a timer, so a
screen reader is never interrupted mid-line.

The severity highlighting (info green, warnings amber, errors red) is
purely decorative for sighted collaborators: it colours the rendered
text via ``SetStyle`` and never changes the text itself, so copying
and screen-reader output stay byte-identical. If styling is
unavailable on a platform, the viewer simply works without it.
"""

from __future__ import annotations

import logging

import wx

from nonvisualaudio import diagnostics
from nonvisualaudio.localization import t
from nonvisualaudio.ui import a11y, theme

log = logging.getLogger("nonvisualaudio.log_viewer")

# Severity → text colour per resolved theme. Readability beats signal
# strength: saturated-but-dark tones on light, softened tones on dark.
# High contrast is deliberately absent — that palette belongs to the
# user's assistive setup and is never overridden.
_HIGHLIGHT_COLOURS: dict[str, dict[str, tuple[int, int, int]]] = {
    "light": {
        "info": (0, 110, 0),
        "warning": (150, 110, 0),
        "error": (180, 0, 0),
    },
    "dark": {
        "info": (120, 200, 120),
        "warning": (220, 200, 90),
        "error": (235, 100, 100),
    },
}


class LogViewerDialog(wx.Dialog):
    """Shows the current session log read-only, with manual refresh."""

    def __init__(self, parent: wx.Window | None) -> None:
        super().__init__(
            parent,
            title=t("ui.logviewer.title"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetName(t("ui.logviewer.title"))
        self.SetHelpText(t("ui.logviewer.help"))

        root = wx.BoxSizer(wx.VERTICAL)

        # TE_RICH2 lets SetStyle colour ranges on Windows; it is a
        # no-op on macOS/GTK and does not change how NVDA reads the
        # control.
        self.text = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE
            | wx.TE_READONLY
            | wx.TE_DONTWRAP
            | wx.HSCROLL
            | wx.TE_RICH2,
        )
        a11y.set_a11y(
            self.text, t("ui.logviewer.text_name"), t("ui.logviewer.text_hint")
        )
        root.Add(self.text, proportion=1, flag=wx.EXPAND | wx.ALL, border=10)

        # Button row.
        button_row = wx.BoxSizer(wx.HORIZONTAL)
        self.copy_btn = wx.Button(self, label=t("ui.logviewer.btn.copy"))
        a11y.set_a11y(
            self.copy_btn,
            t("ui.logviewer.copy_name"),
            t("ui.logviewer.copy_hint"),
        )
        self.copy_btn.Bind(wx.EVT_BUTTON, self._on_copy)
        button_row.Add(self.copy_btn)

        self.refresh_btn = wx.Button(self, label=t("ui.logviewer.btn.refresh"))
        a11y.set_a11y(
            self.refresh_btn,
            t("ui.logviewer.refresh_name"),
            t("ui.logviewer.refresh_hint"),
        )
        self.refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        button_row.Add(self.refresh_btn, flag=wx.LEFT, border=10)

        button_row.AddStretchSpacer(1)

        self.close_btn = wx.Button(self, wx.ID_CLOSE, label=t("ui.btn.close"))
        a11y.set_a11y(
            self.close_btn,
            t("ui.logviewer.close_name"),
            t("ui.logviewer.close_hint"),
        )
        self.close_btn.Bind(wx.EVT_BUTTON, lambda _evt: self.EndModal(wx.ID_CLOSE))
        self.close_btn.SetDefault()
        button_row.Add(self.close_btn)

        root.Add(button_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        self.SetSizer(root)
        self.SetInitialSize(wx.Size(760, 620))
        self.CentreOnParent()

        # Shortcuts: Escape to close, Ctrl/Cmd+Shift+C to copy the log.
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char)

        theme.apply(self)
        self._load()

        # Start inside the log text, not on a button, so the user can
        # read immediately; the default Ctrl+C still works for partial
        # copies inside the control.
        wx.CallAfter(self.text.SetFocus)

    # ------------------------------------------------------------------ #
    # Loading and highlighting
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        """Read the session log and display it; never raises."""
        tail = diagnostics.read_log_tail()
        if tail.status == "missing":
            display = t("ui.logviewer.missing")
        elif tail.status == "unreadable":
            display = t("ui.logviewer.unreadable")
        elif tail.truncated:
            # The notice comes first: it is the first thing a screen
            # reader meets when reading from the top.
            display = t("ui.logviewer.truncated_notice") + "\n\n" + tail.text
        else:
            display = tail.text
        # ChangeValue, not SetValue: no EVT_TEXT event, so assistive
        # tech hears nothing it did not ask for.
        self.text.ChangeValue(display)
        self.text.SetInsertionPoint(0)
        self.text.ShowPosition(0)
        self._apply_highlighting(display)

    def _apply_highlighting(self, display: str) -> None:
        """Colour log lines by severity — purely visual, best effort.

        Only the rendered text colour changes; the text itself is never
        touched, so ``GetValue`` stays byte-identical. Any failure is
        swallowed: the viewer must work fine without highlighting.
        """
        colours = _HIGHLIGHT_COLOURS.get(theme.resolve(theme.current()))
        if colours is None:  # high contrast: never override the palette
            return
        try:
            runs = self._severity_runs(display.split("\n"))
            for start, end, severity in runs:
                attr = wx.TextAttr(wx.Colour(*colours[severity]))
                self.text.SetStyle(start, end, attr)
        except Exception:  # noqa: BLE001 — decoration must never break the viewer
            log.debug("log highlighting unavailable", exc_info=True)

    @staticmethod
    def _severity_runs(
        lines: list[str],
    ) -> list[tuple[int, int, str]]:
        """Merge consecutive same-severity lines into (start, end, sev) runs.

        Positions are character offsets into the joined text (one ``\\n``
        between lines — exactly what was passed to ``ChangeValue``).
        Continuation lines (tracebacks) inherit the previous line's
        severity so an exception block is coloured as one piece; with no
        preceding classified line they stay unstyled.
        """
        runs: list[tuple[int, int, str]] = []
        pos = 0
        carried: str | None = None
        for line in lines:
            severity = diagnostics.severity_of_log_line(line)
            if severity is not None:
                carried = severity
            elif diagnostics.is_log_record(line):
                carried = None  # a DEBUG record breaks the run
            # else: continuation line (traceback etc.) — keep `carried`
            end = pos + len(line)
            if carried is not None and line:
                if runs and runs[-1][2] == carried and runs[-1][1] + 1 == pos:
                    runs[-1] = (runs[-1][0], end, carried)
                else:
                    runs.append((pos, end, carried))
            pos = end + 1  # the joining "\n"
        return runs

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #

    def _on_char(self, event: wx.KeyEvent) -> None:
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        if (
            event.ShiftDown()
            and (event.CmdDown() or event.ControlDown())
            and chr(code).upper() == "C"
        ):
            self._on_copy(None)
            return
        event.Skip()

    def _on_copy(self, _event) -> None:
        """Copy exactly the displayed text — no colour information."""
        text = self.text.GetValue()
        if not text:
            return
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(text))
            finally:
                wx.TheClipboard.Close()
            a11y.update_help(self.copy_btn, t("ui.logviewer.copied_hint"))

    def _on_refresh(self, _event) -> None:
        # Focus stays where it is (usually on this button), so the
        # user can press Refresh repeatedly without being moved around.
        self._load()


def show_log_viewer(parent: wx.Window | None) -> None:
    """Open the log viewer modally."""
    dlg = LogViewerDialog(parent)
    try:
        dlg.ShowModal()
    finally:
        dlg.Destroy()
    log.info("log viewer closed")
