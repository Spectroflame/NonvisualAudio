"""High-level orchestration: decode a file, run all analyses, return a result."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from nonvisualaudio.analysis import memory
from nonvisualaudio.analysis.dynamics import compute_dynamics
from nonvisualaudio.analysis.loudness import measure_loudness
from nonvisualaudio.analysis.memory import (
    ConfirmMemoryCb,
    RamCheckCancelled,
)
from nonvisualaudio.analysis.result import AnalysisResult, FileInfo
from nonvisualaudio.analysis.spectrum import compute_spectrum
from nonvisualaudio.audio.decoder import decode
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


def analyze(
    path: str | Path,
    progress_cb: ProgressCb | None = None,
    percent_start: int = 0,
    percent_end: int = 100,
    label_prefix: str = "",
    confirm_memory_cb: ConfirmMemoryCb | None = None,
) -> AnalysisResult:
    """Run the full analysis pipeline on one audio file.

    ``progress_cb(percent, label)`` — if supplied — is called between
    each pipeline step. Percentages are mapped into the range
    ``[percent_start, percent_end]`` so callers that analyze two files
    (target + reference) can split one 0..100 bar across both halves.

    ``confirm_memory_cb`` — if supplied — is consulted before decoding.
    The RAM guard estimates the file's peak memory footprint and, when
    that crosses the warning threshold, asks the callback whether to
    proceed. Returning ``False`` aborts the run with
    :class:`RamCheckCancelled` so the worker can surface a clean
    cancellation message.
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
    decoded = decode(path)
    file_info = FileInfo(
        filename=decoded.filename,
        duration_seconds=decoded.duration_seconds,
        sample_rate=decoded.sample_rate,
        channels=decoded.channels,
        bit_depth=decoded.bit_depth,
    )

    emit(25, f"{prefix}{t('pipeline.loudness')}")
    loudness = measure_loudness(path)

    emit(75, f"{prefix}{t('pipeline.dynamics')}")
    dynamics = compute_dynamics(decoded.samples, decoded.sample_rate)

    emit(85, f"{prefix}{t('pipeline.spectrum')}")
    spectrum = compute_spectrum(decoded.samples, decoded.sample_rate)

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
        decoded.filename,
        memory.format_seconds(elapsed),
        memory.format_bytes(rss_delta) if rss_delta is not None else "?",
        memory.format_bytes(rss_end),
    )

    return AnalysisResult(
        file_info=file_info,
        loudness=loudness,
        dynamics=dynamics,
        spectrum=spectrum,
    )
