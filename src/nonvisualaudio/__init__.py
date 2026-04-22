"""NonvisualAudio — accessible audio analyzer.

The version is read from a single source of truth: ``pyproject.toml``.
Bumping the release only requires editing that one file.

Resolution order:
1. ``importlib.metadata.version("nonvisualaudio")`` — works for any
   pip-installed copy (editable or wheel) and for PyInstaller bundles
   that ship the package dist-info.
2. Parse ``pyproject.toml`` from the repo root — covers a dev
   checkout where the package was never installed (for example when
   running the tests directly from the source tree).
3. ``"0.0.0+unknown"`` — fallback so the About dialog never crashes.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path


def _read_version() -> str:
    try:
        return _pkg_version("nonvisualaudio")
    except PackageNotFoundError:
        pass
    # Dev-checkout fallback: walk up from this file until we find the
    # repo's pyproject.toml. ``parents[2]`` is the common case
    # (``src/nonvisualaudio/__init__.py`` → repo root), but we probe a
    # few levels up just in case the layout shifts.
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
                break
        try:
            with pyproject.open("rb") as fh:
                data = tomllib.load(fh)
            project = data.get("project") or {}
            version_value = project.get("version")
            if isinstance(version_value, str) and version_value:
                return version_value
        except (OSError, ValueError):
            continue
    return "0.0.0+unknown"


__version__: str = _read_version()
