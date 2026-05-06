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

NVDA does not announce ``SetHelpText`` / MSAA ``accDescription`` by
default — that requires the user to enable "Report object descriptions"
in NVDA's settings — and ``SetToolTip`` only fires on hover, which
screen-reader users never trigger. The result is that the hint text
never reaches NVDA users out of the box.

To close that gap, on Windows we register a ``wx.Accessible``
subclass that:

* Folds ``name + description`` into the accessible NAME for the
  control kinds where NVDA naturally announces only a short label
  (buttons, checkboxes, choices, gauges). NVDA reads the name on
  every focus change, so the hint is always heard.
* Surfaces the raw description through ``GetDescription`` so the
  proper MSAA ``accDescription`` is also populated for users who do
  enable the description-reporting setting, and for other AT clients
  that ignore the embedded form.

For text-input widgets (``wx.TextCtrl``, ``wx.ListBox``) NVDA already
reads the contained value, so embedding the hint into the name would
just duplicate the announcement. They keep the description-only
branch.

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
        """Push a combined name + description into MSAA on Windows.

        The composite name is what NVDA actually announces on focus;
        the raw description is also exposed via ``GetDescription`` so
        clients that read MSAA ``accDescription`` (NVDA with the
        relevant setting on, JAWS, etc.) get an unmangled hint.

        Returning ``wx.ACC_NOT_IMPLEMENTED`` for empty fields lets
        wxWidgets fall through to its default behaviour, which is the
        right thing — we only want to override when we have something
        more useful to say than the platform default.
        """

        def __init__(
            self,
            window: wx.Window,
            composite_name: str,
            description: str,
        ) -> None:
            super().__init__(window)
            self._name = composite_name
            self._description = description

        def update(self, composite_name: str, description: str) -> None:
            """Refresh the strings without re-allocating the provider."""
            self._name = composite_name
            self._description = description

        def GetName(self, child_id: int):  # noqa: N802 — wx API name
            if self._name:
                return wx.ACC_OK, self._name
            return wx.ACC_NOT_IMPLEMENTED, ""

        def GetDescription(self, child_id: int):  # noqa: N802 — wx API name
            if self._description:
                return wx.ACC_OK, self._description
            return wx.ACC_NOT_IMPLEMENTED, ""


# Widget classes whose default screen-reader announcement is just a
# short label / role / state — for these, folding the hint into the
# accessible name is genuinely useful because there is no other
# channel that NVDA reads automatically. wx.TextCtrl and wx.ListBox
# already expose their value, so we leave their name alone and only
# populate ``accDescription``.
_EMBED_HINT_INTO_NAME: tuple[type, ...] = (
    wx.Button,
    wx.CheckBox,
    wx.Choice,
    wx.Gauge,
)


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
    so the hint reaches NVDA via the accessible name (see the module
    docstring for why ``SetHelpText`` and ``SetToolTip`` aren't enough).
    """
    if hasattr(widget, "SetName"):
        widget.SetName(name)
    # Local import keeps Linux / Windows builds from loading the
    # ctypes-backed bridge module unnecessarily; the module itself
    # already gates its initialisation on sys.platform.
    from nonvisualaudio.ui import macos_a11y

    macos_a11y.set_accessibility_title(widget, name)

    # Stash the pieces on the widget so update_help can rebuild the
    # composite name on Windows whenever the description changes.
    # Plain attribute assignment is safe — wxPython widgets are
    # ordinary Python objects with a __dict__.
    widget._a11y_name = name
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
    if hasattr(widget, "_a11y_name"):
        widget._a11y_description = description
        _push_windows_accessible(widget)


def _push_windows_accessible(widget: Any) -> None:
    """Update the widget's wx.Accessible from its stored name + hint.

    No-op on non-Windows platforms. Reuses the same ``_A11yProvider``
    instance across calls so a high-frequency caller (the progress
    label updates several times a second during analysis) doesn't
    churn through allocations.
    """
    if not _WX_ACC_USABLE:
        return
    name = getattr(widget, "_a11y_name", "") or ""
    description = getattr(widget, "_a11y_description", "") or ""
    if not name and not description:
        return
    if (
        name
        and description
        and isinstance(widget, _EMBED_HINT_INTO_NAME)
    ):
        # The period + space separator gives NVDA a natural prosody
        # break between label and hint without sounding like a
        # run-on phrase.
        composite = f"{name}. {description}"
    else:
        composite = name or description
    provider = getattr(widget, "_a11y_provider", None)
    if provider is None:
        try:
            provider = _A11yProvider(widget, composite, description)
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
        provider.update(composite, description)
