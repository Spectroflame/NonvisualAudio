"""Native About dialog.

Uses ``wx.adv.AboutBox`` so the dialog is a plain platform control —
VoiceOver on macOS, Narrator/NVDA on Windows, and Orca on Linux pick
it up through their respective accessibility bridges without any
custom widget code.
"""

from __future__ import annotations

import logging
from pathlib import Path

import wx
import wx.adv

from nonvisualaudio import __version__
from nonvisualaudio.localization import t

log = logging.getLogger("nonvisualaudio.about")

_WEBSITE = "https://github.com/Spectroflame/NonvisualAudio"
_DEVELOPERS = ("Spectroflame",)

_CREDITS = (
    "ffmpeg — EBU R128 loudness measurement (ebur128 filter)",
    "wxPython — native accessibility bridge on every platform",
    "sounddevice and PortAudio — in-memory click playback",
    "scipy.signal, numpy, soundfile — analysis primitives",
)


def _license_text() -> str:
    """Return the MIT license text.

    Falls back to a short notice if the bundled LICENSE cannot be read
    (e.g. in an unusual PyInstaller layout). The About dialog stays
    useful either way.
    """
    candidate_paths: list[Path] = []
    here = Path(__file__).resolve()
    candidate_paths.append(here.parents[3] / "LICENSE")
    try:
        import sys

        if getattr(sys, "frozen", False):
            candidate_paths.append(Path(sys.executable).parent / "LICENSE")
    except Exception:  # noqa: BLE001
        pass

    for path in candidate_paths:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue

    return t("ui.about.license_fallback")


def show_about(parent: wx.Window | None) -> None:
    info = wx.adv.AboutDialogInfo()
    info.SetName(t("app.name"))
    info.SetVersion(__version__)
    info.SetDescription(
        t("ui.about.description")
        + "\n\n"
        + t("ui.about.credits_heading")
        + "\n- "
        + "\n- ".join(_CREDITS)
    )
    info.SetCopyright(t("ui.about.copyright"))
    info.SetWebSite(_WEBSITE, t("ui.about.website_label"))
    for dev in _DEVELOPERS:
        info.AddDeveloper(dev)
    info.SetLicence(_license_text())

    log.info("about dialog opened")
    wx.adv.AboutBox(info, parent)
