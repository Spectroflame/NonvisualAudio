"""Project-mode analysis: treat a folder of tracks as a single piece.

For an album or audio drama that ships as one file per chapter or song,
the user wants two things:

1. The combined loudness, dynamics, and tonal balance of the whole
   thing as if it had been bounced as one continuous file.
2. Cross-track consistency information — which tracks deviate from the
   project's overall character.

This module produces both. Per-file analysis runs through the same
streaming pass the single-file pipeline uses, and a "combined"
:class:`AnalysisResult` is synthesised by one extra ffmpeg pass over the
*concatenation* of all inputs: ffmpeg's ``concat`` filter joins the
tracks into one logical stream, ``asplit`` fans it out into the
``ebur128`` filter (EBU R128 integrated loudness, true peak and LRA,
exactly as if the user had bounced the project to one file) and into an
f32le PCM pipe that feeds the Phase 1 streamers chunk by chunk. Peak RAM
for the combined pass is one pipe chunk plus the streamers' accumulators
— the 2.1 implementation concatenated every decoded track in numpy and
kept all of them alive at once, which scaled linearly with project
length.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from nonvisualaudio.analysis import memory
from nonvisualaudio.analysis.dynamics import DynamicsStreamer
from nonvisualaudio.analysis.loudness import _parse as _parse_ebur128_summary
from nonvisualaudio.analysis.memory import (
    ConfirmMemoryCb,
    RamCheckCancelled,
)
from nonvisualaudio.analysis.pipeline import analyze_streaming
from nonvisualaudio.analysis.result import (
    AnalysisResult,
    DynamicsMetrics,
    FileInfo,
    LoudnessMetrics,
    SpectrumMetrics,
    StereoMetrics,
)
from nonvisualaudio.analysis.spectrum import SpectrumStreamer
from nonvisualaudio.analysis.stereo import StereoStreamer
from nonvisualaudio.audio.decoder import PcmChunkRouter, StreamingSinks
from nonvisualaudio.audio.ffmpeg_runner import (
    FFmpegError,
    find_ffmpeg,
    run_split_streams_streaming,
)
from nonvisualaudio.cancellation import Cancellation
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
# Combined streaming pass
# --------------------------------------------------------------------------- #


def _measure_combined_streaming(
    paths: list[Path],
    project_label: str,
    target_rate: int,
    decode_channels: int,
    total_duration: float,
    on_progress: Callable[[int], None] | None = None,
    cancel: Cancellation | None = None,
) -> tuple[LoudnessMetrics, DynamicsMetrics, SpectrumMetrics, StereoMetrics]:
    """Measure the whole project in one ffmpeg pass over the concatenation.

    The filter graph joins all inputs with ``concat`` and splits the
    result: one branch runs ``ebur128`` (the same graph the previous
    loudness-only concat pass used, so the R128 numbers are unchanged),
    the other leaves ffmpeg as f32le PCM on stdout — resampled to
    ``target_rate`` and mixed down to ``decode_channels`` channels by
    the output options — and is routed chunk-wise into the Phase 1
    streamers. ``decode_channels`` must be 2 only when *every* track is
    stereo; a mixed mono/stereo project has no meaningful combined
    stereo image, and passing 1 keeps the stereo streamer unfed so it
    finalizes to the is_stereo=False sentinel.

    ``on_progress`` — if given — receives 0..100 derived from the
    ebur128 ``t:`` markers against ``total_duration``.
    """
    if not paths:
        raise ValueError("at least one path required")

    dynamics_streamer = DynamicsStreamer(target_rate)
    spectrum_streamer = SpectrumStreamer(target_rate)
    stereo_streamer = StereoStreamer(target_rate)

    def _feed_mono(chunk) -> None:  # noqa: ANN001 — np.ndarray, hot path
        dynamics_streamer.feed(chunk)
        spectrum_streamer.feed(chunk)

    sinks = StreamingSinks(
        feed_mono=_feed_mono,
        feed_stereo_optional=(
            stereo_streamer.feed if decode_channels == 2 else None
        ),
    )

    n = len(paths)
    inputs: list[str] = []
    for p in paths:
        inputs.extend(["-i", str(p)])
    chain_inputs = "".join(f"[{i}:a]" for i in range(n))
    filter_graph = (
        f"{chain_inputs}concat=n={n}:v=0:a=1,asplit=2[pcm][ana];"
        "[ana]ebur128=peak=true:metadata=1:framelog=info[loud]"
    )
    args = [
        find_ffmpeg(),
        "-hide_banner",
        "-nostats",
        "-nostdin",
        *inputs,
        "-filter_complex",
        filter_graph,
        "-map", "[pcm]",
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        "-ac", str(decode_channels),
        "-ar", str(target_rate),
        "pipe:1",
        "-map", "[loud]",
        "-f", "null",
        "-",
    ]

    # Live progress from the ebur128 ``t:`` markers, fired on the stderr
    # reader thread — keep the callback cheap.
    progress_state = {"last_pct": -1}

    def _on_line(raw_line: bytes) -> None:
        if on_progress is None or total_duration <= 0:
            return
        text = raw_line.decode("utf-8", errors="replace")
        from nonvisualaudio.analysis.loudness import _RE_FRAME_T

        m = _RE_FRAME_T.search(text)
        if m is None:
            return
        try:
            t_sec = float(m.group(1))
        except ValueError:
            return
        pct = int(100.0 * min(1.0, max(0.0, t_sec / total_duration)))
        if pct == progress_state["last_pct"]:
            return
        progress_state["last_pct"] = pct
        on_progress(pct)

    # The combined scan is roughly the sum of the individual scans;
    # 30 minutes of audio at realtime ≈ 30s of ffmpeg work, so the
    # 1200-second budget covers very long projects with margin.
    try:
        stderr_text = run_split_streams_streaming(
            args,
            timeout=1200.0,
            stdout_chunk_handler=PcmChunkRouter(sinks, decode_channels),
            stderr_line_callback=_on_line,
            cancel=cancel,
        )
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

    loudness = _parse_ebur128_summary(stderr_text, project_label)
    return (
        loudness,
        dynamics_streamer.finalize(),
        spectrum_streamer.finalize(),
        stereo_streamer.finalize(),
    )


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
    cancel: Cancellation | None = None,
) -> ProjectResult:
    """Run a project-mode analysis over ``paths``.

    Each file goes through the streaming single-file pass (decode,
    loudness, dynamics, spectrum, stereo — one read, chunk-fed). The
    combined measurement is one additional ffmpeg pass over the
    concatenation of all files; no decoded track is ever held in RAM.

    ``confirm_memory_cb`` — if supplied — is consulted before any file
    is decoded; a ``False`` answer aborts the run with
    :class:`RamCheckCancelled`.
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
    # Per-file pass takes 0..70 % of the project span; combined work
    # gets the remaining 70..100 %.
    per_file_span = 70.0 / n
    for i, raw in enumerate(paths):
        # Cancel between files: a cancel during track 3 of 20 stops here
        # before any further decode, so no partial project is assembled.
        if cancel is not None:
            cancel.raise_if_cancelled()
        slice_start = i * per_file_span
        prefix = t("project.file_label", index=i + 1, total=n)
        _scaled(int(slice_start), f"{prefix}: {t('pipeline.decoding')}")

        def _on_decode_progress(inner_pct: int, stage_key: str, *, _start=slice_start, _prefix=prefix) -> None:
            # Same split the single-file pipeline uses: the soundfile
            # path reports two sequential 0..100 streams ("decoding",
            # then "loudness") that share this file's slice half/half;
            # the combined ffmpeg pass reports one stream that covers
            # the whole slice. Keeps the bar monotonic.
            clamped = max(0, min(100, inner_pct))
            if stage_key == "loudness":
                frac = 0.5 + clamped / 200.0
                stage_label = t("pipeline.loudness")
            elif stage_key == "decoding":
                frac = clamped / 200.0
                stage_label = t("pipeline.decoding")
            else:  # "combined"
                frac = clamped / 100.0
                stage_label = t("pipeline.decoding")
            _scaled(int(_start + per_file_span * frac), f"{_prefix}: {stage_label}")

        result = analyze_streaming(
            raw, on_decode_progress=_on_decode_progress, cancel=cancel
        )
        file_results.append(result)
        log.info(
            "project track %d/%d analyzed: %s I=%.1f LUFS",
            i + 1,
            n,
            result.file_info.filename,
            result.loudness.integrated_lufs,
        )

    if cancel is not None:
        cancel.raise_if_cancelled()

    total_duration = sum(fr.file_info.duration_seconds for fr in file_results)
    # The common rate is the highest sample rate among the inputs, so we
    # don't accidentally band-limit a 96 kHz track down to 44.1 kHz when
    # one of the inputs happens to be at 44.1.
    target_rate = max(fr.file_info.sample_rate for fr in file_results)
    channel_counts = {fr.file_info.channels for fr in file_results}
    project_channels = (
        next(iter(channel_counts)) if len(channel_counts) == 1 else 0
    )
    combined_file_info = FileInfo(
        filename=label,
        duration_seconds=total_duration,
        sample_rate=target_rate,
        channels=project_channels,
        bit_depth=None,
    )

    if n == 1:
        # One track: the "combined" project is the track itself. The
        # per-file pass already measured everything, so a second ffmpeg
        # pass would reproduce the same numbers from the same samples.
        combined = replace(file_results[0], file_info=combined_file_info)
    else:
        _scaled(72, t("project.combined_pass"))

        def _on_combined_progress(pct: int) -> None:
            _scaled(72 + int(pct * 0.26), t("project.combined_pass"))

        combined_loudness, combined_dynamics, combined_spectrum, combined_stereo = (
            _measure_combined_streaming(
                [Path(p) for p in paths],
                label,
                target_rate=target_rate,
                # A combined stereo image only exists when every track
                # is stereo; otherwise decode mono and let the stereo
                # streamer finalize to its is_stereo=False sentinel.
                decode_channels=2 if channel_counts == {2} else 1,
                total_duration=total_duration,
                on_progress=_on_combined_progress,
                cancel=cancel,
            )
        )
        combined = AnalysisResult(
            file_info=combined_file_info,
            loudness=combined_loudness,
            dynamics=combined_dynamics,
            spectrum=combined_spectrum,
            stereo=combined_stereo,
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
            combined = replace(
                combined,
                loudness=replace(
                    combined.loudness,
                    true_peak_dbtp=loudest.loudness.true_peak_dbtp,
                    true_peak_time_seconds=loudest.loudness.true_peak_time_seconds,
                    true_peak_track_filename=loudest.file_info.filename,
                ),
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
        combined.loudness.integrated_lufs,
        memory.format_seconds(elapsed),
        memory.format_bytes(rss_delta) if rss_delta is not None else "?",
        memory.format_bytes(rss_end),
    )
    return ProjectResult(
        project_name=label,
        files=tuple(file_results),
        combined=combined,
    )
