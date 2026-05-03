"""Custom About/Help dialog.

Combines the platform "About" with two action buttons users keep
asking for: opening the bundled README and filing a bug report on
GitHub. Replaces the previous ``wx.adv.AboutBox`` flow because that
dialog cannot host extra buttons on every platform.

Accessibility: the dialog is a plain ``wx.Dialog`` with a read-only
multi-line ``wx.TextCtrl`` showing the app information. Every screen
reader walks that text line by line just like in the results dialog.
The action buttons sit in a normal button row, focusable in tab order.
"""

from __future__ import annotations

import logging
import sys
import webbrowser
from pathlib import Path

import wx

from nonvisualaudio import __version__
from nonvisualaudio.localization import t
from nonvisualaudio.ui import a11y, theme

log = logging.getLogger("nonvisualaudio.about")

_WEBSITE = "https://github.com/Spectroflame/NonvisualAudio"
_ISSUES_URL = "https://github.com/Spectroflame/NonvisualAudio/issues/new"

_CREDITS = (
    "ffmpeg — EBU R128 loudness measurement (ebur128 filter)",
    "wxPython — native accessibility bridge on every platform",
    "sounddevice and PortAudio — in-memory click playback",
    "scipy.signal, numpy, soundfile — analysis primitives",
)


# --------------------------------------------------------------------------- #
# README discovery
# --------------------------------------------------------------------------- #


def _readme_candidates() -> list[Path]:
    """Locations to probe for a user-facing README.

    Order matters: in a packaged build the README sits next to the
    executable; in a dev checkout it lives at the repo root.
    """
    out: list[Path] = []
    here = Path(__file__).resolve()
    repo_root = here.parents[3]
    for name in ("README.txt", "README.md"):
        out.append(repo_root / name)
    if getattr(sys, "frozen", False):
        try:
            exe_dir = Path(sys.executable).resolve().parent
            for name in ("README.txt", "README.md"):
                out.append(exe_dir / name)
                out.append(exe_dir.parent / name)
        except Exception:  # noqa: BLE001 — best-effort
            pass
    return out


def _locate_readme() -> Path | None:
    for path in _readme_candidates():
        if path.is_file():
            return path
    return None


# --------------------------------------------------------------------------- #
# Dialog
# --------------------------------------------------------------------------- #


class AboutDialog(wx.Dialog):
    """Compact About/Help dialog with README and bug-report shortcuts."""

    def __init__(self, parent: wx.Window | None) -> None:
        super().__init__(
            parent,
            title=t("ui.about.title"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetName(t("ui.about.title"))
        self.SetHelpText(t("ui.about.help"))

        root = wx.BoxSizer(wx.VERTICAL)

        # Heading. A regular static text is fine here because the next
        # widget down (the read-only TextCtrl) sits in the tab order and
        # is what the screen reader latches onto when the dialog opens.
        heading_label = wx.StaticText(
            self,
            label=f"{t('app.name')} {__version__}",
        )
        font = heading_label.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        font.SetPointSize(font.GetPointSize() + 2)
        heading_label.SetFont(font)
        root.Add(heading_label, flag=wx.ALL, border=12)

        info_text = (
            t("ui.about.description")
            + "\n\n"
            + t("ui.about.copyright")
            + "\n"
            + t("ui.about.website_label")
            + ": "
            + _WEBSITE
            + "\n\n"
            + t("ui.about.credits_heading")
            + "\n- "
            + "\n- ".join(_CREDITS)
        )
        self.info = wx.TextCtrl(
            self,
            value=info_text,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
        )
        a11y.set_a11y(
            self.info, t("ui.about.info_name"), t("ui.about.info_hint")
        )
        self.info.SetMinSize(wx.Size(-1, 220))
        root.Add(
            self.info,
            proportion=1,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT,
            border=12,
        )

        # Action button row.
        button_row = wx.BoxSizer(wx.HORIZONTAL)
        self.readme_btn = wx.Button(self, label=t("ui.about.btn.readme"))
        a11y.set_a11y(
            self.readme_btn,
            t("ui.about.readme_name"),
            t("ui.about.readme_hint"),
        )
        self.readme_btn.Bind(wx.EVT_BUTTON, self._on_show_readme)
        button_row.Add(self.readme_btn)

        self.bug_btn = wx.Button(self, label=t("ui.about.btn.report_bug"))
        a11y.set_a11y(
            self.bug_btn,
            t("ui.about.report_bug_name"),
            t("ui.about.report_bug_hint"),
        )
        self.bug_btn.Bind(wx.EVT_BUTTON, self._on_report_bug)
        button_row.Add(self.bug_btn, flag=wx.LEFT, border=8)

        button_row.AddStretchSpacer(1)

        self.close_btn = wx.Button(self, wx.ID_CLOSE, label=t("ui.btn.close"))
        a11y.set_a11y(
            self.close_btn,
            t("ui.about.close_name"),
            t("ui.about.close_hint"),
        )
        self.close_btn.Bind(
            wx.EVT_BUTTON, lambda _evt: self.EndModal(wx.ID_CLOSE)
        )
        self.close_btn.SetDefault()
        button_row.Add(self.close_btn)

        root.Add(
            button_row,
            flag=wx.EXPAND | wx.ALL,
            border=12,
        )

        self.SetSizer(root)
        self.SetInitialSize(wx.Size(580, 440))
        self.CentreOnParent()

        self.Bind(wx.EVT_CHAR_HOOK, self._on_char)

        theme.apply(self)
        # Land focus on the readme button so the screen reader announces
        # the most useful action first; the surrounding info is already
        # readable line by line via arrow keys once the user tabs into it.
        wx.CallAfter(self.readme_btn.SetFocus)

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #

    def _on_char(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        event.Skip()

    def _on_show_readme(self, _event: wx.CommandEvent) -> None:
        path = _locate_readme()
        if path is None:
            log.warning("README not found in any candidate location")
            wx.MessageBox(
                t("ui.about.readme_missing.body"),
                t("ui.about.readme_missing.title"),
                style=wx.OK | wx.ICON_WARNING,
                parent=self,
            )
            return
        log.info("opening README: %s", path)
        try:
            webbrowser.open(path.as_uri(), new=2)
        except Exception as exc:  # noqa: BLE001 — OS-level issue
            log.error("webbrowser.open(README) failed: %s", exc)
            wx.MessageBox(
                t("ui.about.readme_missing.body"),
                t("ui.about.readme_missing.title"),
                style=wx.OK | wx.ICON_WARNING,
                parent=self,
            )

    def _on_report_bug(self, _event: wx.CommandEvent) -> None:
        log.info("opening issues page: %s", _ISSUES_URL)
        try:
            webbrowser.open(_ISSUES_URL, new=2)
        except Exception as exc:  # noqa: BLE001
            log.error("webbrowser.open(issues) failed: %s", exc)


def show_about(parent: wx.Window | None) -> None:
    """Open the new About/Help dialog modally."""
    dlg = AboutDialog(parent)
    try:
        dlg.ShowModal()
    finally:
        dlg.Destroy()
    log.info("about dialog closed")
