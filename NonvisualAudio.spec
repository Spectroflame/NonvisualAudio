# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for NonvisualAudio (macOS / Windows / Linux).

The spec picks up the platform-specific bundled ffmpeg from
``src/nonvisualaudio/resources/bin/<platform>/`` if present. On
macOS the output is wrapped into an .app bundle; on Windows and
Linux it stays a folder under ``dist/NonvisualAudio``.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


def _project_version() -> str:
    """Read the app version from pyproject.toml — single source of truth."""
    with open("pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    return data["project"]["version"]


_VERSION = _project_version()

block_cipher = None


def _platform_dir() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "win"
    return "linux"


def _ffmpeg_binary_name() -> str:
    return "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"


_platform = _platform_dir()
_ffmpeg_name = _ffmpeg_binary_name()
_ffmpeg_src = Path("src") / "nonvisualaudio" / "resources" / "bin" / _platform / _ffmpeg_name
_ffmpeg_dst = f"nonvisualaudio/resources/bin/{_platform}"

# The bundled ffmpeg is only included if a static binary has actually been
# placed in the resources folder. The CI build downloads it there before
# running PyInstaller; local development normally relies on system ffmpeg.
datas = []
if _ffmpeg_src.is_file():
    datas.append((str(_ffmpeg_src), _ffmpeg_dst))

# Data resources (genre profiles JSON, localisation catalogues, help
# HTML). Bundled verbatim at package root so importlib.resources and
# direct Path lookups find them in the PyInstaller tree the same way
# as in a source checkout.
_resources_dir = Path("src") / "nonvisualaudio" / "resources"
for pattern in ("*.json", "*.html"):
    for file_path in _resources_dir.rglob(pattern):
        rel = file_path.relative_to(_resources_dir.parent.parent)
        dst = str(rel.parent).replace("\\", "/")
        datas.append((str(file_path), dst))

hiddenimports = (
    collect_submodules("scipy.signal")
    + collect_submodules("scipy.special")
    + collect_submodules("soundfile")
    + collect_submodules("sounddevice")
)

a = Analysis(
    ["src/nonvisualaudio/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NonvisualAudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # The app is windowed on macOS and Windows; on Linux the
    # ``console=False`` flag still produces a GUI executable because wx
    # picks the native toolkit on its own.
    console=False,
    disable_windowed_traceback=False,
    # On macOS we ship a single universal2 (fat) bundle that runs
    # natively on Apple Silicon and Intel — the GitHub Actions Intel
    # Mac runner pool has been unreliable for months and a separate
    # x86_64 matrix row was failing to dequeue. PyInstaller can only
    # produce a universal2 bundle when the Python interpreter and
    # every loaded .dylib / .so already carries both slices, which
    # the CI workflow ensures by installing python.org's universal2
    # build and pulling universal2 wheels for numpy / scipy / wxPython
    # / soundfile / sounddevice. Other platforms keep the host arch.
    #
    # PyInstaller refuses the equivalent --target-architecture CLI
    # flag when a .spec file is supplied (it's a makespec-only
    # option), so this is the canonical place to set it.
    target_arch="universal2" if sys.platform == "darwin" else None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NonvisualAudio",
)

# Only wrap the collection into an .app bundle on macOS. On Windows and
# Linux the ``dist/NonvisualAudio`` directory is the distributable.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="NonvisualAudio.app",
        icon=None,
        bundle_identifier="com.nonvisualaudio.app",
        info_plist={
            "CFBundleName": "NonvisualAudio",
            "CFBundleDisplayName": "NonvisualAudio",
            "CFBundleShortVersionString": _VERSION,
            "CFBundleVersion": _VERSION,
            "NSHighResolutionCapable": True,
            # Accessibility: let the app participate in the a11y tree.
            "NSAppleEventsUsageDescription": "NonvisualAudio does not send Apple events; this string is required by macOS when accessibility APIs are used.",
        },
    )
