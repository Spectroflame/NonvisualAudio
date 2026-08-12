"""Release-packaging guards for the 2.1.1 hotfix.

These are fast, PyInstaller-free checks that lock in the fixes made after
the 2.1.0 package shipped with a broken macOS first-launch helper and a
README that named the wrong download artifacts. They read the source
tree directly so a regression (renamed helper, stale README, loosened CI
permissions, version drift) fails locally long before a release build.

They are intentionally tolerant of small formatting changes: assertions
match on stable substrings and structural facts, not exact whitespace.
The workflow is parsed with a tiny indentation-aware splitter rather
than PyYAML so the suite needs no dependency beyond the dev extras.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HELPER_NAME = "NonvisualAudio starten (Erststart).command"
MACOS_ARTIFACT = "NonvisualAudio-macOS.zip"
OLD_MACOS_ARTIFACTS = (
    "NonvisualAudio-macOS-arm64.zip",
    "NonvisualAudio-macOS-x86_64.zip",
)
WORKFLOW = ".github/workflows/build.yml"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# macOS first-launch helper
# --------------------------------------------------------------------------- #


def test_macos_helper_exists_with_exact_name() -> None:
    helper = ROOT / "packaging" / "macos" / HELPER_NAME
    assert helper.is_file(), f"missing first-launch helper: {helper}"


def test_no_split_helper_artifacts() -> None:
    # Guard against the broken-up filename that gave 2.1.0 its bug: a
    # shell-word-split helper name left as separate files in the repo.
    macos_dir = ROOT / "packaging" / "macos"
    for stray in ("NonvisualAudio", "starten", "(Erststart).command"):
        assert not (macos_dir / stray).exists(), f"stray helper artifact: {stray}"


def test_liesmich_names_helper_and_version() -> None:
    liesmich = _read("packaging/macos/LIESMICH.txt")
    assert HELPER_NAME in liesmich
    assert "2.1.1" in liesmich
    assert "2.1.0" not in liesmich


# --------------------------------------------------------------------------- #
# README download artifact names
# --------------------------------------------------------------------------- #


def test_readme_uses_current_macos_artifact_name() -> None:
    readme = _read("README.md")
    assert MACOS_ARTIFACT in readme
    assert "NonvisualAudio-Windows-x64.zip" in readme
    assert "NonvisualAudio-Linux-x64.tar.gz" in readme


def test_readme_drops_old_macos_artifact_names() -> None:
    readme = _read("README.md")
    for old in OLD_MACOS_ARTIFACTS:
        assert old not in readme, f"stale macOS artifact name in README: {old}"


# --------------------------------------------------------------------------- #
# Build workflow — content checks
# --------------------------------------------------------------------------- #


def test_workflow_references_correct_helper_name() -> None:
    assert HELPER_NAME in _read(WORKFLOW)


def test_workflow_makes_helper_executable() -> None:
    # The helper must be chmod +x'd before zipping so a Finder
    # double-click actually runs it.
    assert f'chmod +x "$STAGE/{HELPER_NAME}"' in _read(WORKFLOW)


def test_workflow_verifies_macos_zip() -> None:
    # The post-package verify step must assert all three entries exist.
    text = _read(WORKFLOW)
    assert f"NonvisualAudio/{HELPER_NAME}" in text
    assert "NonvisualAudio/NonvisualAudio.app/" in text
    assert "NonvisualAudio/LIESMICH.txt" in text


def test_platform_jobs_run_tests_before_pyinstaller() -> None:
    _, jobs = _split_global_and_jobs(_read(WORKFLOW))
    for name in ("macos", "windows", "linux"):
        block = _job_block(jobs, name)
        assert "-m pytest tests -q" in block, (
            f"job '{name}' does not run the complete test suite"
        )
        assert block.index("-m pytest tests -q") < block.index(
            "pyinstaller --clean"
        ), f"job '{name}' runs tests only after PyInstaller"


def test_linux_gui_tests_run_under_virtual_display() -> None:
    _, jobs = _split_global_and_jobs(_read(WORKFLOW))
    linux = _job_block(jobs, "linux")
    assert "xvfb" in linux
    assert "xvfb-run --auto-servernum python -m pytest tests -q" in linux


# --------------------------------------------------------------------------- #
# Build workflow — permission scoping
# --------------------------------------------------------------------------- #


def _split_global_and_jobs(text: str) -> tuple[str, str]:
    """Return (header-before-jobs, jobs-section) of the workflow file."""
    match = re.search(r"^jobs:\s*$", text, re.MULTILINE)
    assert match, "workflow has no top-level 'jobs:' key"
    return text[: match.start()], text[match.start() :]


def _job_block(jobs_section: str, name: str) -> str:
    """Slice out one job's text block by its 2-space-indented header."""
    pattern = rf"^  {re.escape(name)}:\s*$"
    start = re.search(pattern, jobs_section, re.MULTILINE)
    assert start, f"job '{name}' not found"
    # The block runs until the next 2-space-indented job header.
    rest = jobs_section[start.end() :]
    nxt = re.search(r"^  \w[\w-]*:\s*$", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def test_global_permissions_are_read_only() -> None:
    header, _ = _split_global_and_jobs(_read(WORKFLOW))
    assert re.search(r"^permissions:\s*$", header, re.MULTILINE), (
        "no top-level permissions block"
    )
    assert re.search(r"^\s+contents:\s*read\s*$", header, re.MULTILINE)
    assert not re.search(r"^\s+contents:\s*write\s*$", header, re.MULTILINE), (
        "top-level permissions must not grant write"
    )


def test_release_job_has_write_permission() -> None:
    _, jobs = _split_global_and_jobs(_read(WORKFLOW))
    release = _job_block(jobs, "release")
    assert re.search(r"^\s+permissions:\s*$", release, re.MULTILINE)
    assert re.search(r"^\s+contents:\s*write\s*$", release, re.MULTILINE)


def test_only_release_job_grants_write() -> None:
    # The single 'contents: write' in the whole file must live inside the
    # release job, so no build job silently keeps write access.
    text = _read(WORKFLOW)
    assert len(re.findall(r"contents:\s*write", text)) == 1
    _, jobs = _split_global_and_jobs(text)
    for name in ("macos", "windows", "linux"):
        assert "contents: write" not in _job_block(jobs, name), (
            f"build job '{name}' unexpectedly grants write access"
        )


# --------------------------------------------------------------------------- #
# Version
# --------------------------------------------------------------------------- #


def test_pyproject_version_is_2_2_0() -> None:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    assert data["project"]["version"] == "2.2.0"
