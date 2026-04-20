"""A steady metronome-style click while the analyzer is working.

The click samples live in ``_embedded_click.py`` as XOR-scrambled
base64 data — they are unpacked into memory on first use and pushed
straight to PortAudio through ``sounddevice``. Nothing is ever written
to disk, so there is no playable WAV file in the installed app bundle
or in the user's temp directory.

If no audio output is available (headless CI, disabled sound device,
PortAudio not installed) the ticker silently degrades: the timer still
fires so other consumers can hook into it, but no sound is produced.
The analysis itself never depends on the click and must not be blocked
by audio issues.
"""

from __future__ import annotations

import logging

import numpy as np
import wx

from nonvisualaudio.ui._embedded_click import SAMPLE_RATE, load_click_pcm

log = logging.getLogger("nonvisualaudio.ui.click")


class ClickTicker:
    """Plays a short click sound on a repeating timer, fully in memory."""

    def __init__(self, parent: wx.Window, interval_ms: int = 700) -> None:
        self._pcm = np.frombuffer(load_click_pcm(), dtype=np.float32).copy() * 0.35
        self._timer = wx.Timer(parent)
        parent.Bind(wx.EVT_TIMER, self._on_tick, self._timer)
        self._interval_ms = interval_ms
        self._sd = self._try_import_sounddevice()
        self._available = self._sd is not None

    @staticmethod
    def _try_import_sounddevice():
        """Import ``sounddevice`` lazily and absorb any failure.

        PortAudio may be missing on a freshly set up machine. The app
        should still run — the click is a nice-to-have, not a
        requirement.
        """
        try:
            import sounddevice as sd
        except Exception as exc:  # noqa: BLE001 — any import failure means no audio
            log.warning("sounddevice unavailable; click sound disabled (%s)", exc)
            return None
        try:
            # Probe the default output device. On machines with no audio at
            # all this raises, and we disable the click proactively.
            sd.check_output_settings(samplerate=SAMPLE_RATE, channels=1)
        except Exception as exc:  # noqa: BLE001 — headless / broken device
            log.warning("no usable audio output; click sound disabled (%s)", exc)
            return None
        return sd

    def _on_tick(self, event: wx.TimerEvent) -> None:
        self._play_once()

    def _play_once(self) -> None:
        if self._sd is None:
            return
        try:
            self._sd.stop()
            self._sd.play(self._pcm, SAMPLE_RATE, blocking=False)
        except Exception as exc:  # noqa: BLE001 — silently disable on first failure
            log.warning("click playback failed once; disabling (%s)", exc)
            self._sd = None
            self._available = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        self._play_once()
        self._timer.Start(self._interval_ms)

    def stop(self) -> None:
        if self._timer.IsRunning():
            self._timer.Stop()
        if self._sd is not None:
            try:
                self._sd.stop()
            except Exception:  # noqa: BLE001 — stopping silence is fine either way
                pass
