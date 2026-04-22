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


def save(prefs: dict[str, Any]) -> None:
    path = _preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(prefs, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


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
