"""Genre reference targets used for comparison mode.

Data lives in ``resources/genres.json`` so the set can be maintained
without touching code. A user override at
``<user_data_dir>/genres.json`` is merged on top of the bundled defaults:
entries with the same ``key`` replace the bundled ones, new keys are
appended. That lets the in-app genre editor add or tweak profiles
without writing to the package folder.

The module exposes the same API as before (``GENRES`` dict,
``list_genres()``, ``grouped_genres()``, ``CATEGORY_ORDER``) so callers
and tests do not need to change.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from nonvisualaudio.localization import DEFAULT_LANG, current_lang
from nonvisualaudio.paths import user_genres_path

log = logging.getLogger("nonvisualaudio.genre_profiles")

_BUNDLE_RESOURCE = "genres.json"


def _resolve_localised(value: Any, lang: str) -> str:
    """Pick the localised text from a plain string or a ``{lang: text}`` dict.

    Supports both the legacy format (a bare string) — so user-added
    profiles from before the localisation upgrade keep working — and
    the new dict format. Falls back to English, then to any non-empty
    value, and finally to an empty string.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for k in (lang, DEFAULT_LANG):
            v = value.get(k)
            if v:
                return str(v)
        for v in value.values():
            if v:
                return str(v)
    return ""


@dataclass(frozen=True)
class GenreProfile:
    key: str
    display_name: str
    category: str  # human-readable category name (already resolved)
    target_lufs: float
    lra_low: float
    lra_high: float
    notes: str


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _load_bundle() -> dict[str, Any]:
    with (files("nonvisualaudio.resources") / _BUNDLE_RESOURCE).open(
        "r", encoding="utf-8"
    ) as fh:
        return json.load(fh)


def _load_user_override() -> dict[str, Any] | None:
    path = user_genres_path()
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # A broken user file should never stop the app from starting.
        # Fall through to the bundle and log the reason so a debug run
        # (NVA_DEBUG=1) surfaces the problem.
        log.warning("user genres override at %s is unreadable: %s", path, exc)
        return None


def _merge(
    bundle: dict[str, Any], override: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str], set[str]]:
    """Return (categories, profiles, user_profile_keys, user_category_keys).

    ``user_profile_keys`` contains every profile key that originated in
    the override (new or modified). That distinction is what lets the
    editor show ``[user]`` vs. ``[modified]`` vs. ``[built-in]`` badges.
    Same idea for categories.
    """
    categories: list[dict[str, Any]] = [dict(c) for c in bundle.get("categories", [])]
    profiles: list[dict[str, Any]] = [dict(p) for p in bundle.get("profiles", [])]
    user_profile_keys: set[str] = set()
    user_category_keys: set[str] = set()

    if override is None:
        return categories, profiles, user_profile_keys, user_category_keys

    for cat in override.get("categories", []):
        key = cat.get("key")
        if not key:
            continue
        user_category_keys.add(key)
        for i, existing in enumerate(categories):
            if existing.get("key") == key:
                categories[i] = dict(cat)
                break
        else:
            categories.append(dict(cat))

    for prof in override.get("profiles", []):
        key = prof.get("key")
        if not key:
            continue
        user_profile_keys.add(key)
        for i, existing in enumerate(profiles):
            if existing.get("key") == key:
                profiles[i] = dict(prof)
                break
        else:
            profiles.append(dict(prof))

    return categories, profiles, user_profile_keys, user_category_keys


