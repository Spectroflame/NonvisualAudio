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

Windows specifics:

NVDA's announcement order on focus is *name → role → state →
description*. The name and the description need to stay in their own
channels: putting the description into the name pushes the role and
state behind a wall of supplementary text, which is especially painful
on stateful controls (a CheckBox's checked/unchecked, a Choice's
selected item, a Gauge's value).

To make the description channel reliable, on Windows we register a
``wx.Accessible`` subclass that surfaces the raw description through
``GetDescription``. wxWidgets' default already mirrors ``SetHelpText``
into MSAA ``accDescription`` on most builds, but the routing is
implementation-defined; an explicit override makes the behaviour
deterministic. NVDA reads ``accDescription`` after the role and state
when the "Report object descriptions" option is enabled (which is the
NVDA factory default), so the hint reaches the user without delaying
the part they care about most.

The wxAccessible registration is gated on ``sys.platform``, so macOS
keeps using the NSAccessibility bridge in :mod:`macos_a11y` and Linux
keeps relying on Orca picking up wx's GTK accessible defaults.
"""

from __future__ import annotations

import sys
from typing import Any

import wx


# wx.Accessible exists on every wxPython platform but only has an
# effect on Windows — wxWidgets only routes WM_GETOBJECT through it
# in the wxMSW backend. We keep the platform check explicit so the
# behaviour is obvious; the ``hasattr`` guard catches the (unlikely)
# case of a wxPython build that strips the class altogether.
_WX_ACC_USABLE = sys.platform == "win32" and hasattr(wx, "Accessible")


if _WX_ACC_USABLE:

    class _A11yProvider(wx.Accessible):
        """Expose the widget's hint as MSAA ``accDescription``.

        We deliberately do not override ``GetName``: NVDA reads the
        name first, before role and state, so folding the hint into
        the name would block the state announcement on stateful
        controls. Letting ``GetName`` fall through to the wx default
        keeps the name short while ``GetDescription`` carries the
        hint, which NVDA reads after the state.

        Returning ``wx.ACC_NOT_IMPLEMENTED`` for empty descriptions
        lets wxWidgets fall through to its default behaviour, which
        is the right thing — we only want to override when we have
        something more useful to say than the platform default.
        """

        def __init__(self, window: wx.Window, description: str) -> None:
            super().__init__(window)
            self._description = description

        def update(self, description: str) -> None:
            """Refresh the description without re-allocating the provider."""
            self._description = description

        def GetDescription(self, child_id: int):  # noqa: N802 — wx API name
            if self._description:
                return wx.ACC_OK, self._description
            return wx.ACC_NOT_IMPLEMENTED, ""


def set_a11y(widget: Any, name: str, description: str = "") -> None:
    """Set screen-reader name, description, and mouse tooltip on a widget.

    ``widget`` is typed loosely so this module does not force every
    caller to import wx. All ``wx.Window`` subclasses implement
    ``SetName``, ``SetHelpText`` and ``SetToolTip`` so duck typing is
    safe here.

    On macOS, wxPython's ``SetName`` writes to NSAccessibility's
    ``accessibilityIdentifier`` rather than its ``accessibilityTitle``
    — VoiceOver reads the latter, so a separate call into
    :mod:`nonvisualaudio.ui.macos_a11y` patches the title via the
    Objective-C runtime. That call is a cheap no-op everywhere except
    macOS.

    On Windows, ``set_a11y`` additionally registers a wx.Accessible
    so the description is exposed via MSAA ``accDescription`` (see the
    module docstring for why we keep description and name separate).
    """
    if hasattr(widget, "SetName"):
        widget.SetName(name)
    # Local import keeps Linux / Windows builds from loading the
    # ctypes-backed bridge module unnecessarily; the module itself
    # already gates its initialisation on sys.platform.
    from nonvisualaudio.ui import macos_a11y

    macos_a11y.set_accessibility_title(widget, name)

    # Stash the description on the widget so update_help can refresh
    # the wx.Accessible whenever the description changes. Plain
    # attribute assignment is safe — wxPython widgets are ordinary
    # Python objects with a __dict__.
    widget._a11y_description = description
    _push_windows_accessible(widget)

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
    widget._a11y_description = description
    _push_windows_accessible(widget)


def _push_windows_accessible(widget: Any) -> None:
    """Update the widget's wx.Accessible from its stored description.

    No-op on non-Windows platforms. Reuses the same ``_A11yProvider``
    instance across calls so a high-frequency caller (the progress
    label updates several times a second during analysis) doesn't
    churn through allocations.
    """
    if not _WX_ACC_USABLE:
        return
    description = getattr(widget, "_a11y_description", "") or ""
    provider = getattr(widget, "_a11y_provider", None)
    if provider is None:
        if not description:
            # Nothing to override yet — let the wx default Accessible
            # handle this widget until a description shows up.
            return
        try:
            provider = _A11yProvider(widget, description)
            widget.SetAccessible(provider)
            widget._a11y_provider = provider
        except Exception:  # noqa: BLE001
            # SetAccessible can fail before a widget's native peer
            # exists or on widget subclasses that don't accept a
            # custom accessible. An accessibility hint must never
            # crash the UI, so swallow and let the platform default
            # take over.
            return
    else:
        provider.update(description)
