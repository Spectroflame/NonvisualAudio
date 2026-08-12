"""Regression tests for release and tester-build version synchronization."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bump_version.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bump_version_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_files(tmp_path: Path) -> tuple[Path, Path]:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "test-project"\nversion = "2.2.0"\n',
        encoding="utf-8",
    )
    liesmich = tmp_path / "LIESMICH.txt"
    liesmich.write_text(
        "NonvisualAudio 2.2.0\n\nInstallationshinweise.\n",
        encoding="utf-8",
    )
    return pyproject, liesmich


def _run(
    pyproject: Path, liesmich: Path, *version_args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            *version_args,
            "--no-reinstall",
            "--pyproject",
            str(pyproject),
            "--liesmich",
            str(liesmich),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _version(pyproject: Path) -> str:
    with pyproject.open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def test_absolute_version_updates_both_files(tmp_path: Path) -> None:
    pyproject, liesmich = _fixture_files(tmp_path)

    result = _run(pyproject, liesmich, "v2.2.1")

    assert result.returncode == 0, result.stderr
    assert _version(pyproject) == "2.2.1"
    assert liesmich.read_text(encoding="utf-8").splitlines()[0] == (
        "NonvisualAudio 2.2.1"
    )


def test_marker_version_updates_both_files(tmp_path: Path) -> None:
    pyproject, liesmich = _fixture_files(tmp_path)

    result = _run(pyproject, liesmich, "--marker", "rc1")

    assert result.returncode == 0, result.stderr
    assert _version(pyproject) == "2.2.0rc1"
    assert liesmich.read_text(encoding="utf-8").splitlines()[0] == (
        "NonvisualAudio 2.2.0rc1"
    )


@pytest.mark.parametrize("marker", ["", "rc 1", "rc1;touch-pwned"])
def test_invalid_marker_leaves_both_files_unchanged(
    tmp_path: Path, marker: str
) -> None:
    pyproject, liesmich = _fixture_files(tmp_path)
    original_pyproject = pyproject.read_bytes()
    original_liesmich = liesmich.read_bytes()

    result = _run(pyproject, liesmich, "--marker", marker)

    assert result.returncode != 0
    assert pyproject.read_bytes() == original_pyproject
    assert liesmich.read_bytes() == original_liesmich


def test_malformed_liesmich_leaves_pyproject_unchanged(tmp_path: Path) -> None:
    pyproject, liesmich = _fixture_files(tmp_path)
    liesmich.write_text("unexpected heading\n", encoding="utf-8")
    original_pyproject = pyproject.read_bytes()

    result = _run(pyproject, liesmich, "2.2.1")

    assert result.returncode != 0
    assert pyproject.read_bytes() == original_pyproject
    assert liesmich.read_text(encoding="utf-8") == "unexpected heading\n"


def test_cli_path_for_error_message_escapes_terminal_controls() -> None:
    module = _load_script()

    rendered = module._path_for_message(Path("line-break\nansi-\x1b[31m"))

    assert "\n" not in rendered
    assert "\x1b" not in rendered
    assert r"\n" in rendered
    assert r"\x1b" in rendered


def test_second_replace_failure_rolls_back_first_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    pyproject, liesmich = _fixture_files(tmp_path)
    original_pyproject = pyproject.read_bytes()
    original_liesmich = liesmich.read_bytes()
    atomic_write = module._atomic_write_text
    calls = 0

    def fail_second_write(path: Path, text: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second replace failure")
        atomic_write(path, text)

    monkeypatch.setattr(module, "_atomic_write_text", fail_second_write)

    with pytest.raises(OSError, match="simulated second replace failure"):
        module._rewrite_version_files(pyproject, liesmich, "2.2.1")

    assert calls == 3  # pyproject update, LIESMICH failure, pyproject rollback
    assert pyproject.read_bytes() == original_pyproject
    assert liesmich.read_bytes() == original_liesmich
