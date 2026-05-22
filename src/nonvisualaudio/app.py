"""Application entry point."""

from __future__ import annotations

import logging
import os
import sys
import traceback

import wx

from nonvisualaudio import localization, logging_setup, preferences
from nonvisualaudio.errors import UserFacingError
from nonvisualaudio.localization import t
from nonvisualaudio.reporting import genre_profiles
from nonvisualaudio.ui import theme
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
    # Add the rotating file log on top of the stderr handler so support
    # cases can be diagnosed after the fact — stderr is invisible to the
    # user in a bundled app. The stderr handler keeps its own threshold.
    logging_setup.init_file_logging()
    logging_setup.install_excepthooks()


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
                title=t("error.app.crashed.title"),
                body=t("error.app.crashed.body", detail=detail),
                hint=t("error.app.crashed.hint"),
            ),
        )
        return True


def _configure_language() -> None:
    """Resolve and load the UI/report language before any UI is built.

    Priority chain matches the documented behaviour:
    ``NVA_LANG`` env var → saved preference → system locale → English.
    """
    env = os.environ.get("NVA_LANG", "").strip() or None
    pref = preferences.load_language()
    lang = localization.resolve_lang(env_override=env, preference=pref)
    localization.load(lang)
    # Genre profiles were already imported with the default (English)
    # catalogue; reload them so their display_name/notes reflect the
    # language we just chose.
    genre_profiles.reload()


def _configure_theme() -> None:
    """Pick the active theme key without colouring any widgets yet.

    Priority: ``NVA_THEME`` env var → saved preference → ``auto``.
    The actual palette is applied once the main window has been built.
    """
    env = os.environ.get("NVA_THEME", "").strip().lower() or None
    key = env if env in theme.VALID_THEMES else None
    if key is None:
        saved = preferences.load_theme()
        if saved in theme.VALID_THEMES:
            key = saved
    theme.set_current(key or theme.DEFAULT_THEME)


def main() -> int:
    _configure_logging()
    _configure_language()
    # The banner is logged after language resolution so it records the
    # language the session actually runs in.
    logging_setup.log_session_banner()
    _configure_theme()
    app = NonvisualAudioApp(False)
    try:
        app.MainLoop()
    except UserFacingError as exc:
        # Startup-time errors never had a chance to reach the event loop.
        logging.getLogger("nonvisualaudio").exception("startup error: %s", exc)
        show_error(None, exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
