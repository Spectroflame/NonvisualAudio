"""Tests for the 2.0.4 stability-hardening changes.

These cover small, focused behaviours added during the audit:
- ``_subprocess_env`` preserves dynamic-linker hints per platform.
- ``find_ffmpeg`` records which binary was resolved (bundled vs system)
  and the fallback path is loud in the logs.
- ``preferences.save`` returns ``False`` on I/O errors instead of raising.
- ``genre_profiles.save`` stays non-fatal on I/O errors.
- ``diagnostics.system_info`` surfaces the resolved ffmpeg.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from nonvisualaudio import diagnostics, preferences
from nonvisualaudio.audio import ffmpeg_runner
from nonvisualaudio.reporting import genre_profiles


@pytest.fixture(autouse=True)
def _reset_ffmpeg_cache() -> None:
    """Each test gets a fresh ffmpeg-resolution cache."""
    ffmpeg_runner._active_info = None
    ffmpeg_runner._binary_runs.cache_clear()
    yield
    ffmpeg_runner._active_info = None
    ffmpeg_runner._binary_runs.cache_clear()


# --------------------------------------------------------------------------- #
# _subprocess_env
# --------------------------------------------------------------------------- #


def test_subprocess_env_forwards_dyld_on_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ffmpeg_runner.sys, "platform", "darwin")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/opt/lib")
    monkeypatch.setenv("DYLD_FALLBACK_LIBRARY_PATH", "/opt/fallback")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/should/not/leak")
    env = ffmpeg_runner._subprocess_env()
    assert env["DYLD_LIBRARY_PATH"] == "/opt/lib"
    assert env["DYLD_FALLBACK_LIBRARY_PATH"] == "/opt/fallback"
    # Linux-only vars must not bleed onto macOS.
    assert "LD_LIBRARY_PATH" not in env


def test_subprocess_env_forwards_ld_on_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ffmpeg_runner.sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/usr/local/lib")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/should/not/leak")
    env = ffmpeg_runner._subprocess_env()
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib"
    assert "DYLD_LIBRARY_PATH" not in env


def test_subprocess_env_only_path_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ffmpeg_runner.sys, "platform", "win32")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/should/not/leak")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/should/not/leak")
    env = ffmpeg_runner._subprocess_env()
    assert set(env.keys()) == {"PATH"}


# --------------------------------------------------------------------------- #
# find_ffmpeg bookkeeping
# --------------------------------------------------------------------------- #


def test_find_ffmpeg_records_bundled_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundled = tmp_path / "ffmpeg"
    bundled.write_text("not actually run")
    monkeypatch.setattr(ffmpeg_runner, "_bundled_binary", lambda name: bundled)
    monkeypatch.setattr(ffmpeg_runner, "_binary_runs", lambda path: True)
    monkeypatch.setattr(ffmpeg_runner.shutil, "which", lambda name: None)

    resolved = ffmpeg_runner.find_ffmpeg()
    assert resolved == str(bundled)
    assert ffmpeg_runner.active_ffmpeg_info() == (str(bundled), "bundled")


def test_find_ffmpeg_falls_back_to_system_and_logs_loudly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bundled = tmp_path / "ffmpeg"
    bundled.write_text("broken")
    system = "/usr/local/bin/ffmpeg"
    monkeypatch.setattr(ffmpeg_runner, "_bundled_binary", lambda name: bundled)
    # Bundled probe fails, system probe succeeds.
    monkeypatch.setattr(
        ffmpeg_runner,
        "_binary_runs",
        lambda path: path == system,
    )
    monkeypatch.setattr(ffmpeg_runner.shutil, "which", lambda name: system)

    with caplog.at_level(logging.ERROR, logger="nonvisualaudio.ffmpeg"):
        resolved = ffmpeg_runner.find_ffmpeg()
    assert resolved == system
    assert ffmpeg_runner.active_ffmpeg_info() == (system, "system")
    # The fallback must show up at ERROR level so it lands in support logs.
    fallback_messages = [
        rec.message for rec in caplog.records if rec.levelno >= logging.ERROR
    ]
    assert any("system PATH" in msg for msg in fallback_messages)


def test_active_ffmpeg_info_is_none_before_resolution() -> None:
    assert ffmpeg_runner.active_ffmpeg_info() is None


# --------------------------------------------------------------------------- #
# preferences.save non-fatal
# --------------------------------------------------------------------------- #


def test_preferences_save_returns_false_on_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Point save() at a path whose parent we cannot create (file in place
    # of a directory). It must log and return False, never raise.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setattr(
        preferences, "_preferences_path", lambda: blocker / "preferences.json"
    )
    result = preferences.save({"language": "de"})
    assert result is False


def test_preferences_save_succeeds_and_returns_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        preferences, "_preferences_path", lambda: tmp_path / "preferences.json"
    )
    assert preferences.save({"language": "de"}) is True
    assert (tmp_path / "preferences.json").is_file()


# --------------------------------------------------------------------------- #
# genre_profiles.save non-fatal
# --------------------------------------------------------------------------- #


def test_genre_profiles_save_swallows_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setattr(
        genre_profiles, "user_genres_path", lambda: blocker / "genres.json"
    )
    # Should not raise. The empty-payload short-circuit doesn't apply
    # because we pass non-empty categories.
    genre_profiles.save_user_overrides(
        categories=[{"key": "test", "label": "Test"}],
        profiles=[],
    )


# --------------------------------------------------------------------------- #
# diagnostics surfaces ffmpeg info
# --------------------------------------------------------------------------- #


def test_system_info_reports_unresolved_ffmpeg() -> None:
    info = diagnostics.system_info()
    assert "ffmpeg in use" in info
    assert "not resolved" in info


def test_system_info_reports_resolved_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ffmpeg_runner,
        "_active_info",
        ("/opt/bundle/ffmpeg", "bundled"),
    )
    info = diagnostics.system_info()
    assert "bundled" in info
    assert "ffmpeg" in info
