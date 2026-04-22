"""Open the bundled HTML help in the system's default browser.

We hand off to the OS browser instead of rendering inside a wx.html
widget because (a) every screen reader already knows how to navigate
HTML in Safari/Edge/Firefox and (b) wx.html has only basic
accessibility support.
"""

from __future__ import annotations

import logging
import webbrowser
from pathlib import Path

from nonvisualaudio.localization import current_lang

log = logging.getLogger("nonvisualaudio.help")

_FALLBACK_LANG = "en"


def _help_root() -> Path:
    """Return the folder that holds the bundled help HTML files."""
    # ui/help_viewer.py → parent = ui/, parent.parent = nonvisualaudio/.
    pkg_root = Path(__file__).resolve().parent.parent
    return pkg_root / "resources" / "help"


def _resolve(lang: str) -> Path | None:
    root = _help_root()
    candidate = root / f"help_{lang}.html"
    if candidate.is_file():
        return candidate
    fallback = root / f"help_{_FALLBACK_LANG}.html"
    if fallback.is_file():
        return fallback
    return None


def open_help() -> bool:
    """Open the help page for the active language in the default browser.

    Returns ``True`` if the call to the OS browser was dispatched,
    ``False`` if no help file could be found. The caller is expected
    to show a friendly fallback message in the ``False`` case.
    """
    path = _resolve(current_lang())
    if path is None:
        log.error("no help html found under %s", _help_root())
        return False
    uri = path.as_uri()
    log.info("opening help: %s", uri)
    try:
        return bool(webbrowser.open(uri, new=2))
    except Exception as exc:  # noqa: BLE001 — any OS-level issue falls here
        log.error("webbrowser.open failed: %s", exc)
        return False
