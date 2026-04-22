"""Cross-platform paths for user-writable resources.

Kept separate from wxPython so the analysis/reporting layers can read
user overrides without having to pull in wx (which the test suite never
initialises). The directory is only created lazily by callers that
actually write to it; just importing this module does not touch the
filesystem.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

_APP_NAME = "NonvisualAudio"


def user_data_dir() -> Path:
    """Return the per-user data directory for the app.

    macOS:   ~/Library/Application Support/NonvisualAudio
    Windows: %APPDATA%/NonvisualAudio  (falls back to ~/AppData/Roaming)
    Linux:   $XDG_CONFIG_HOME/NonvisualAudio or ~/.config/NonvisualAudio
    """
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        return home / "Library" / "Application Support" / _APP_NAME
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / _APP_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else home / ".config"
    return base / _APP_NAME


def user_genres_path() -> Path:
    return user_data_dir() / "genres.json"
