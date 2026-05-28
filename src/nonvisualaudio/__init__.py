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
2. ``importlib.metadata.version("nonvisualaudio")`` — for an installed
   wheel or a PyInstaller bundle that ships the package dist-info.
3. ``"0.0.0+unknown"`` — fallback so the About dialog never crashes.
"""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path


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


def _read_version() -> str:
    # In a source checkout pyproject.toml is authoritative and never
    # lags behind a version bump the way the editable-install metadata
    # does. A frozen bundle has no adjacent pyproject.toml, so it falls
    # through to the shipped dist metadata.
    if not getattr(sys, "frozen", False):
        from_pyproject = _version_from_pyproject()
        if from_pyproject is not None:
            return from_pyproject
    try:
        return _pkg_version("nonvisualaudio")
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__: str = _read_version()
