"""The app version comes from exactly one source.

These guard the invariant that every surface that shows a version (log
banner, diagnostic report, About dialog) reads ``nonvisualaudio.__version__``,
and that this value tracks ``pyproject.toml`` — including inside a frozen
PyInstaller bundle, where it must read the spec-stamped ``_version.txt``
rather than the (possibly stale) bundled dist-info.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

import nonvisualaudio
from nonvisualaudio import __version__, diagnostics, logging_setup


def _pyproject_version() -> str:
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_version_matches_pyproject() -> None:
    # Single source of truth: the running version equals pyproject's.
    assert __version__ == _pyproject_version()


def test_log_banner_uses_central_version(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="nonvisualaudio"):
        logging_setup.log_session_banner()
    banner = "\n".join(r.getMessage() for r in caplog.records)
    assert f"NonvisualAudio {__version__} started" in banner


def test_diagnostic_report_uses_central_version() -> None:
    assert f"NonvisualAudio version : {__version__}" in diagnostics.system_info()


def test_frozen_bundle_reads_stamped_version_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Simulate a frozen bundle whose dist-info lags behind the build: the
    # version must come from the spec-stamped _version.txt, not the metadata.
    pkg_dir = tmp_path / "nonvisualaudio"
    pkg_dir.mkdir()
    (pkg_dir / "_version.txt").write_text("9.9.9\n", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert nonvisualaudio._read_version() == "9.9.9"


def test_frozen_bundle_without_version_file_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Missing _version.txt must not crash: fall through to dist metadata
    # (or the "0.0.0+unknown" sentinel), never raise.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert nonvisualaudio._read_version()  # non-empty string, no exception


@pytest.mark.parametrize(
    "payload",
    [
        b"",  # empty
        b"   \n",  # whitespace only
        b"not-a-version",  # leading non-digit
        b"2.1.0\n2.1.0",  # multi-line / injected second line
        b"\x00\x01\x02\xff",  # binary garbage
        b"9" * 500,  # oversized blob
    ],
)
def test_frozen_bundle_corrupt_version_file_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: bytes
) -> None:
    # A corrupted/tampered _version.txt must be rejected (treated like a
    # missing file) so the runtime falls back instead of surfacing junk.
    pkg_dir = tmp_path / "nonvisualaudio"
    pkg_dir.mkdir()
    (pkg_dir / "_version.txt").write_bytes(payload)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert nonvisualaudio._version_from_bundle() is None
    # _read_version must still yield a usable, non-empty string.
    assert nonvisualaudio._read_version()
