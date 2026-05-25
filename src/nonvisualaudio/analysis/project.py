"""Project-mode analysis: treat a folder of tracks as a single piece.

For an album or audio drama that ships as one file per chapter or song,
the user wants two things:

1. The combined loudness, dynamics, and tonal balance of the whole
   thing as if it had been bounced as one continuous file.
2. Cross-track consistency information — which tracks deviate from the
   project's overall character.

This module produces both. Per-file analysis still runs (we need the
individual numbers for the consistency report), and a "combined"
:class:`AnalysisResult` is synthesised by:

- Running ffmpeg's ``ebur128`` filter over the *concatenation* of all
  inputs, so EBU R128 integrated loudness, true peak, and LRA are
  measured exactly as if the user had bounced the project to one file.
- Resampling each decoded track to a common sample rate, concatenating
  them in numpy, and feeding the result to the existing dynamics and
  spectrum analysers. This is RAM-bounded but stays purely in-process.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from nonvisualaudio.analysis import memory
from nonvisualaudio.analysis.dynamics import compute_dynamics
from nonvisualaudio.analysis.loudness import _parse as _parse_ebur128_summary
from nonvisualaudio.analysis.loudness import measure_loudness
from nonvisualaudio.analysis.memory import (
    ConfirmMemoryCb,
    RamCheckCancelled,
)
from nonvisualaudio.analysis.result import (
    AnalysisResult,
    FileInfo,
    LoudnessMetrics,
)
from nonvisualaudio.analysis.spectrum import compute_spectrum
from nonvisualaudio.analysis.stereo import compute_stereo
from nonvisualaudio.audio.decoder import DecodedAudio, decode, decode_and_measure
from nonvisualaudio.audio.ffmpeg_runner import FFmpegError, find_ffmpeg, run
from nonvisualaudio.errors import LoudnessMeasurementError
from nonvisualaudio.localization import t

log = logging.getLogger("nonvisualaudio.project")

ProgressCb = Callable[[int, str], None]


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProjectResult:
    """Outcome of a project-mode analysis.

    ``files`` holds the per-track results, ``combined`` is the
    synthesised whole-project measurement that the report builder
    presents as if it were one file.
    """

    project_name: str
    files: tuple[AnalysisResult, ...]
    combined: AnalysisResult


# --------------------------------------------------------------------------- #
# Sample-domain helpers
# --------------------------------------------------------------------------- #


def _resample_mono(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Resample a mono float32 buffer to ``dst_rate`` using rational ratios.

    scipy.signal.resample_poly is already a project dependency (used by
    the spectrum analyser). It runs an anti-aliased polyphase filter,
    which avoids the spectral mirroring you would get from a raw FFT
    resampler when the rates differ widely.
    """
    if src_rate == dst_rate or samples.size == 0:
        return samples.astype(np.float32, copy=False)
    g = math.gcd(src_rate, dst_rate)
    up = dst_rate // g
    down = src_rate // g
    # Local import keeps the module import-cheap when project mode is unused.
    from scipy import signal as scipy_signal

    out = scipy_signal.resample_poly(samples.astype(np.float64), up, down)
    return out.astype(np.float32, copy=False)


def _concatenate_decoded(decoded: list[DecodedAudio]) -> tuple[np.ndarray, int]:
    """Resample every track to the project's common rate and concatenate.

    The common rate is the highest sample rate among the inputs, so we
    don't accidentally band-limit a 96 kHz track down to 44.1 kHz when
    one of the inputs happens to be at 44.1.
    """
    if not decoded:
        return np.zeros(0, dtype=np.float32), 0
    target_rate = max(d.sample_rate for d in decoded)
    parts: list[np.ndarray] = []
    for d in decoded:
        parts.append(_resample_mono(d.samples, d.sample_rate, target_rate))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32), target_rate


def _concatenate_decoded_stereo(
    decoded: list[DecodedAudio], target_rate: int
) -> np.ndarray | None:
    """Concatenate two-channel buffers, resampling each side independently.

    Returns ``None`` when not every track carries a stereo buffer — a
    mixed mono/stereo project has no meaningful combined stereo image,
    and the report builder must say so rather than pretend otherwise.
    """
    if not decoded or any(d.stereo_samples is None for d in decoded):
        return None
    parts: list[np.ndarray] = []
    for d in decoded:
        stereo = d.stereo_samples
        assert stereo is not None  # narrowed by the check above
        if d.sample_rate == target_rate:
            parts.append(stereo.astype(np.float32, copy=False))
            continue
        left = _resample_mono(stereo[:, 0], d.sample_rate, target_rate)
        right = _resample_mono(stereo[:, 1], d.sample_rate, target_rate)
        # Resamplers can return arrays whose lengths differ by 1 sample
        # for odd polyphase ratios — clip to the shorter length so the
        # column-stack stays consistent.
        n = min(left.size, right.size)
        parts.append(np.column_stack((left[:n], right[:n])).astype(np.float32, copy=False))
    return np.concatenate(parts, axis=0)


