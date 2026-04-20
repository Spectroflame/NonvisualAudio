# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for NonvisualAudio (macOS .app)."""

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Include the bundled static ffmpeg binary inside the app bundle so the
# .app is self-contained on other Macs.
datas = [
    (
        "src/nonvisualaudio/resources/bin/darwin/ffmpeg",
        "nonvisualaudio/resources/bin/darwin",
    ),
]

hiddenimports = (
    collect_submodules("scipy.signal")
    + collect_submodules("scipy.special")
    + collect_submodules("soundfile")
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
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
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

app = BUNDLE(
    coll,
    name="NonvisualAudio.app",
    icon=None,
    bundle_identifier="com.nonvisualaudio.app",
    info_plist={
        "CFBundleName": "NonvisualAudio",
        "CFBundleDisplayName": "NonvisualAudio",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
        # Accessibility: let the app participate in the a11y tree.
        "NSAppleEventsUsageDescription": "NonvisualAudio does not send Apple events; this string is required by macOS when accessibility APIs are used.",
    },
)
