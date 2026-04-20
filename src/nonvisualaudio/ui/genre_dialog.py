"""Modal genre picker dialog.

Uses individual check boxes for each profile so the user can pick any
number of genres and have the file compared against all of them at
once. Check boxes are reliably accessible to VoiceOver, NVDA and Orca
when used as plain wx widgets (unlike list-with-checkboxes composites,
which have a history of announcing inconsistently).
"""

from __future__ import annotations

from collections.abc import Iterable

import wx

from nonvisualaudio.reporting.genre_profiles import GENRES, grouped_genres


class GenreDialog(wx.Dialog):
    """Modal dialog that lets the user pick one or more genre profiles."""

    def __init__(
        self,
        parent: wx.Window | None,
        selected_keys: Iterable[str] | None = None,
    ) -> None:
        super().__init__(
            parent,
            title="Choose genre references",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetName("Choose genre references")
        self.SetHelpText(
            "A list of genre profiles grouped by category. "
            "Tab between profiles and press Space to toggle each one. "
            "Any number of profiles can be selected at once."
        )

        self._checks: dict[str, wx.CheckBox] = {}
        pre_selected: set[str] = set(selected_keys or ())

        root = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(
            self,
            label=(
                "Pick one or more genres whose typical loudness and dynamics\n"
                "you want the file compared against. You can select as many as\n"
                "you like — the report will include a comparison for each."
            ),
        )
        root.Add(intro, flag=wx.ALL, border=10)

        clear_btn = wx.Button(self, label="Clear all")
        clear_btn.SetName("Clear all genre selections")
        clear_btn.Bind(wx.EVT_BUTTON, self._on_clear_all)
        root.Add(clear_btn, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        # Scrolled region containing the grouped check boxes.
        scroll = wx.ScrolledWindow(self, style=wx.VSCROLL)
        scroll.SetScrollRate(0, 16)
        inner = wx.BoxSizer(wx.VERTICAL)

        first_check: wx.CheckBox | None = None
        for category, profiles in grouped_genres():
            cat_label = wx.StaticText(scroll, label=category)
            font = cat_label.GetFont()
            font.SetWeight(wx.FONTWEIGHT_BOLD)
            cat_label.SetFont(font)
            inner.Add(cat_label, flag=wx.TOP | wx.LEFT | wx.RIGHT, border=6)
            for p in profiles:
                check = wx.CheckBox(scroll, label=p.display_name)
                check.SetName(p.display_name)
                check.SetHelpText(f"{category} genre reference. {p.notes}")
                if p.key in pre_selected:
                    check.SetValue(True)
                self._checks[p.key] = check
                inner.Add(check, flag=wx.LEFT | wx.RIGHT, border=12)
                if first_check is None:
                    first_check = check

        scroll.SetSizer(inner)
        scroll.FitInside()
        root.Add(scroll, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)

        # Standard OK/Cancel row.
        button_sizer = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        root.Add(button_sizer, flag=wx.EXPAND | wx.ALL, border=10)

        self.SetSizer(root)
        self.SetInitialSize(wx.Size(520, 560))
        self.CentreOnParent()

        self.Bind(wx.EVT_CHAR_HOOK, self._on_char)

        if first_check is not None:
            first_check.SetFocus()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _on_clear_all(self, event: wx.CommandEvent) -> None:
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
        """Return the checked profile keys in display order."""
        return [key for key, check in self._checks.items() if check.GetValue()]

    def selected_display_names(self) -> list[str]:
        names: list[str] = []
        for key in self.selected_keys():
            profile = GENRES.get(key)
            if profile is not None:
                names.append(profile.display_name)
        return names
