"""Application entry point."""

from __future__ import annotations

import logging
import os
import sys
import traceback

import wx

from nonvisualaudio.errors import UserFacingError
from nonvisualaudio.ui.error_dialog import show_error
from nonvisualaudio.ui.main_window import MainWindow


def _configure_logging() -> None:
    """Enable structured logging when NVA_DEBUG=1.

    All UI events, analysis steps, and subprocess invocations are logged to
    stderr with timestamps so an external watcher can follow the live stream.
    """
    debug = os.environ.get("NVA_DEBUG", "").strip() not in ("", "0", "false", "False")
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )
    if debug:
        logging.getLogger("nonvisualaudio").info("Debug logging enabled (NVA_DEBUG=1).")


class NonvisualAudioApp(wx.App):
    """Top-level wx application with a crash catcher.

    Any exception that escapes a wx callback is routed through the
    friendly error dialog rather than dumping a traceback to the
    console. That way the user always sees something useful, even when
    the failure is on our side.
    """

    def OnInit(self) -> bool:  # noqa: N802 — wx convention
        self.SetAppName("NonvisualAudio")
        self.SetVendorName("NonvisualAudio")
        window = MainWindow()
        window.Show()
        self.SetTopWindow(window)
        return True

    def OnExceptionInMainLoop(self) -> bool:  # noqa: N802 — wx convention
        exc_type, exc, tb = sys.exc_info()
        logging.getLogger("nonvisualaudio").exception(
            "unhandled exception in main loop: %s", exc
        )
        top = self.GetTopWindow()
        if isinstance(exc, UserFacingError):
            show_error(top, exc)
            return True
        detail = "".join(traceback.format_exception_only(exc_type, exc)).strip()
        show_error(
            top,
            UserFacingError(
                title="Something went wrong inside NonvisualAudio",
                body=(
                    "The app hit an unexpected problem and recovered. Your "
                    "selections were not lost, and you can try again.\n\n"
                    f"Technical detail: {detail}"
                ),
                hint=(
                    "If this keeps happening, relaunch NonvisualAudio from a "
                    "terminal with NVA_DEBUG=1 set — that writes a detailed "
                    "log to the terminal so the problem can be diagnosed."
                ),
            ),
        )
        return True


def main() -> int:
    _configure_logging()
    app = NonvisualAudioApp(False)
    try:
        app.MainLoop()
    except UserFacingError as exc:
        # Startup-time errors never had a chance to reach the event loop.
        show_error(None, exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