# --------------------------------------------------------------------------- #
# FFmpeg concat-loudness
# --------------------------------------------------------------------------- #


def _measure_loudness_combined(
    paths: list[Path], project_label: str
) -> LoudnessMetrics:
    """Run ebur128 over the concatenation of every input file.

    Uses ffmpeg's ``concat`` filter inside ``-filter_complex`` so the
    different inputs are joined into one logical stream before the
    ebur128 filter sees them. The filter automatically resamples
    inputs to a common rate, so files with mismatched sample rates or
    channel counts still produce a single, exact loudness reading.

    Falls back to the single-file ``measure_loudness`` for one-element
    inputs to keep the simple case cheap.
    """
    if not paths:
        raise ValueError("at least one path required")
    if len(paths) == 1:
        return measure_loudness(paths[0])

    n = len(paths)
    inputs: list[str] = []
    for p in paths:
        inputs.extend(["-i", str(p)])
    chain_inputs = "".join(f"[{i}:a]" for i in range(n))
    filter_graph = (
        f"{chain_inputs}concat=n={n}:v=0:a=1[concat];"
        "[concat]ebur128=peak=true:metadata=1:framelog=info[ana]"
    )
    args = [
        find_ffmpeg(),
        "-hide_banner",
        "-nostats",
        "-nostdin",
        *inputs,
        "-filter_complex",
        filter_graph,
        "-map",
        "[ana]",
        "-f",
        "null",
        "-",
    ]
    # The combined scan is roughly the sum of the individual scans;
    # 30 minutes of audio at realtime ≈ 30s of ffmpeg work, so the
    # 1200-second budget covers very long projects with margin.
    try:
        proc = run(args, timeout=1200.0)
    except FFmpegError as exc:
        raw = str(exc)
        if raw.startswith("timeout:"):
            raise LoudnessMeasurementError(
                title=t("error.loudness.timeout.title", name=project_label),
                body=t("error.loudness.timeout.body"),
                hint=t("error.loudness.timeout.hint"),
            ) from exc
        raise LoudnessMeasurementError(
            title=t("error.loudness.generic.title", name=project_label),
            body=t("error.loudness.generic.body"),
            hint=t("error.loudness.generic.hint"),
        ) from exc
    stderr = proc.stderr.decode("utf-8", errors="replace")
    return _parse_ebur128_summary(stderr, project_label)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def _emit(cb: ProgressCb | None, percent: int, label: str) -> None:
    if cb is not None:
        cb(max(0, min(100, percent)), label)


