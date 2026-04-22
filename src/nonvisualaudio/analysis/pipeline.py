"""High-level orchestration: decode a file, run all analyses, return a result."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from nonvisualaudio.analysis.dynamics import compute_dynamics
from nonvisualaudio.analysis.loudness import measure_loudness
from nonvisualaudio.analysis.result import AnalysisResult, FileInfo
from nonvisualaudio.analysis.spectrum import compute_spectrum
from nonvisualaudio.audio.decoder import decode
from nonvisualaudio.localization import t

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
) -> AnalysisResult:
    """Run the full analysis pipeline on one audio file.

    ``progress_cb(percent, label)`` — if supplied — is called between
    each pipeline step. Percentages are mapped into the range
    ``[percent_start, percent_end]`` so callers that analyze two files
    (target + reference) can split one 0..100 bar across both halves.
    """
    emit = _make_emit(progress_cb, percent_start, percent_end)
    prefix = f"{label_prefix}: " if label_prefix else ""

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
    return AnalysisResult(
        file_info=file_info,
        loudness=loudness,
        dynamics=dynamics,
        spectrum=spectrum,
    )
