"""Tests for the RAM-guard layer.

Covers the byte/duration formatting helpers, the warning-threshold
logic in :class:`MemoryEstimate`, the estimator's reaction when the
file probe fails, and the pipeline's integration with the confirm
callback.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from nonvisualaudio.analysis import memory, pipeline
from nonvisualaudio.analysis.memory import (
    MemoryEstimate,
    RamCheckCancelled,
    estimate_file_bytes,
    estimate_project_bytes,
    format_bytes,
    format_seconds,
)


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #


def test_format_bytes_renders_none_as_question_mark() -> None:
    assert format_bytes(None) == "?"


def test_format_bytes_uses_compact_units() -> None:
    # Below 1024 stays in B with no decimals — kilobyte/MB takes over above.
    assert format_bytes(0) == "0 B"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1500).endswith("kB")
    assert format_bytes(1_500_000).endswith("MB")
    assert format_bytes(1_500_000_000).endswith("GB")


def test_format_seconds_switches_to_minutes_at_60() -> None:
    assert format_seconds(None) == "?"
    assert format_seconds(5.3).endswith(" s")
    assert format_seconds(125.0) == "2 min 05 s"


# --------------------------------------------------------------------------- #
# Threshold logic
# --------------------------------------------------------------------------- #


def test_estimate_concerning_when_both_signals_tight() -> None:
    # 600 MB vs 1 GB available (60 %) AND vs 1.2 GB total (50 %) — both
    # fractions cross their thresholds, so the gate warns.
    est = MemoryEstimate(
        label="x",
        estimated_bytes=600_000_000,
        available_bytes=1_000_000_000,
        total_bytes=1_200_000_000,
    )
    assert est.is_concerning is True


def test_estimate_not_concerning_on_large_system() -> None:
    # Small file on a roomy system: stays below both thresholds.
    est = MemoryEstimate(
        label="x",
        estimated_bytes=100_000_000,
        available_bytes=16_000_000_000,
        total_bytes=32_000_000_000,
    )
    assert est.is_concerning is False


def test_estimate_not_concerning_when_well_within_available() -> None:
    """A multi-gigabyte estimate stays quiet when there is plenty free."""
    # 8 GB estimate against 50 GB free is 16 % — well below the threshold,
    # even though it sits far above the absolute fallback. This is the
    # case the warning gate used to over-flag.
    est = MemoryEstimate(
        label="x",
        estimated_bytes=8_000_000_000,
        available_bytes=50_000_000_000,
        total_bytes=64_000_000_000,
    )
    assert est.is_concerning is False


def test_estimate_not_concerning_when_total_is_roomy() -> None:
    """High fraction-of-available alone must not warn on a roomy machine.

    macOS often reports very low ``available`` even when total RAM is
    plentiful (``vm_stat`` excludes evictable active pages). The gate
    sanity-checks against total before triggering, so a 64 GB box keeps
    quiet even when free briefly looks tight.
    """
    # 12 GB estimate vs 15 GB available is 80 % — would cross the
    # available fraction. But total is 64 GB (the estimate is 19 % of it),
    # so the analysis has plenty of room to spread into.
    est = MemoryEstimate(
        label="x",
        estimated_bytes=12_000_000_000,
        available_bytes=15_000_000_000,
        total_bytes=64_000_000_000,
    )
    assert est.is_concerning is False


def test_estimate_uses_total_when_available_unknown() -> None:
    """When only total is known we fall back to a stricter fraction of it."""
    # 10 GB on a 16 GB box (62.5 % of total) crosses the total-fraction
    # threshold (40 %).
    tight = MemoryEstimate(
        label="x",
        estimated_bytes=10_000_000_000,
        available_bytes=None,
        total_bytes=16_000_000_000,
    )
    assert tight.is_concerning is True
    # 4 GB on a 64 GB box (6 %) stays well under the threshold.
    roomy = MemoryEstimate(
        label="x",
        estimated_bytes=4_000_000_000,
        available_bytes=None,
        total_bytes=64_000_000_000,
    )
    assert roomy.is_concerning is False


def test_estimate_uses_absolute_only_when_no_probe() -> None:
    # Both probes failed: fall back to the fixed huge-file threshold.
    est = MemoryEstimate(
        label="x",
        estimated_bytes=memory.WARN_ABSOLUTE_BYTES + 1,
        available_bytes=None,
        total_bytes=None,
    )
    assert est.is_concerning is True


# --------------------------------------------------------------------------- #
# Estimator
# --------------------------------------------------------------------------- #


def _write_short_wav(path: Path, *, duration_s: float, sr: int = 48000) -> None:
    samples = np.zeros(int(duration_s * sr), dtype=np.float32)
    sf.write(str(path), samples, sr, subtype="PCM_16")


def test_estimate_file_bytes_is_flat_in_duration(tmp_path: Path) -> None:
    """Streaming analysis: the estimate must not scale with file length.

    A 4× longer file only grows the estimate by the tiny per-hour
    accumulator term — for sub-minute test files that rounds to a few
    kB on top of the flat base.
    """
    short = tmp_path / "short.wav"
    long_ = tmp_path / "long.wav"
    _write_short_wav(short, duration_s=0.5)
    _write_short_wav(long_, duration_s=2.0)

    short_estimate = estimate_file_bytes(short)
    long_estimate = estimate_file_bytes(long_)
    assert short_estimate >= memory.STREAMING_BASE_BYTES
    assert long_estimate >= short_estimate
    # The whole point of the streaming rewrite: no proportional growth.
    assert long_estimate < memory.STREAMING_BASE_BYTES + memory.STREAMING_BYTES_PER_HOUR


def test_estimate_file_bytes_unprobeable_input_uses_base(tmp_path: Path) -> None:
    """If probing returns no metadata, report the flat streaming base.

    An unprobeable file still streams chunk by chunk, so its peak RAM
    is no different from a probeable one of unknown duration.
    """
    bogus = tmp_path / "garbage.bin"
    bogus.write_bytes(b"\x00" * 1024)
    assert estimate_file_bytes(bogus) == memory.STREAMING_BASE_BYTES


def test_estimate_project_bytes_is_one_pass_not_a_sum(tmp_path: Path) -> None:
    """Project mode runs sequential streaming passes, not parallel buffers.

    The combined pass spans the total duration, so the estimate is one
    streaming working set over the summed duration — far below the sum
    of 2.1-style per-track decode buffers.
    """
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    _write_short_wav(a, duration_s=0.5)
    _write_short_wav(b, duration_s=0.5)
    single = estimate_file_bytes(a)
    total = estimate_project_bytes([a, b])
    assert total >= memory.STREAMING_BASE_BYTES
    # One pass over 1 s of audio, not 2 × (base + per-file term).
    assert total < 2 * single


# --------------------------------------------------------------------------- #
# Pipeline integration
# --------------------------------------------------------------------------- #


def test_pipeline_aborts_when_callback_cancels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A False answer from the confirm callback aborts before decode."""
    wav = tmp_path / "loud.wav"
    _write_short_wav(wav, duration_s=0.1)

    # Force the estimate to look concerning regardless of the actual file.
    monkeypatch.setattr(
        memory,
        "build_estimate",
        lambda label, estimated_bytes: MemoryEstimate(
            label=label,
            estimated_bytes=10_000_000_000,
            available_bytes=1_000_000_000,
            total_bytes=2_000_000_000,
        ),
    )

    captured: list[MemoryEstimate] = []

    def cb(est: MemoryEstimate) -> bool:
        captured.append(est)
        return False  # user clicks "No"

    with pytest.raises(RamCheckCancelled):
        pipeline.analyze(wav, confirm_memory_cb=cb)
    assert captured, "confirm callback should have been called"
    assert captured[0].is_concerning


