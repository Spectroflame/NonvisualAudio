"""Set NSAccessibility title on wx widgets via the Objective-C runtime.

Background: wxPython's :py:meth:`wx.Window.SetName` on macOS maps to
``[NSView setAccessibilityIdentifier:]`` rather than to
``setAccessibilityTitle:``. VoiceOver reads ``accessibilityTitle``,
not the identifier — which is why wx.TextCtrls in our forms were
announced as "edit text" only, with no field name. The fix is to
call ``setAccessibilityTitle:`` directly on the underlying NSView.

This module is a no-op on every non-macOS platform, so callers can
invoke :func:`set_accessibility_title` unconditionally — it falls
through cheaply on Windows / Linux.

PyObjC is intentionally not used: the Objective-C runtime is reachable
through ``ctypes`` alone, which keeps the runtime dependency surface
minimal (the project already ships zero network libraries; we don't
want to add an interpreter-level ObjC bridge either).
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any

log = logging.getLogger("nonvisualaudio.macos_a11y")

_AVAILABLE = False

# Module-level handles to the runtime. Populated lazily on macOS only.
_libobjc = None
_msg_send_charp = None
_msg_send_void = None
_msg_send_id = None
_msg_send_bool_sel = None
_NSSTRING_CLASS: int = 0
_SEL_STRING_FROM_UTF8: int = 0
_SEL_SET_ACCESSIBILITY_TITLE: int = 0
_SEL_DOCUMENT_VIEW: int = 0
_SEL_RESPONDS_TO_SELECTOR: int = 0


def _initialise_runtime() -> bool:
    """Wire ctypes up to ``libobjc``. Returns True on success.

    ``objc_msgSend`` is variadic in the C ABI, but ctypes needs a
    fixed prototype per call site. We cast the same symbol address
    into two CFUNCTYPE-typed views so that one call can pass an
    NSString* and another a const char*.
    """
    global _libobjc
    global _msg_send_charp, _msg_send_void, _msg_send_id, _msg_send_bool_sel
    global _NSSTRING_CLASS
    global _SEL_STRING_FROM_UTF8, _SEL_SET_ACCESSIBILITY_TITLE
    global _SEL_DOCUMENT_VIEW, _SEL_RESPONDS_TO_SELECTOR

    libobjc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
    _libobjc = libobjc
    libobjc.objc_getClass.restype = ctypes.c_void_p
    libobjc.objc_getClass.argtypes = [ctypes.c_char_p]
    libobjc.sel_registerName.restype = ctypes.c_void_p
    libobjc.sel_registerName.argtypes = [ctypes.c_char_p]
    # object_getClass / class_getName let us read the dynamic class
    # name of an NSView so the bridge can opt out of overriding
    # accessibility on widgets that already self-describe (NSButton).
    libobjc.object_getClass.restype = ctypes.c_void_p
    libobjc.object_getClass.argtypes = [ctypes.c_void_p]
    libobjc.class_getName.restype = ctypes.c_char_p
    libobjc.class_getName.argtypes = [ctypes.c_void_p]

    msg_send_addr = ctypes.cast(libobjc.objc_msgSend, ctypes.c_void_p).value
    if msg_send_addr is None:
        return False

    # [NSString stringWithUTF8String:(const char*)] -> id
    proto_charp = ctypes.CFUNCTYPE(
        ctypes.c_void_p,  # returns id (NSString*)
        ctypes.c_void_p,  # self (Class)
        ctypes.c_void_p,  # _cmd (SEL)
        ctypes.c_char_p,  # const char *utf8
    )
    _msg_send_charp = proto_charp(msg_send_addr)

    # [view setAccessibilityTitle:(NSString*)] -> void
    proto_void = ctypes.CFUNCTYPE(
        None,
        ctypes.c_void_p,  # self (NSView*)
        ctypes.c_void_p,  # _cmd (SEL)
        ctypes.c_void_p,  # NSString*
    )
    _msg_send_void = proto_void(msg_send_addr)

    # [view documentView] -> id (used to walk into NSScrollView-wrapped
    # multiline text controls so we can title the inner NSTextView,
    # which is what VoiceOver actually focuses on).
    proto_id = ctypes.CFUNCTYPE(
        ctypes.c_void_p,  # returns id
        ctypes.c_void_p,  # self
        ctypes.c_void_p,  # _cmd
    )
    _msg_send_id = proto_id(msg_send_addr)

    # [view respondsToSelector:(SEL)] -> BOOL — guards documentView so
    # we don't send it to NSViews that aren't NSScrollViews.
    proto_bool_sel = ctypes.CFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_void_p,  # self
        ctypes.c_void_p,  # _cmd
        ctypes.c_void_p,  # SEL
    )
    _msg_send_bool_sel = proto_bool_sel(msg_send_addr)

    _NSSTRING_CLASS = libobjc.objc_getClass(b"NSString")
    _SEL_STRING_FROM_UTF8 = libobjc.sel_registerName(b"stringWithUTF8String:")
    _SEL_SET_ACCESSIBILITY_TITLE = libobjc.sel_registerName(
        b"setAccessibilityTitle:"
    )
    _SEL_DOCUMENT_VIEW = libobjc.sel_registerName(b"documentView")
    _SEL_RESPONDS_TO_SELECTOR = libobjc.sel_registerName(
        b"respondsToSelector:"
    )

    if not (
        _NSSTRING_CLASS
        and _SEL_STRING_FROM_UTF8
        and _SEL_SET_ACCESSIBILITY_TITLE
        and _SEL_DOCUMENT_VIEW
        and _SEL_RESPONDS_TO_SELECTOR
    ):
        return False
    return True


if sys.platform == "darwin":
    try:
        _AVAILABLE = _initialise_runtime()
    except Exception as exc:  # noqa: BLE001 — best-effort init
        log.warning("macOS a11y bridge unavailable: %s", exc)
        _AVAILABLE = False


def is_available() -> bool:
    """True when calls to :func:`set_accessibility_title` will do work."""
    return _AVAILABLE


def _apply_title(view_ptr: int, ns_str: int) -> None:
    """Send setAccessibilityTitle: to a single NSView pointer."""
    _msg_send_void(
        ctypes.c_void_p(int(view_ptr)),
        _SEL_SET_ACCESSIBILITY_TITLE,
        ns_str,
    )


def _class_name_of(view_ptr: int) -> str:
    """Return the Objective-C class name of an NSView pointer.

    Empty string on failure. Used to opt the bridge out of widgets
    whose visible label already serves as their accessibility title
    (most importantly NSButton — wx button labels would otherwise be
    silently replaced by whatever was passed to ``SetName``).

    Uses the module-level ``_libobjc`` handle so the carefully set
    argtypes (in particular the c_void_p pointer types) are honoured;
    re-loading the library inside this function would discard them
    and let ctypes truncate the pointer to a 32-bit int.
    """
    if _libobjc is None:
        return ""
    try:
        cls = _libobjc.object_getClass(ctypes.c_void_p(view_ptr))
        if not cls:
            return ""
        name = _libobjc.class_getName(cls)
        return name.decode("utf-8") if name else ""
    except Exception:  # noqa: BLE001
        return ""


def _document_view(view_ptr: int) -> int:
    """Return the documentView NSView* for an NSScrollView, else 0.

    Multiline ``wx.TextCtrl`` on macOS is an NSScrollView wrapping an
    NSTextView. ``GetHandle`` returns the outer scroll view, but
    VoiceOver focuses on the inner NSTextView — so the title needs to
    land on both for screen readers to find it.
    """
    p = ctypes.c_void_p(int(view_ptr))
    sel = ctypes.c_void_p(_SEL_DOCUMENT_VIEW)
    responds = _msg_send_bool_sel(p, _SEL_RESPONDS_TO_SELECTOR, sel)
    if not responds:
        return 0
    inner = _msg_send_id(p, _SEL_DOCUMENT_VIEW)
    return int(inner) if inner else 0


def set_accessibility_title(widget: Any, title: str) -> None:
    """Set NSAccessibility's title on the underlying NSView.

    No-op on non-macOS platforms, when the widget has no native peer
    yet, or when ``title`` is empty. Any failure is swallowed —
    accessibility hints must never crash the application.

    For widgets backed by an NSScrollView (most importantly multiline
    ``wx.TextCtrl``), the title is also applied to the
    ``documentView`` so VoiceOver picks it up on the inner text view.
    """
    if not _AVAILABLE or not title:
        return
    try:
        handle = widget.GetHandle()
    except Exception:  # noqa: BLE001 — every wx widget should expose this
        return
    if not handle:
        return
    # NSButton's accessibilityTitle defaults to the visible button
    # label. Overriding it would silently mask the user-facing text
    # (e.g. "Add Audio Files…" would be replaced by whatever
    # wx.SetName was passed). wxPython buttons report as ``wxNSButton``;
    # the popup-menu variant is ``wxNSPopUpButton``, which we still
    # want to title via the bridge so VoiceOver announces "Category"
    # plus "popup menu".
    class_name = _class_name_of(int(handle))
    if class_name == "wxNSButton":
        return
    try:
        ns_str = _msg_send_charp(
            _NSSTRING_CLASS,
            _SEL_STRING_FROM_UTF8,
            title.encode("utf-8"),
        )
        if not ns_str:
            return
        _apply_title(int(handle), ns_str)
        inner = _document_view(int(handle))
        if inner:
            _apply_title(inner, ns_str)
    except Exception as exc:  # noqa: BLE001
        log.debug("setAccessibilityTitle failed: %s", exc)
