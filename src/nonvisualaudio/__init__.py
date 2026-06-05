"""NonvisualAudio — accessible audio analyzer.

The version is read from a single source of truth: ``pyproject.toml``.
Bumping the release only requires editing that one file.

Resolution order:
1. Parse ``pyproject.toml`` from the repo root — the single source of
   truth in a source checkout, and always current. Preferred over the
   installed metadata because that only refreshes on ``pip install -e
   .`` and would otherwise keep reporting the pre-bump version (so the
   App / About dialog launched via the dev ``.command`` script would
   lag behind a bump). Skipped in a frozen PyInstaller bundle, which
   ships no adjacent ``pyproject.toml``.
2. In a frozen PyInstaller bundle: the ``_version.txt`` the spec writes
   at build time straight from ``pyproject.toml`` — the very same value
   it stamps into the macOS ``Info.plist``. Reading this file (rather
   than the bundled dist-info) keeps the logged / About / diagnostic
   version locked to the build's real version: the dist-info only
   refreshes on a (re)install and could otherwise lag behind a bump,
   making a diagnostic report claim an old version for a new build.
3. ``importlib.metadata.version("nonvisualaudio")`` — for an installed
   wheel, or as a fallback in a bundle missing ``_version.txt``.
4. ``"0.0.0+unknown"`` — fallback so the About dialog never crashes.
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

# A plausible version string: a leading digit followed by version-ish
# characters only (covers 2.1.0, 2.1.0rc1, 2.1.0-rc.1, 0.0.0+unknown).
# Deliberately lenient, but enough to reject a corrupted/tampered
# ``_version.txt`` — empty, multi-line, binary, or oversized junk — so
# such a file falls back to dist-info/pyproject instead of surfacing
# garbage as "the version". This is a robustness check, NOT a security
# control: the file is plain build metadata an attacker with write
# access to the bundle could trivially replace with another valid
# version. Nothing trusts the value beyond display/diagnostics.
_PLAUSIBLE_VERSION = re.compile(r"^\d[A-Za-z0-9.\-+]{0,63}$")


def _version_from_pyproject() -> str | None:
    # Walk up from this file until we find the repo's pyproject.toml.
    # ``parents[2]`` is the common case (``src/nonvisualaudio/__init__.py``
    # → repo root); a couple of neighbours are probed in case the layout
    # shifts.
    here = Path(__file__).resolve()
    for candidate in (here.parents[2], here.parents[1], here.parents[3]):
        pyproject = candidate / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            import tomllib  # Python 3.11+
        except ModuleNotFoundError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ModuleNotFoundError:
                return None
        try:
            with pyproject.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, ValueError):
            continue
        project = data.get("project") or {}
        version_value = project.get("version")
        if isinstance(version_value, str) and version_value:
            return version_value
    return None


def _version_from_bundle() -> str | None:
    # In a frozen PyInstaller build the spec writes the pyproject version
    # to ``nonvisualaudio/_version.txt`` under the unpacked bundle root
    # (``sys._MEIPASS``) — i.e. inside the Python package path, an internal
    # build artifact, never a user-facing file in the app root or in any
    # user-data / export / diagnostic directory, and never user config.
    # It is the same value stamped into Info.plist, so reading it ties the
    # runtime version to the build's real version without depending on the
    # (possibly stale) bundled dist-info. A missing or corrupted file
    # returns None so the caller falls back to dist-info/pyproject.
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        return None
    version_file = Path(base) / "nonvisualaudio" / "_version.txt"
    try:
        # Bounded read: build metadata, never user input, but a corrupted
        # or tampered bundle must not pull an arbitrary blob into memory.
        # 256 bytes dwarfs any real version string.
        with version_file.open("rb") as fh:
            raw = fh.read(256)
    except OSError:
        return None
    text = raw.decode("utf-8", "replace").strip()
    if not _PLAUSIBLE_VERSION.match(text):
        return None
    return text


def _read_version() -> str:
    # In a source checkout pyproject.toml is authoritative and never
    # lags behind a version bump the way the editable-install metadata
    # does. A frozen bundle has no adjacent pyproject.toml, so it reads
    # the version file the spec stamps from pyproject, falling back to
    # the shipped dist metadata only if that file is missing.
    if not getattr(sys, "frozen", False):
        from_pyproject = _version_from_pyproject()
        if from_pyproject is not None:
            return from_pyproject
    else:
        from_bundle = _version_from_bundle()
        if from_bundle is not None:
            return from_bundle
    try:
        return _pkg_version("nonvisualaudio")
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__: str = _read_version()
