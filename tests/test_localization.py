"""Tests for the localisation layer and user preferences."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nonvisualaudio import localization, preferences


@pytest.fixture
def pinned_user_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(preferences, "user_data_dir", lambda: tmp_path)
    # localization reads from the same helper for user overrides.
    monkeypatch.setattr(localization, "user_data_dir", lambda: tmp_path)
    yield tmp_path
    # Restore English so later tests are untouched.
    localization.load("en")


# --------------------------------------------------------------------------- #
# t() and decimal_sep()
# --------------------------------------------------------------------------- #


def test_english_catalog_loads_default_keys():
    localization.load("en")
    assert localization.current_lang() == "en"
    assert localization.t("status.idle") == "Ready. Press Analyze to begin."


def test_german_catalog_overrides_english():
    localization.load("de")
    assert localization.current_lang() == "de"
    assert localization.t("status.idle").startswith("Bereit.")
    # Coming back to English must fully restore the English strings.
    localization.load("en")
    assert localization.t("status.idle") == "Ready. Press Analyze to begin."


def test_unknown_key_returns_raw_key():
    localization.load("en")
    assert localization.t("totally.missing.key") == "totally.missing.key"


def test_de_and_en_catalogs_have_identical_keys():
    """Every key must exist in both bundled catalogs.

    English is the master; a key present in only one file means a
    translation was forgotten. Asserting the two difference lists
    separately makes the failure message name the missing keys.
    """
    en = set(localization._load_bundle("en"))
    de = set(localization._load_bundle("de"))
    assert sorted(en - de) == [], "keys missing in de.json"
    assert sorted(de - en) == [], "keys missing in en.json"


def test_format_substitution_applies_named_kwargs():
    # Use the lower-level catalogue so we do not have to ship a fake
    # key in the JSON files.
    localization._catalog["test.greeting"] = "Hello {name}, it is {hour} o'clock."
    try:
        assert (
            localization.t("test.greeting", name="Alex", hour=9)
            == "Hello Alex, it is 9 o'clock."
        )
    finally:
        localization._catalog.pop("test.greeting", None)


def test_format_failure_returns_raw_template():
    localization._catalog["test.broken"] = "Hello {missing_placeholder}"
    try:
        assert localization.t("test.broken", name="Alex") == "Hello {missing_placeholder}"
    finally:
        localization._catalog.pop("test.broken", None)


def test_decimal_sep_follows_language():
    localization.load("en")
    assert localization.decimal_sep() == "."
    localization.load("de")
    assert localization.decimal_sep() == ","
    localization.load("en")


# --------------------------------------------------------------------------- #
# Language detection / resolution
# --------------------------------------------------------------------------- #


def test_resolve_lang_env_override_wins(monkeypatch: pytest.MonkeyPatch):
    assert localization.resolve_lang(env_override="de", preference="en") == "de"


def test_resolve_lang_preference_used_when_no_env():
    assert localization.resolve_lang(env_override=None, preference="de") == "de"


def test_resolve_lang_normalises_locale_strings():
    assert localization.resolve_lang(env_override="de_DE.UTF-8") == "de"
    assert localization.resolve_lang(env_override="en-US") == "en"


def test_resolve_lang_unknown_language_falls_through(monkeypatch: pytest.MonkeyPatch):
    # Unknown override + unknown preference + unknown env → English.
    # Every detection source has to be neutralised: env vars, the
    # platform-native UI-language probes, and ``locale.getlocale()``
    # (which caches the process locale independently of the environment).
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.setenv(var, "xx_ZZ")
    monkeypatch.setattr(localization, "_detect_macos_lang", lambda: None)
    monkeypatch.setattr(localization, "_detect_windows_lang", lambda: None)
    import locale as _locale
    monkeypatch.setattr(_locale, "getlocale", lambda *_a, **_k: ("xx_ZZ", None))
    assert localization.resolve_lang(env_override="xx", preference="xx") == "en"


def test_detect_system_lang_reads_env_vars(monkeypatch: pytest.MonkeyPatch):
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert localization.detect_system_lang() == "de"


def test_detect_macos_lang_parses_apple_languages(monkeypatch: pytest.MonkeyPatch):
    # `defaults read -g AppleLanguages` prints a plist array; the first
    # quoted token is the user's preferred UI language.
    class _FakeProc:
        returncode = 0
        stdout = '(\n    "de-DE",\n    "en-US"\n)\n'

    monkeypatch.setattr(
        localization.subprocess, "run", lambda *_a, **_k: _FakeProc()
    )
    assert localization._detect_macos_lang() == "de"


def test_detect_system_lang_uses_platform_probe_when_env_empty(
    monkeypatch: pytest.MonkeyPatch,
):
    # No locale env vars — the case of a Finder-launched macOS app.
    # Detection must fall through to the native platform probe instead
    # of giving up and returning English.
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(localization.sys, "platform", "darwin")
    monkeypatch.setattr(localization, "_detect_macos_lang", lambda: "de")
    assert localization.detect_system_lang() == "de"


# --------------------------------------------------------------------------- #
# Preferences
# --------------------------------------------------------------------------- #


def test_missing_preferences_file_is_ok(pinned_user_dir: Path):
    assert preferences.load_language() is None
    assert preferences.load_theme() is None
    assert preferences.load() == {}


def test_save_language_round_trip(pinned_user_dir: Path):
    preferences.save_language("de")
    assert preferences.load_language() == "de"
    on_disk = json.loads((pinned_user_dir / "preferences.json").read_text())
    assert on_disk == {"language": "de"}


def test_save_preserves_unknown_keys(pinned_user_dir: Path):
    # Write a hypothetical future key directly.
    (pinned_user_dir / "preferences.json").write_text(
        json.dumps({"language": "de", "some_future_key": 42}),
        encoding="utf-8",
    )
    # Touching only `theme` must not wipe `some_future_key`.
    preferences.save_theme("dark")
    on_disk = json.loads((pinned_user_dir / "preferences.json").read_text())
    assert on_disk == {
        "language": "de",
        "some_future_key": 42,
        "theme": "dark",
    }


def test_broken_preferences_file_is_not_an_error(pinned_user_dir: Path):
    (pinned_user_dir / "preferences.json").write_text("{ not valid", encoding="utf-8")
    assert preferences.load_language() is None
    assert preferences.load() == {}


def test_report_sections_round_trip(pinned_user_dir: Path):
    # Default: never set → load returns None and callers default to "all".
    assert preferences.load_report_sections() is None
    preferences.save_report_sections(["loudness", "frequency"])
    assert preferences.load_report_sections() == ["loudness", "frequency"]


# --------------------------------------------------------------------------- #
# User override of the catalogue
# --------------------------------------------------------------------------- #


def test_user_catalogue_override_wins_over_bundle(pinned_user_dir: Path):
    i18n_dir = pinned_user_dir / "i18n"
    i18n_dir.mkdir()
    (i18n_dir / "en.json").write_text(
        json.dumps({"status.idle": "Custom idle text from the user."}),
        encoding="utf-8",
    )
    localization.load("en")
    assert localization.t("status.idle") == "Custom idle text from the user."


def test_user_catalogue_partial_override_preserves_other_keys(pinned_user_dir: Path):
    i18n_dir = pinned_user_dir / "i18n"
    i18n_dir.mkdir()
    (i18n_dir / "de.json").write_text(
        json.dumps({"status.idle": "Spezieller Text"}),
        encoding="utf-8",
    )
    localization.load("de")
    assert localization.t("status.idle") == "Spezieller Text"
    # Other German keys from the bundle are still reachable.
    assert localization.t("status.running").startswith("Analyse")
