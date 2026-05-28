#!/usr/bin/env python3
"""Bump the project version in pyproject.toml.

The About dialog and the PyInstaller spec both read from
``pyproject.toml`` (via ``nonvisualaudio.__version__`` or directly), so
this is the only file that needs to change for a new release.

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
import re
import subprocess
import sys
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
        default=Path(__file__).resolve().parent.parent / "pyproject.toml",
        help="Path to pyproject.toml (defaults to repo root).",
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


def _current_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = _PYPROJECT_VERSION_LINE.search(text)
    if not match:
        sys.exit(
            f"bump_version: could not find a 'version = \"…\"' line in {path}."
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


def _rewrite_pyproject(path: Path, new_version: str) -> str:
    original = path.read_text(encoding="utf-8")
    match = _PYPROJECT_VERSION_LINE.search(original)
    if not match:
        sys.exit(
            f"bump_version: could not find a 'version = \"…\"' line in {path}."
        )
    old_version = match.group("version")
    if old_version == new_version:
        print(f"bump_version: already at {new_version}; nothing to do.")
        return old_version
    updated = (
        original[: match.start()]
        + match.group("prefix")
        + new_version
        + match.group("suffix")
        + original[match.end():]
    )
    path.write_text(updated, encoding="utf-8")
    print(f"bump_version: {path.name} {old_version} -> {new_version}")
    return old_version


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
    _rewrite_pyproject(args.pyproject, new_version)
    if not args.no_reinstall:
        _reinstall()
    print(f"bump_version: done, pyproject.toml now reports {new_version}.")


if __name__ == "__main__":
    main()
