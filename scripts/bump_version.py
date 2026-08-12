#!/usr/bin/env python3
"""Keep the project and packaged macOS readme versions in sync.

The About dialog and the PyInstaller spec read from ``pyproject.toml``
(via ``nonvisualaudio.__version__`` or directly). The macOS archive also
ships ``packaging/macos/LIESMICH.txt``, whose heading must carry the same
version. This script updates both surfaces together.

Usage::

    python scripts/bump_version.py 2.1.0
    python scripts/bump_version.py v2.1.0   # leading v is stripped

The script also runs ``pip install -e .`` in the active environment
(unless ``--no-reinstall`` is passed) so ``importlib.metadata`` picks
up the new version in the running dev checkout — that is what the
About dialog reads at runtime.

The CI workflow (``.github/workflows/build.yml``) calls this same
logic when a ``v*`` git tag is pushed, keeping the GitHub release tag
and the shipped binary's version bit-for-bit identical.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

# Loose but strict enough: major.minor.patch with optional pre/build suffix.
# PEP 440 compliance is handled implicitly by the fact that pyproject.toml
# is consumed by pip, which will reject anything it cannot parse.
_VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,2}(?:[.\-+][A-Za-z0-9.\-+]+)?$")

# A pre-release marker appended verbatim to the current version, e.g.
# ``rc1`` → ``2.1.0rc1``. Kept to alphanumerics + dots so it cannot smuggle
# in shell metacharacters or whitespace; pip's PEP 440 parser is the final
# arbiter of whether the composed version is actually installable.
_MARKER_RE = re.compile(r"^[A-Za-z0-9.]+$")

_PYPROJECT_VERSION_LINE = re.compile(
    r'^(?P<prefix>\s*version\s*=\s*")(?P<version>[^"]*)(?P<suffix>".*)$',
    re.MULTILINE,
)
_LIESMICH_VERSION_LINE = re.compile(r"^NonvisualAudio\s+\S+\s*$")

_ROOT = Path(__file__).resolve().parent.parent


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "version",
        nargs="?",
        help=(
            "New absolute version string, for example 2.1.0. A leading 'v' "
            "is stripped. Omit when using --marker."
        ),
    )
    parser.add_argument(
        "--marker",
        help=(
            "Append a pre-release marker (e.g. rc1, beta2) to the CURRENT "
            "pyproject version instead of setting an absolute version. Lets a "
            "tester build report e.g. 2.1.0rc1 while the branch keeps the "
            "plain 2.1.0 — the marker is never committed."
        ),
    )
    parser.add_argument(
        "--no-reinstall",
        action="store_true",
        help="Skip the 'pip install -e .' step (useful in CI).",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=_ROOT / "pyproject.toml",
        help="Path to pyproject.toml (defaults to repo root).",
    )
    parser.add_argument(
        "--liesmich",
        type=Path,
        default=_ROOT / "packaging" / "macos" / "LIESMICH.txt",
        help="Path to the packaged macOS readme (defaults to repo packaging).",
    )
    return parser.parse_args()


def _normalise(version: str) -> str:
    cleaned = version.strip()
    if cleaned.startswith(("v", "V")):
        cleaned = cleaned[1:]
    if not _VERSION_RE.match(cleaned):
        sys.exit(
            f"bump_version: {cleaned!r} is not a plausible version string "
            "(expected e.g. 2.1.0 or 2.1.0-rc.1)."
        )
    return cleaned


def _path_for_message(path: Path) -> str:
    """Render an untrusted CLI path without terminal control characters."""
    return ascii(os.fspath(path))


def _current_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = _PYPROJECT_VERSION_LINE.search(text)
    if not match:
        sys.exit(
            "bump_version: could not find a 'version = \"…\"' line in "
            f"{_path_for_message(path)}."
        )
    return match.group("version")


def _compose_marker_version(path: Path, raw_marker: str) -> str:
    marker = raw_marker.strip().lstrip(".")
    if not _MARKER_RE.match(marker):
        sys.exit(
            f"bump_version: marker {raw_marker!r} must be alphanumeric "
            "(e.g. rc1, beta2)."
        )
    return f"{_current_version(path)}{marker}"


def _updated_pyproject(path: Path, new_version: str) -> tuple[str, str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        original = handle.read()
    match = _PYPROJECT_VERSION_LINE.search(original)
    if not match:
        sys.exit(
            "bump_version: could not find a 'version = \"…\"' line in "
            f"{_path_for_message(path)}."
        )
    old_version = match.group("version")
    updated = (
        original[: match.start()]
        + match.group("prefix")
        + new_version
        + match.group("suffix")
        + original[match.end():]
    )
    return original, updated


def _updated_liesmich(path: Path, new_version: str) -> tuple[str, str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        original = handle.read()
    first_line, separator, remainder = original.partition("\n")
    if separator and first_line.endswith("\r"):
        first_line = first_line[:-1]
        separator = "\r\n"
    if not _LIESMICH_VERSION_LINE.fullmatch(first_line):
        sys.exit(
            "bump_version: could not find the expected "
            "'NonvisualAudio <version>' heading in "
            f"{_path_for_message(path)}."
        )
    updated = f"NonvisualAudio {new_version}{separator}{remainder}"
    return original, updated


def _atomic_write_text(path: Path, text: str) -> None:
    """Replace one existing UTF-8 text file without exposing a partial write."""
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _rewrite_version_files(
    pyproject_path: Path, liesmich_path: Path, new_version: str
) -> None:
    pyproject_original, pyproject_updated = _updated_pyproject(
        pyproject_path, new_version
    )
    liesmich_original, liesmich_updated = _updated_liesmich(
        liesmich_path, new_version
    )

    pyproject_changed = pyproject_updated != pyproject_original
    liesmich_changed = liesmich_updated != liesmich_original

    if pyproject_changed:
        _atomic_write_text(pyproject_path, pyproject_updated)
    try:
        if liesmich_changed:
            _atomic_write_text(liesmich_path, liesmich_updated)
    except Exception:
        # The two files cannot be replaced by one filesystem transaction.
        # If the second replace fails, restore the already-replaced first
        # file atomically so the version surfaces do not diverge.
        if pyproject_changed:
            _atomic_write_text(pyproject_path, pyproject_original)
        raise

    if pyproject_changed or liesmich_changed:
        print(f"bump_version: synchronized version {new_version}.")
    else:
        print(f"bump_version: already at {new_version}; nothing to do.")


def _reinstall() -> None:
    """Refresh the editable install so importlib.metadata sees the new version.

    Silent on success, prints any pip failure for the user to read.
    """
    cmd = [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(
            f"bump_version: pip install failed (rc={proc.returncode})",
            file=sys.stderr,
        )
        if proc.stdout:
            print(proc.stdout, file=sys.stderr)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
        sys.exit(proc.returncode)
    print("bump_version: refreshed editable install")


def main() -> None:
    args = _parse_args()
    if args.marker is not None:
        if args.version is not None:
            sys.exit("bump_version: pass either a version or --marker, not both.")
        new_version = _compose_marker_version(args.pyproject, args.marker)
    elif args.version is not None:
        new_version = _normalise(args.version)
    else:
        sys.exit("bump_version: provide a version argument or --marker.")
    _rewrite_version_files(args.pyproject, args.liesmich, new_version)
    if not args.no_reinstall:
        _reinstall()
    print(f"bump_version: done, pyproject.toml now reports {new_version}.")


if __name__ == "__main__":
    main()
