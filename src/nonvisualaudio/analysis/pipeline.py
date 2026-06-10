"""High-level orchestration: stream a file through all analyses, return a result.

Since 2.2 the pipeline is streaming end to end (Phase 3 of the RAM
rewrite): the decoder hands each PCM chunk straight to the Phase 1
streamer classes and releases it. Peak RAM is bounded by one decode
chunk plus the streamers' accumulators — a few MB regardless of input
length — where the 2.1 batch path materialised the whole decoded file
(~660 MB per mono hour at 48 kHz, plus float64 temporaries on top).

The batch functions (``decode_and_measure``, ``compute_dynamics``,
``compute_spectrum``, ``compute_stereo``) remain available; the
equivalence tests pin the streamers' output to theirs.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from nonvisualaudio.analysis import memory
from nonvisualaudio.analysis.dynamics import DynamicsStreamer
from nonvisualaudio.analysis.memory import (
    ConfirmMemoryCb,
    RamCheckCancelled,
)
from nonvisualaudio.analysis.result import AnalysisResult, FileInfo
from nonvisualaudio.analysis.spectrum import SpectrumStreamer
from nonvisualaudio.analysis.stereo import StereoStreamer
from nonvisualaudio.audio.decoder import (
    StreamingSinks,
    decode_and_measure_streaming,
)
from nonvisualaudio.cancellation import Cancellation
from nonvisualaudio.localization import t

log = logging.getLogger("nonvisualaudio.pipeline")

ProgressCb = Callable[[int, str], None]


def _make_emit(
    cb: ProgressCb | None, start: int, end: int
) -> Callable[[int, str], None]:
    """Return a helper that maps a 0..100 inner percentage into [start, end]."""
    span = max(end - start, 1)

    def emit(inner_percent: int, label: str) -> None:
        if cb is None:
            return
        pct = start + int(span * max(0, min(100, inner_percent)) / 100)
        cb(pct, label)

    return emit


class _StreamerSet:
    """The three Phase 1 streamers wired up as decoder sinks.

    Built by the decoder's sinks factory once the stream's sample rate
    is known. ``feed_mono`` fans each mono chunk out to dynamics and
    spectrum; the stereo streamer is attached as the optional stereo
    sink only for genuinely two-channel sources, so for mono input its
    ``finalize`` returns the same is_stereo=False sentinel the batch
    ``compute_stereo(None, sr)`` produced.
    """

    def __init__(self, sample_rate: int, channels: int) -> None:
        self.dynamics = DynamicsStreamer(sample_rate)
        self.spectrum = SpectrumStreamer(sample_rate)
        self.stereo = StereoStreamer(sample_rate)
        self.sinks = StreamingSinks(
            feed_mono=self._feed_mono,
            feed_stereo_optional=(
                self.stereo.feed if channels == 2 else None
            ),
        )

    def _feed_mono(self, chunk) -> None:  # noqa: ANN001 — np.ndarray, hot path
        self.dynamics.feed(chunk)
        self.spectrum.feed(chunk)


def analyze_streaming(
    path: str | Path,
    on_decode_progress: Callable[[int, str], None] | None = None,
    cancel: Cancellation | None = None,
) -> AnalysisResult:
    """Run the streaming analysis pass on one audio file.

    This is the core the public :func:`analyze` and the project mode
    share: decode + loudness + all three analysers in a single pass over
    the file, without RAM-guard gate, progress mapping, or timing logs.

    ``on_decode_progress(percent, stage_key)`` follows the decoder's
    progress contract — ``stage_key`` is ``"decoding"``, ``"loudness"``
    or ``"combined"``. By the time the decode finishes, the analysis is
    finished too (the streamers consumed every chunk inline); the final
    ``finalize`` calls are cheap reductions over the accumulators.
    """
    holder: list[_StreamerSet] = []

    def _make_sinks(sample_rate: int, channels: int) -> StreamingSinks:
        streamer_set = _StreamerSet(sample_rate, channels)
        holder.append(streamer_set)
        return streamer_set.sinks

    info, loudness = decode_and_measure_streaming(
        path,
        on_progress=on_decode_progress,
        sinks_factory=_make_sinks,
        cancel=cancel,
    )
    if cancel is not None:
        cancel.raise_if_cancelled()
    assert holder, "decoder must call the sinks factory before returning"
    streamers = holder[0]

    return AnalysisResult(
        file_info=FileInfo(
            filename=info.filename,
            duration_seconds=info.duration_seconds,
            sample_rate=info.sample_rate,
            channels=info.channels,
            bit_depth=info.bit_depth,
        ),
        loudness=loudness,
        dynamics=streamers.dynamics.finalize(),
        spectrum=streamers.spectrum.finalize(),
        stereo=streamers.stereo.finalize(),
    )


def analyze(
    path: str | Path,
    progress_cb: ProgressCb | None = None,
    percent_start: int = 0,
    percent_end: int = 100,
    label_prefix: str = "",
    confirm_memory_cb: ConfirmMemoryCb | None = None,
    cancel: Cancellation | None = None,
) -> AnalysisResult:
    """Run the full analysis pipeline on one audio file.

    ``progress_cb(percent, label)`` — if supplied — is called as the
    streaming pass advances. Percentages are mapped into the range
    ``[percent_start, percent_end]`` so callers that analyze two files
    (target + reference) can split one 0..100 bar across both halves.

    ``confirm_memory_cb`` — if supplied — is consulted before decoding.
    The RAM guard estimates the run's peak memory footprint and, when
    that crosses the warning threshold, asks the callback whether to
    proceed. Returning ``False`` aborts the run with
    :class:`RamCheckCancelled` so the worker can surface a clean
    cancellation message. With the streaming pipeline the estimate is
    small and duration-independent, so the warning only fires on
    genuinely starved systems.
    """
    emit = _make_emit(progress_cb, percent_start, percent_end)
    prefix = f"{label_prefix}: " if label_prefix else ""
    p = Path(path)

    if confirm_memory_cb is not None:
        estimate = memory.build_estimate(
            label=p.name,
            estimated_bytes=memory.estimate_file_bytes(p),
        )
        log.info(
            "ram estimate for %s: %s (available %s, total %s)",
            p.name,
            memory.format_bytes(estimate.estimated_bytes),
            memory.format_bytes(estimate.available_bytes),
            memory.format_bytes(estimate.total_bytes),
        )
        if estimate.is_concerning and not confirm_memory_cb(estimate):
            raise RamCheckCancelled()

    t_start = time.perf_counter()
    rss_start = memory.peak_rss_bytes()

    emit(0, f"{prefix}{t('pipeline.decoding')}")

    # Decode, loudness and the chunk-fed analysers share one pass. The
    # combined ffmpeg pass — used for MP3, M4A, Opus and the other
    # formats soundfile cannot read — emits a single ``"combined"``
    # progress stream that covers the whole work, so it maps to the
    # full 0..98. The soundfile fast path runs two sequential reads of
    # comparable length — the decode+analysis loop ("decoding"), then
    # the ffmpeg ebur128 scan ("loudness") — each reporting its own
    # 0..100; they get one half of the range apiece so the bar (and the
    # remaining-time estimate on top of it) never moves backwards. The
    # tail 98..100 covers the streamers' finalize reductions, which are
    # near-instant.
    def _on_decode_progress(inner_pct: int, stage_key: str) -> None:
        clamped = max(0, min(100, inner_pct))
        if stage_key == "loudness":
            outer = 49 + int(clamped * 0.49)
            label = t("pipeline.loudness")
        elif stage_key == "decoding":
            outer = int(clamped * 0.49)
            label = t("pipeline.decoding")
        else:  # "combined": one ffmpeg pass is the entire file read
            outer = int(clamped * 0.98)
            label = t("pipeline.decoding")
        emit(outer, f"{prefix}{label}")

    result = analyze_streaming(
        p, on_decode_progress=_on_decode_progress, cancel=cancel
    )

    emit(100, f"{prefix}{t('pipeline.done')}")

    elapsed = time.perf_counter() - t_start
    rss_end = memory.peak_rss_bytes()
    rss_delta = (
        rss_end - rss_start
        if rss_start is not None and rss_end is not None
        else None
    )
    log.info(
        "analyze done: %s in %s, peak RAM Δ %s (peak %s)",
        result.file_info.filename,
        memory.format_seconds(elapsed),
        memory.format_bytes(rss_delta) if rss_delta is not None else "?",
        memory.format_bytes(rss_end),
    )

    return result