def test_pipeline_skips_callback_for_small_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the estimate is fine, the callback isn't invoked.

    We stop the run after the guard — decode() is monkeypatched to a
    no-op-style raise so the test doesn't depend on ffmpeg's
    behaviour for a one-sample file. The point is: the gate should
    never reach decode() in this scenario.
    """
    wav = tmp_path / "tiny.wav"
    _write_short_wav(wav, duration_s=0.05)

    monkeypatch.setattr(
        memory,
        "build_estimate",
        lambda label, estimated_bytes: MemoryEstimate(
            label=label,
            estimated_bytes=1_000_000,
            available_bytes=16_000_000_000,
            total_bytes=32_000_000_000,
        ),
    )

    calls = {"n": 0}

    def cb(_: MemoryEstimate) -> bool:
        calls["n"] += 1
        return False  # would cancel if reached

    # Replace decode with a sentinel so the test doesn't need to run
    # the full ffmpeg pipeline; reaching this code proves the guard
    # let us pass.
    class _StopHere(Exception):
        pass

    def _fake_decode(path, sinks=None, on_progress=None, sinks_factory=None, cancel=None):  # noqa: ANN001
        raise _StopHere()

    monkeypatch.setattr(pipeline, "decode_and_measure_streaming", _fake_decode)
    with pytest.raises(_StopHere):
        pipeline.analyze(wav, confirm_memory_cb=cb)
    assert calls["n"] == 0


def test_pipeline_invokes_callback_only_when_concerning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The callback must fire exactly once when the estimate is concerning."""
    wav = tmp_path / "big.wav"
    _write_short_wav(wav, duration_s=0.05)

    monkeypatch.setattr(
        memory,
        "build_estimate",
        lambda label, estimated_bytes: MemoryEstimate(
            label=label,
            estimated_bytes=5_000_000_000,
            available_bytes=1_000_000_000,
            total_bytes=2_000_000_000,
        ),
    )

    calls: list[MemoryEstimate] = []

    def cb(est: MemoryEstimate) -> bool:
        calls.append(est)
        return True  # let it through

    class _StopHere(Exception):
        pass

    def _fake_decode(path, sinks=None, on_progress=None, sinks_factory=None, cancel=None):  # noqa: ANN001
        raise _StopHere()

    monkeypatch.setattr(pipeline, "decode_and_measure_streaming", _fake_decode)
    with pytest.raises(_StopHere):
        pipeline.analyze(wav, confirm_memory_cb=cb)
    assert len(calls) == 1
    assert calls[0].is_concerning is True
