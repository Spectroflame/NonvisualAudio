"""UI theme palettes: auto / light / dark / high-contrast.

The theme layer intentionally stays narrow — only the widgets where
the platform's native appearance is not enough get re-coloured:
``wx.Panel``, read-only ``wx.TextCtrl``, ``wx.ListBox``,
``wx.ScrolledWindow``, ``wx.StaticText`` and ``wx.CheckBox``.
Buttons, menus, combo boxes, and message dialogs stay platform-native
so screen readers and the OS accessibility bridge keep behaving
exactly the way the user expects.
"""

from __future__ import annotations

import logging

import wx

log = logging.getLogger("nonvisualaudio.theme")

VALID_THEMES: tuple[str, ...] = ("auto", "light", "dark", "high_contrast")
DEFAULT_THEME: str = "auto"


# RGB triples: (background for read-only fields, foreground text,
# panel background).
_PALETTES: dict[str, dict[str, tuple[int, int, int]]] = {
    "light": {
        "bg": (255, 255, 255),
        "fg": (0, 0, 0),
        "panel_bg": (245, 245, 245),
    },
    "dark": {
        "bg": (35, 35, 38),
        "fg": (232, 232, 232),
        "panel_bg": (24, 24, 26),
    },
    "high_contrast": {
        # Classic assistive-technology palette: pure black background
        # with saturated yellow text. Matches what many Windows
        # high-contrast schemes default to and stays readable with
        # low-vision users who keep a magnifier on top.
        "bg": (0, 0, 0),
        "fg": (255, 255, 0),
        "panel_bg": (0, 0, 0),
    },
}


_current: str = DEFAULT_THEME


# --------------------------------------------------------------------------- #
# Resolution helpers
# --------------------------------------------------------------------------- #


def resolve_auto() -> str:
    """Return ``"dark"`` or ``"light"`` based on the system appearance.

    Uses ``wx.SystemSettings.GetAppearance().IsDark()`` where
    available (wxPython 4.1+). Falls back to light for any platform or
    build that does not expose the hint.
    """
    try:
        appearance = wx.SystemSettings.GetAppearance()
        if appearance.IsDark():
            return "dark"
    except Exception:  # noqa: BLE001 — SystemSettings is best-effort
        pass
    return "light"


def resolve(theme_key: str) -> str:
    """Return the concrete palette key for ``theme_key``.

    Unknown values fall back to ``light`` rather than raising, so a
    stale preferences file or typo never crashes the UI.
    """
    if theme_key == "auto":
        return resolve_auto()
    if theme_key in _PALETTES:
        return theme_key
    log.warning("unknown theme key %r; falling back to light", theme_key)
    return "light"


# --------------------------------------------------------------------------- #
# Current-theme accessors
# --------------------------------------------------------------------------- #


def set_current(theme_key: str) -> None:
    """Remember the user-facing theme (``auto``, ``light``, …).

    Does not apply the theme — call :func:`apply` on a window for
    that. Stored separately so the "View → Theme" menu can show the
    user's selection, even when the concrete effect is "dark" under
    ``auto``.
    """
    global _current
    _current = theme_key if theme_key in VALID_THEMES else DEFAULT_THEME


def current() -> str:
    return _current


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #


def apply(window: wx.Window, theme_key: str | None = None) -> None:
    """Apply ``theme_key`` (or the current one) to ``window`` recursively.

    Safe to call multiple times and on partially-built windows — any
    widget type we do not know how to paint is skipped.
    """
    key = theme_key if theme_key is not None else _current
    palette = _PALETTES[resolve(key)]
    fg = wx.Colour(*palette["fg"])
    bg = wx.Colour(*palette["bg"])
    panel_bg = wx.Colour(*palette["panel_bg"])
    _apply_to(window, fg, bg, panel_bg)
    window.Refresh()


def _apply_to(
    w: wx.Window,
    fg: wx.Colour,
    bg: wx.Colour,
    panel_bg: wx.Colour,
) -> None:
    if isinstance(w, (wx.Panel, wx.ScrolledWindow, wx.Dialog, wx.Frame)):
        w.SetBackgroundColour(panel_bg)
        w.SetForegroundColour(fg)
    elif isinstance(w, (wx.TextCtrl, wx.ListBox)):
        w.SetBackgroundColour(bg)
        w.SetForegroundColour(fg)
    elif isinstance(w, (wx.StaticText, wx.CheckBox)):
        w.SetForegroundColour(fg)
    for child in w.GetChildren():
        _apply_to(child, fg, bg, panel_bg)
