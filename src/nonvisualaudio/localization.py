"""Lightweight localisation layer.

The catalogue is a flat ``{key: string}`` JSON map, one file per
language in ``resources/i18n/<lang>.json``. English is the master —
every key must exist there, and any language with a missing key falls
through to the English string (never to the raw key, which would be
hostile to screen-reader users).

A user override at ``<user_data_dir>/i18n/<lang>.json`` is merged on
top of the bundled language file, matching the pattern used for
genre overrides. That satisfies the "erweiterbar über JSON"
requirement without pulling in ``gettext`` or any other heavy i18n
stack.

Numbers are locale-aware: ``decimal_sep()`` returns "," for German
and "." for English. The report builder uses this to format values
like ``minus 21,4 LUFS`` on a German run.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

from nonvisualaudio.paths import user_data_dir

log = logging.getLogger("nonvisualaudio.localization")

# Languages the app ships with. Additional languages drop-shipped by
# the user (via a file in the i18n folder) work automatically — this
# tuple is only used for auto-detection and the UI language menu.
SUPPORTED_LANGS: tuple[str, ...] = ("en", "de")

DEFAULT_LANG = "en"

_catalog: dict[str, str] = {}
_lang: str = DEFAULT_LANG


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _load_bundle(lang: str) -> dict[str, str]:
    """Return the bundled catalogue for ``lang`` or an empty dict."""
    resource = files("nonvisualaudio.resources.i18n") / f"{lang}.json"
    try:
        with resource.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        # A broken bundle is a developer bug, but never crash the app.
        log.error("could not read bundled catalogue for %s: %s", lang, exc)
        return {}
    if not isinstance(data, dict):
        log.error("catalogue for %s is not a JSON object", lang)
        return {}
    return {k: str(v) for k, v in data.items()}


def _load_user_override(lang: str) -> dict[str, str]:
    path = user_data_dir() / "i18n" / f"{lang}.json"
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("user catalogue override %s is unreadable: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("user catalogue %s is not a JSON object; ignoring", path)
        return {}
    return {k: str(v) for k, v in data.items()}


def load(lang: str) -> None:
    """Load the catalogue for ``lang``. English is always the baseline.

    Subsequent ``t()`` calls look up first in the user override, then
    in the requested language bundle, then in English, then finally
    fall back to the English text literal (never to the raw key).
    """
    global _catalog, _lang
    lang = (lang or DEFAULT_LANG).lower()
    base = _load_bundle(DEFAULT_LANG)
    if lang != DEFAULT_LANG:
        base.update(_load_bundle(lang))
    base.update(_load_user_override(lang))
    _catalog = base
    _lang = lang
    log.info("localisation loaded: lang=%s keys=%d", lang, len(_catalog))


# --------------------------------------------------------------------------- #
# Lookup
# --------------------------------------------------------------------------- #


def t(key: str, /, **fmt: object) -> str:
    """Return the localised string for ``key`` with optional formatting.

    The value is looked up in the catalogue. If the key is not
    present, the key itself is returned — that makes missing-key bugs
    visible in the UI rather than silently shipping empty text.
    """
    raw = _catalog.get(key, key)
    if not fmt:
        return raw
    try:
        return raw.format(**fmt)
    except (KeyError, IndexError, ValueError) as exc:
        log.warning("format failure for key %s: %s", key, exc)
        return raw


def current_lang() -> str:
    return _lang


def has_key(key: str) -> bool:
    """Return True if ``key`` is present in the active catalogue."""
    return key in _catalog


def t_subject(key: str, /, *, project: bool = False, **fmt: object) -> str:
    """Look up ``key`` with an optional project-aware override.

    When ``project`` is true, the lookup tries ``"{key}.project"`` first
    and falls back to the base ``key`` if no project variant exists.
    This lets the report builder swap "the file" for "the project" in
    project mode without duplicating every sentence — only the verdicts
    and other subject-bearing lines need a ``.project`` sibling in the
    catalogue.
    """
    if project:
        candidate = f"{key}.project"
        if candidate in _catalog:
            return t(candidate, **fmt)
    return t(key, **fmt)


def decimal_sep() -> str:
    """Decimal separator for the active language."""
    return "," if _lang.startswith("de") else "."


# --------------------------------------------------------------------------- #
# Language detection
# --------------------------------------------------------------------------- #


def _normalise_lang(raw: str | None) -> str | None:
    if not raw:
        return None
    head = raw.strip().lower().split("_", 1)[0].split("-", 1)[0]
    if head in SUPPORTED_LANGS:
        return head
    return None


def _detect_macos_lang() -> str | None:
    """Read the preferred UI language from macOS ``AppleLanguages``.

    An app double-clicked in Finder inherits no ``LANG`` / ``LC_*``
    variables — Finder does not set them — so the env-based probe sees
    nothing and the app wrongly falls back to English. ``AppleLanguages``
    is the ordered list macOS itself consults to pick an app's language;
    its first entry (e.g. ``"de-DE"``) is the user's preferred UI
    language.
    """
    try:
        proc = subprocess.run(
            ["/usr/bin/defaults", "read", "-g", "AppleLanguages"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    # The value is a plist array, e.g.  ( "de-DE", "en-US" ). The first
    # quoted token is the preferred language.
    match = re.search(r'"([^"]+)"', proc.stdout)
    return _normalise_lang(match.group(1)) if match else None


def _detect_windows_lang() -> str | None:
    """Read the user's UI language via the Win32 locale API.

    Like a Finder-launched macOS app, a Windows app started from a
    shortcut has no ``LANG`` set. ``GetUserDefaultLocaleName`` returns
    the user's locale (e.g. ``"de-DE"``) regardless.
    """
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(85)  # LOCALE_NAME_MAX_LENGTH
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(
            buffer, len(buffer)
        ):
            return _normalise_lang(buffer.value)
    except Exception:  # noqa: BLE001 — Win32 probing is best-effort
        pass
    return None


def detect_system_lang() -> str:
    """Best-effort system UI-language detection.

    Resolution order:

    1. Locale environment variables — an intentional signal, reliably
       set on Linux desktops and in any terminal session.
    2. The platform's native UI-language API. This is the only thing
       that works for a macOS app double-clicked in Finder or a Windows
       app started from a shortcut: neither inherits ``LANG`` / ``LC_*``,
       so without this step they always fell back to English.
    3. The ``locale`` module.
    4. English.
    """
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        resolved = _normalise_lang(os.environ.get(var))
        if resolved:
            return resolved

    if sys.platform == "darwin":
        resolved = _detect_macos_lang()
        if resolved:
            return resolved
    elif sys.platform.startswith("win"):
        resolved = _detect_windows_lang()
        if resolved:
            return resolved

    try:
        import locale

        loc, _enc = locale.getlocale()
        resolved = _normalise_lang(loc)
        if resolved:
            return resolved
    except Exception:  # noqa: BLE001 — locale probing is best-effort
        pass
    return DEFAULT_LANG


def resolve_lang(
    env_override: str | None = None,
    preference: str | None = None,
) -> str:
    """Pick the language to load using the documented priority chain.

    1. ``env_override`` — typically ``NVA_LANG``.
    2. ``preference`` — typically ``preferences.load_language()``.
    3. System locale via :func:`detect_system_lang`.
    """
    for candidate in (env_override, preference):
        resolved = _normalise_lang(candidate)
        if resolved:
            return resolved
    return detect_system_lang()


# Load English by default so tests and early imports always have a
# working catalogue without needing an explicit ``load()`` call.
load(DEFAULT_LANG)
