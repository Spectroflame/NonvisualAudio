"""Regression tests for atomic preference persistence."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, TextIO

import pytest

from nonvisualaudio import persistence, preferences


def _pin_preferences_path(
    monkeypatch: pytest.MonkeyPatch, path: Path
) -> None:
    monkeypatch.setattr(preferences, "_preferences_path", lambda: path)


def test_preferences_save_atomically_replaces_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "preferences.json"
    path.write_text('{"old": true}\n', encoding="utf-8")
    _pin_preferences_path(monkeypatch, path)

    assert preferences.save({"language": "de", "theme": "dark"}) is True

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "language": "de",
        "theme": "dark",
    }
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not portable")
def test_preferences_save_preserves_existing_file_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "preferences.json"
    path.write_text('{"language": "en"}\n', encoding="utf-8")
    path.chmod(0o640)
    _pin_preferences_path(monkeypatch, path)

    assert preferences.save({"language": "de"}) is True
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not portable")
def test_atomic_write_json_skips_mode_copy_on_non_posix_platforms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "preferences.json"
    path.write_text('{"language": "en"}\n', encoding="utf-8")
    path.chmod(0o400)
    monkeypatch.setattr(persistence, "_PRESERVE_TARGET_MODE", False)

    persistence.atomic_write_json(path, {"language": "de"})

    assert stat.S_IMODE(path.stat().st_mode) & stat.S_IWUSR


def test_preferences_save_failure_preserves_existing_file_and_removes_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "preferences.json"
    original = b'{"language": "en"}\n'
    path.write_bytes(original)
    _pin_preferences_path(monkeypatch, path)

    def fail_mid_write(data: Any, fh: TextIO, **kwargs: Any) -> None:
        fh.write('{"language": ')
        raise OSError("simulated write failure")

    monkeypatch.setattr(persistence.json, "dump", fail_mid_write)

    assert preferences.save({"language": "de"}) is False
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_preferences_replace_failure_preserves_existing_file_and_removes_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "preferences.json"
    original = b'{"language": "en"}\n'
    path.write_bytes(original)
    _pin_preferences_path(monkeypatch, path)

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(persistence.os, "replace", fail_replace)

    assert preferences.save({"language": "de"}) is False
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_preferences_save_creates_missing_target_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "new-user" / "preferences.json"
    _pin_preferences_path(monkeypatch, path)

    assert preferences.save({"language": "de"}) is True
    assert json.loads(path.read_text(encoding="utf-8")) == {"language": "de"}


def test_atomic_write_json_cleans_temp_after_non_io_serialization_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "preferences.json"

    def fail_serialization(data: Any, fh: TextIO, **kwargs: Any) -> None:
        fh.write("{")
        raise TypeError("simulated serialization failure")

    monkeypatch.setattr(persistence.json, "dump", fail_serialization)

    with pytest.raises(TypeError, match="simulated serialization failure"):
        persistence.atomic_write_json(path, {"unsupported": object()})
    assert list(tmp_path.iterdir()) == []