def analyze_project(
    paths: list[str | Path],
    project_name: str | None = None,
    progress_cb: ProgressCb | None = None,
    percent_start: int = 0,
    percent_end: int = 100,
    confirm_memory_cb: ConfirmMemoryCb | None = None,
) -> ProjectResult:
    """Run a project-mode analysis over ``paths``.

    Each file is decoded once. From the decoded samples we get the
    per-file dynamics and spectrum and the combined buffer for the
    whole-project dynamics/spectrum. Loudness is measured with
    ffmpeg — once per file plus one extra concatenation pass.

    ``confirm_memory_cb`` — if supplied — is consulted before any file
    is decoded. The estimate covers the worst case where every track
    plus the concatenated buffer is held in RAM at the same time; a
    ``False`` answer aborts the run with :class:`RamCheckCancelled`.
    """
    if not paths:
        raise ValueError("project analysis needs at least one file")
    label = project_name or t("project.default_name")

    if confirm_memory_cb is not None:
        estimate = memory.build_estimate(
            label=label,
            estimated_bytes=memory.estimate_project_bytes(paths),
        )
        log.info(
            "ram estimate for project %s (%d files): %s "
            "(available %s, total %s)",
            label,
            len(paths),
            memory.format_bytes(estimate.estimated_bytes),
            memory.format_bytes(estimate.available_bytes),
            memory.format_bytes(estimate.total_bytes),
        )
        if estimate.is_concerning and not confirm_memory_cb(estimate):
            raise RamCheckCancelled()

    span = max(percent_end - percent_start, 1)

    def _scaled(inner_percent: int, stage: str) -> None:
        pct = percent_start + int(span * max(0, min(100, inner_percent)) / 100)
        _emit(progress_cb, pct, stage)

    t_start = time.perf_counter()
    rss_start = memory.peak_rss_bytes()
    n = len(paths)
    file_results: list[AnalysisResult] = []
    decoded_tracks: list[DecodedAudio] = []
    # Per-file pass takes 0..70 % of the project span; combined work
    # gets the remaining 70..100 %.
    per_file_span = 70.0 / n
    for i, raw in enumerate(paths):
        slice_start = i * per_file_span
        prefix = t("project.file_label", index=i + 1, total=n)
        _scaled(int(slice_start), f"{prefix}: {t('pipeline.decoding')}")

        def _on_decode_progress(inner_pct: int, stage_key: str, *, _start=slice_start, _prefix=prefix) -> None:
            label = (
                t("pipeline.loudness") if stage_key == "loudness" else t("pipeline.decoding")
            )
            # Decode + loudness occupy 0..80% of this file's slice.
            _scaled(int(_start + per_file_span * inner_pct * 0.008), f"{_prefix}: {label}")

        decoded, loud = decode_and_measure(raw, on_progress=_on_decode_progress)
        decoded_tracks.append(decoded)
        # Sequential per-track analyses, ordered to match pipeline.analyze.
        # We cannot free decoded.stereo_samples here (the combined stereo
        # pass at the end of the project still needs every track's stereo
        # buffer), but running the three analysers serially still keeps a
        # single float64 temporary live at a time rather than three.
        _scaled(int(slice_start + per_file_span * 0.80), f"{prefix}: {t('pipeline.stereo')}")
        stereo = compute_stereo(decoded.stereo_samples, decoded.sample_rate)
        _scaled(int(slice_start + per_file_span * 0.87), f"{prefix}: {t('pipeline.dynamics')}")
        dyn = compute_dynamics(decoded.samples, decoded.sample_rate)
        _scaled(int(slice_start + per_file_span * 0.94), f"{prefix}: {t('pipeline.spectrum')}")
        spec = compute_spectrum(decoded.samples, decoded.sample_rate)
        file_results.append(
            AnalysisResult(
                file_info=FileInfo(
                    filename=decoded.filename,
                    duration_seconds=decoded.duration_seconds,
                    sample_rate=decoded.sample_rate,
                    channels=decoded.channels,
                    bit_depth=decoded.bit_depth,
                ),
                loudness=loud,
                dynamics=dyn,
                spectrum=spec,
                stereo=stereo,
            )
        )
        log.info(
            "project track %d/%d analyzed: %s I=%.1f LUFS",
            i + 1,
            n,
            decoded.filename,
            loud.integrated_lufs,
        )

    _scaled(72, t("project.combining_loudness"))
    combined_loudness = _measure_loudness_combined(
        [Path(p) for p in paths], label
    )

    # True-peak provenance for project mode. The ffmpeg concat pass
    # reports a project-wide true peak, but its timeline maps to nothing
    # on disk — the value is fine, the timestamp is not actionable. The
    # per-track scans already know exactly where each file's loudest
    # peak sits, and the maximum across all tracks is the project-wide
    # peak (concat does not synthesise new samples). Pulling both the
    # dBTP value and the timestamp from the loudest per-track scan
    # gives the user a "track + position in track" they can jump to.
    if file_results:
        loudest = max(
            file_results, key=lambda fr: fr.loudness.true_peak_dbtp
        )
        if loudest.loudness.true_peak_time_seconds is not None:
            combined_loudness = replace(
                combined_loudness,
                true_peak_dbtp=loudest.loudness.true_peak_dbtp,
                true_peak_time_seconds=loudest.loudness.true_peak_time_seconds,
                true_peak_track_filename=loudest.file_info.filename,
            )

    _scaled(85, t("project.combining_samples"))
    combined_samples, target_rate = _concatenate_decoded(decoded_tracks)

    _scaled(90, t("project.combining_dynamics"))
    combined_dynamics = compute_dynamics(combined_samples, target_rate)

    _scaled(95, t("project.combining_spectrum"))
    combined_spectrum = compute_spectrum(combined_samples, target_rate)

    _scaled(98, t("project.combining_stereo"))
    combined_stereo_samples = _concatenate_decoded_stereo(decoded_tracks, target_rate)
    combined_stereo = compute_stereo(combined_stereo_samples, target_rate)

    total_duration = sum(d.duration_seconds for d in decoded_tracks)
    channel_counts = {d.channels for d in decoded_tracks}
    project_channels = (
        next(iter(channel_counts)) if len(channel_counts) == 1 else 0
    )

    combined = AnalysisResult(
        file_info=FileInfo(
            filename=label,
            duration_seconds=total_duration,
            sample_rate=target_rate,
            channels=project_channels,
            bit_depth=None,
        ),
        loudness=combined_loudness,
        dynamics=combined_dynamics,
        spectrum=combined_spectrum,
        stereo=combined_stereo,
    )

    _scaled(100, t("project.done"))
    elapsed = time.perf_counter() - t_start
    rss_end = memory.peak_rss_bytes()
    rss_delta = (
        rss_end - rss_start
        if rss_start is not None and rss_end is not None
        else None
    )
    log.info(
        "project analyzed: %d files, total %.1fs audio, combined I=%.1f LUFS, "
        "wall time %s, peak RAM Δ %s (peak %s)",
        n,
        total_duration,
        combined_loudness.integrated_lufs,
        memory.format_seconds(elapsed),
        memory.format_bytes(rss_delta) if rss_delta is not None else "?",
        memory.format_bytes(rss_end),
    )
    return ProjectResult(
        project_name=label,
        files=tuple(file_results),
        combined=combined,
    )
