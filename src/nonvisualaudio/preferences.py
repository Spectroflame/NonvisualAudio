"""User preferences — single JSON file in the user data directory.

Kept minimal on purpose. The app's privacy contract states that no
preferences file is written unless the user explicitly changes a
setting (language, theme, …). Reading a missing file is never an
error; writing silently creates the directory when needed.

Structure::

    {
        "language": "de",
        "theme": "dark"
    }

Unknown keys are preserved on save so a future version can add
fields without losing older users' choices.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from nonvisualaudio.paths import user_data_dir

log = logging.getLogger("nonvisualaudio.preferences")


def _preferences_path():
    return user_data_dir() / "preferences.json"


# --------------------------------------------------------------------------- #
# Low-level read/write
# --------------------------------------------------------------------------- #


def load() -> dict[str, Any]:
    path = _preferences_path()
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("preferences file %s is unreadable: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("preferences file %s is not a JSON object; ignoring", path)
        return {}
    return data


def save(prefs: dict[str, Any]) -> bool:
    """Persist preferences to disk. Returns True on success, False on I/O error.

    Failures are logged but never raised: a read-only data directory or
    a full disk should not crash the UI callback that triggered the
    save. The in-memory state stays valid; only the next session will
    not see the change.
    """
    path = _preferences_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(prefs, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as exc:
        log.warning("could not save preferences to %s: %s", path, exc)
        return False
    return True


def _update(key: str, value: Any) -> None:
    prefs = load()
    prefs[key] = value
    save(prefs)


# --------------------------------------------------------------------------- #
# Typed accessors
# --------------------------------------------------------------------------- #


def load_language() -> str | None:
    value = load().get("language")
    return str(value) if isinstance(value, str) and value else None


def save_language(lang: str) -> None:
    _update("language", lang)


def load_theme() -> str | None:
    value = load().get("theme")
    return str(value) if isinstance(value, str) and value else None


def save_theme(theme: str) -> None:
    _update("theme", theme)


def load_report_sections() -> list[str] | None:
    """Return the user's report-section selection as a list of keys.

    ``None`` is returned when the user has never adjusted the picker —
    callers then default to "every section enabled", which matches the
    historical report layout.
    """
    value = load().get("report_sections")
    if not isinstance(value, list):
        return None
    keys = [str(item) for item in value if isinstance(item, str) and item]
    return keys


def save_report_sections(keys: list[str]) -> None:
    _update("report_sections", list(keys))


def load_verbose_logging() -> bool:
    """Return whether verbose (un-redacted) logging is enabled.

    Defaults to ``False`` — the privacy-preserving setting — so a fresh
    install never writes full file paths or the user name into the logs.
    """
    return load().get("verbose_logging") is True


def save_verbose_logging(enabled: bool) -> None:
    _update("verbose_logging", bool(enabled))
