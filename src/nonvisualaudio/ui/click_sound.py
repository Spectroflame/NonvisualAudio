"""A steady metronome-style click while the analyzer is working.

The click samples live in ``_embedded_click.py`` as XOR-scrambled
base64 data — they are unpacked into memory on first use and pushed
to a ``QAudioSink`` through a ``QBuffer``. Nothing is ever written to
disk, so there is no playable WAV file in the installed app bundle
or in the user's temp directory.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QTimer
from PySide6.QtMultimedia import QAudioFormat, QAudioSink

from nonvisualaudio.ui._embedded_click import (
    SAMPLE_RATE,
    load_click_pcm,
)

log = logging.getLogger("nonvisualaudio.ui.click")


def _audio_format() -> QAudioFormat:
    fmt = QAudioFormat()
    fmt.setSampleRate(SAMPLE_RATE)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    return fmt


class ClickTicker(QObject):
    """Plays a short click sound on a repeating timer, fully in memory."""

    def __init__(self, parent: QObject | None = None, interval_ms: int = 700) -> None:
        super().__init__(parent)
        self._pcm = QByteArray(load_click_pcm())
        self._sink = QAudioSink(_audio_format(), self)
        self._sink.setVolume(0.35)
        # A fresh QBuffer is opened for every tick — QAudioSink consumes
        # the device sequentially and we simply give it a new view on the
        # same bytes each time.
        self._buffer: QBuffer | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._play_once)

    def _play_once(self) -> None:
        # Stop any in-flight playback first so we don't stack clicks.
        self._sink.stop()
        self._buffer = QBuffer(self)
        self._buffer.setData(self._pcm)
        self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._sink.start(self._buffer)

    def start(self) -> None:
        self._play_once()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._sink.stop()
        if self._buffer is not None:
            self._buffer.close()
            self._buffer = None