def _build_profile_objects(
    categories: list[dict[str, Any]], profiles: list[dict[str, Any]]
) -> tuple[GenreProfile, ...]:
    lang = current_lang()
    category_name_by_key = {
        c["key"]: _resolve_localised(c.get("display_name"), lang) for c in categories
    }
    objects: list[GenreProfile] = []
    for p in profiles:
        cat_key = p.get("category_key", "")
        category_name = category_name_by_key.get(cat_key, cat_key)
        try:
            objects.append(
                GenreProfile(
                    key=p["key"],
                    display_name=_resolve_localised(p.get("display_name"), lang),
                    category=category_name,
                    target_lufs=float(p["target_lufs"]),
                    lra_low=float(p["lra_low"]),
                    lra_high=float(p["lra_high"]),
                    notes=_resolve_localised(p.get("notes", ""), lang),
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            # A malformed profile — missing ``key``, or a missing /
            # non-numeric ``target_lufs`` / ``lra_*`` — must never crash
            # the import-time reload() and with it the whole app launch.
            # Bundled profiles are well-formed, so in practice this only
            # fires on a hand-edited or partially written user override:
            # skip the bad entry and keep every valid profile (including
            # the bundled defaults) available.
            log.warning(
                "skipping malformed genre profile %r: %s",
                p.get("key", "<no key>"),
                exc,
            )
    return tuple(objects)


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

# Cached raw JSON-like dicts for the editor (which needs to know which
# entries came from the user override so it can show the right badge and
# persist changes to the right place).
_bundle_cache: dict[str, Any] | None = None
_override_cache: dict[str, Any] | None = None
_categories: list[dict[str, Any]] = []
_profiles: list[dict[str, Any]] = []
_user_profile_keys: set[str] = set()
_user_category_keys: set[str] = set()

CATEGORY_ORDER: tuple[str, ...] = ()
_PROFILES: tuple[GenreProfile, ...] = ()
GENRES: dict[str, GenreProfile] = {}


def reload() -> None:
    """Reload genre profiles from disk. Safe to call multiple times.

    Call this after a language change so the localised ``display_name``
    and ``notes`` on :class:`GenreProfile` reflect the new language.
    """
    global _bundle_cache, _override_cache
    global _categories, _profiles
    global _user_profile_keys, _user_category_keys
    global CATEGORY_ORDER, _PROFILES, GENRES

    _bundle_cache = _load_bundle()
    _override_cache = _load_user_override()
    _categories, _profiles, _user_profile_keys, _user_category_keys = _merge(
        _bundle_cache, _override_cache
    )
    lang = current_lang()
    CATEGORY_ORDER = tuple(
        _resolve_localised(c.get("display_name"), lang) for c in _categories
    )
    _PROFILES = _build_profile_objects(_categories, _profiles)
    GENRES = {p.key: p for p in _PROFILES}


reload()


# ---------------------------------------------------------------------------
# Public API (unchanged from before)
# ---------------------------------------------------------------------------


def list_genres() -> list[GenreProfile]:
    """Return profiles in display order, grouped by category."""
    ordered: list[GenreProfile] = []
    seen = set()
    for category in CATEGORY_ORDER:
        for p in _PROFILES:
            if p.category == category and p.key not in seen:
                ordered.append(p)
                seen.add(p.key)
    return ordered


def grouped_genres() -> list[tuple[str, list[GenreProfile]]]:
    """Return genres grouped by category, preserving CATEGORY_ORDER."""
    groups: dict[str, list[GenreProfile]] = {c: [] for c in CATEGORY_ORDER}
    for p in _PROFILES:
        groups.setdefault(p.category, []).append(p)
    return [(c, groups[c]) for c in CATEGORY_ORDER if groups[c]]


# ---------------------------------------------------------------------------
# Editor helpers
# ---------------------------------------------------------------------------


def profile_origin(key: str) -> str:
    """Return ``"user"``, ``"modified"``, or ``"built-in"``.

    ``"user"`` — present in the override but not in the bundle.
    ``"modified"`` — present in both, the override shadows the bundle.
    ``"built-in"`` — only in the bundle.
    """
    in_bundle = any(p.get("key") == key for p in (_bundle_cache or {}).get("profiles", []))
    in_user = key in _user_profile_keys
    if in_user and in_bundle:
        return "modified"
    if in_user:
        return "user"
    return "built-in"


def raw_profile(key: str) -> dict[str, Any] | None:
    """Return the raw dict for a profile, suitable for pre-filling the editor.

    ``display_name`` and ``notes`` are returned as the original dict
    ``{en, de}`` (or a plain string for legacy user entries) — the
    editor uses ``localised_field`` to extract individual language
    values.
    """
    for p in _profiles:
        if p.get("key") == key:
            return dict(p)
    return None


def localised_field(value: Any, lang: str) -> str:
    """Read one language value from a stored display_name/notes field.

    Returns the empty string if that language is not stored. Never
    falls back to another language — the form dialog wants to know if
    a field is genuinely missing so it can leave the input empty.
    """
    if isinstance(value, str):
        # Legacy entries are treated as English-only.
        return value if lang == DEFAULT_LANG else ""
    if isinstance(value, dict):
        return str(value.get(lang, "") or "")
    return ""


def raw_profiles() -> list[dict[str, Any]]:
    """Return copies of the merged raw profile dicts."""
    return [dict(p) for p in _profiles]


def raw_categories() -> list[dict[str, Any]]:
    """Return copies of the merged raw category dicts."""
    return [dict(c) for c in _categories]


def bundle_profile(key: str) -> dict[str, Any] | None:
    for p in (_bundle_cache or {}).get("profiles", []):
        if p.get("key") == key:
            return dict(p)
    return None


def user_override_raw() -> dict[str, Any]:
    """Return the current user override, or an empty skeleton if none."""
    if _override_cache is not None:
        return {
            "version": _override_cache.get("version", 1),
            "categories": [dict(c) for c in _override_cache.get("categories", [])],
            "profiles": [dict(p) for p in _override_cache.get("profiles", [])],
        }
    return {"version": 1, "categories": [], "profiles": []}


def save_user_overrides(
    categories: list[dict[str, Any]], profiles: list[dict[str, Any]]
) -> Path:
    """Write the user override JSON and reload. Returns the file path.

    Only call this with the subset that actually differs from the
    bundle — the caller (the editor) is in charge of pruning entries
    whose value is identical to the bundled default.

    When both lists are empty (the user just deleted the last
    override), the JSON file is removed entirely instead of leaving
    an empty stub on disk. That keeps the privacy promise that
    "nothing is written to disk if you don't customise anything"
    intact even after a customise-then-undo round trip.
    """
    path = user_genres_path()
    if not categories and not profiles:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("could not remove empty override at %s: %s", path, exc)
        reload()
        return path
    payload = {
        "version": 1,
        "categories": list(categories),
        "profiles": list(profiles),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as exc:
        # Disk full, read-only home, missing permissions — none of these
        # should crash the editor dialog. The in-memory state already
        # holds the user's edits; only the persistence step failed, and
        # the next launch will fall back to the bundled defaults.
        log.error("could not save genre override to %s: %s", path, exc)
        return path
    reload()
    return path
