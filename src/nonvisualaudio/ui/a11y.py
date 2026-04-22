"""Accessibility helper.

wxPython exposes accessibility through ``SetName`` (the short label a
screen reader announces first) and ``SetHelpText`` (the longer
description). ``set_a11y`` sets both from one call so UI code does not
have to repeat the same two-line boilerplate around every widget.

It also installs the description as a native tooltip via
``SetToolTip``. Sighted collaborators get a standard hover popup,
VoiceOver and NVDA still read the ``SetHelpText`` string when the
user asks for extra help on a focused control (VoiceOver: VO + Shift
+ H / VO + H; NVDA: NVDA + Tab to repeat, or Object Review).

Localised strings are passed in by the caller (typically via
``localization.t()``) — this module intentionally does not hold any
user-facing text itself so callers have a single source of truth for
translations.
"""

from __future__ import annotations

from typing import Any


def set_a11y(widget: Any, name: str, description: str = "") -> None:
    """Set screen-reader name, description, and mouse tooltip on a widget.

    ``widget`` is typed loosely so this module does not force every
    caller to import wx. All ``wx.Window`` subclasses implement
    ``SetName``, ``SetHelpText`` and ``SetToolTip`` so duck typing is
    safe here.
    """
    if hasattr(widget, "SetName"):
        widget.SetName(name)
    if description:
        update_help(widget, description)


def update_help(widget: Any, description: str) -> None:
    """Refresh the help-text and tooltip on a widget.

    Call this when the value of a read-only label changes (the selected
    files list, the selected-genres list, progress messages, …) so
    both the screen-reader help text and the hover tooltip reflect the
    latest state.
    """
    if description and hasattr(widget, "SetHelpText"):
        widget.SetHelpText(description)
    if description and hasattr(widget, "SetToolTip"):
        widget.SetToolTip(description)
