#!/usr/bin/env python3
"""Verify the 2.2 streaming pipeline's peak RAM against the 2.1 batch path.

Screenreader-friendly verification: prints one fact per line and a
final PASS/FAIL verdict, no tables, no progress bars.

Method
------
A synthetic stereo WAV (default: 10 minutes at 48 kHz) is written in
1-second chunks so this parent process stays small. Three child
processes then each report their peak RSS:

- ``baseline``: imports the analysis stack, touches no audio.
- ``streaming``: runs ``pipeline.analyze`` (the 2.2 streaming pass).
- ``batch``: replays the 2.1 pipeline (full decode, then the batch
  analysers on the materialised buffers).

Peak RSS is per-process and monotonic, so separate processes are the
only way to attribute peaks honestly. The interesting numbers are the
deltas against the baseline child.

PASS criteria
-------------
1. The streaming delta stays under ``--limit-mb`` (default 300 MB) —
   i.e. peak RAM is bounded by chunk + accumulator state, not by the
   decoded file (the 10-minute test file alone would be ~345 MB as
   float32 mono+stereo buffers).
2. The batch delta is at least twice the streaming delta, proving the
   rewrite actually moved the needle on the same input.

Run from the repository root:

    .venv/bin/python scripts/verify_streaming_ram.py
    .venv/bin/python scripts/verify_streaming_ram.py --minutes 30
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

SR = 48000
CHILD_MARKER = "PEAK_RSS_BYTES="


def _write_test_wav(path: Path, seconds: int) -> None:
    """Write a stereo test signal in 1-second chunks (parent stays small)."""
    import numpy as np
    import soundfile as sf

    with sf.SoundFile(
        str(path), "w", samplerate=SR, channels=2, subtype="PCM_16"
    ) as f:
        for second in range(seconds):
            t = (np.arange(SR) + second * SR) / SR
            left = 0.4 * np.sin(2 * np.pi * 440.0 * t)
            right = 0.4 * np.sin(2 * np.pi * 440.0 * t + 0.5)
            f.write(np.column_stack((left, right)).astype(np.float32))


def _child_code(mode: str, wav: str) -> str:
    """Source for one measurement child. Prints its peak RSS at the end."""
    return f"""
import sys
sys.path.insert(0, {str(SRC)!r})
from nonvisualaudio.analysis import memory
from nonvisualaudio.analysis import pipeline
from nonvisualaudio.analysis.dynamics import compute_dynamics
from nonvisualaudio.analysis.spectrum import compute_spectrum
from nonvisualaudio.analysis.stereo import compute_stereo
from nonvisualaudio.audio.decoder import decode_and_measure

mode = {mode!r}
if mode == "streaming":
    pipeline.analyze({wav!r})
elif mode == "batch":
    decoded, loudness = decode_and_measure({wav!r})
    stereo = compute_stereo(decoded.stereo_samples, decoded.sample_rate)
    from dataclasses import replace
    decoded = replace(decoded, stereo_samples=None)
    dynamics = compute_dynamics(decoded.samples, decoded.sample_rate)
    spectrum = compute_spectrum(decoded.samples, decoded.sample_rate)
print("{CHILD_MARKER}" + str(memory.peak_rss_bytes()))
"""


def _measure(mode: str, wav: Path) -> int:
    proc = subprocess.run(
        [sys.executable, "-c", _child_code(mode, str(wav))],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if proc.returncode != 0:
        print(f"FAIL: {mode} child exited {proc.returncode}.")
        print(proc.stderr.strip()[-2000:])
        raise SystemExit(1)
    for line in proc.stdout.splitlines():
        if line.startswith(CHILD_MARKER):
            return int(line[len(CHILD_MARKER):])
    print(f"FAIL: {mode} child printed no peak-RSS line.")
    raise SystemExit(1)


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.0f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--minutes",
        type=int,
        default=10,
        help="length of the synthetic stereo test file (default: 10)",
    )
    parser.add_argument(
        "--limit-mb",
        type=int,
        default=300,
        help="maximum allowed streaming peak delta in MB (default: 300)",
    )
    parser.add_argument(
        "--skip-batch",
        action="store_true",
        help="only check the streaming bound, skip the 2.1 comparison run",
    )
    args = parser.parse_args()
    seconds = args.minutes * 60

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "ram_check.wav"
        print(
            f"Writing test file: {args.minutes} minutes stereo at {SR} Hz "
            f"({_mb(seconds * SR * 2 * 2)} on disk)."
        )
        _write_test_wav(wav, seconds)
        decoded_mb = _mb(seconds * SR * 4 * 3)
        print(
            f"For reference: 2.1 would hold {decoded_mb} of decoded buffers "
            "(mono mixdown plus stereo) before analysis overhead."
        )

        print("Measuring baseline child (imports only).")
        baseline = _measure("baseline", wav)
        print(f"Baseline peak RSS: {_mb(baseline)}.")

        print("Measuring streaming child (2.2 pipeline.analyze).")
        streaming = _measure("streaming", wav)
        streaming_delta = max(0, streaming - baseline)
        print(
            f"Streaming peak RSS: {_mb(streaming)}, "
            f"delta over baseline: {_mb(streaming_delta)}."
        )

        batch_delta = None
        if not args.skip_batch:
            print("Measuring batch child (2.1 reference pipeline).")
            batch = _measure("batch", wav)
            batch_delta = max(0, batch - baseline)
            print(
                f"Batch peak RSS: {_mb(batch)}, "
                f"delta over baseline: {_mb(batch_delta)}."
            )

    limit = args.limit_mb * 1024 * 1024
    ok = True
    if streaming_delta <= limit:
        print(
            f"PASS: streaming delta {_mb(streaming_delta)} is within the "
            f"{args.limit_mb} MB bound."
        )
    else:
        ok = False
        print(
            f"FAIL: streaming delta {_mb(streaming_delta)} exceeds the "
            f"{args.limit_mb} MB bound."
        )
    if batch_delta is not None:
        if streaming_delta * 2 <= batch_delta:
            print(
                "PASS: streaming uses less than half the batch path's "
                f"extra RAM ({_mb(streaming_delta)} vs {_mb(batch_delta)})."
            )
        else:
            ok = False
            print(
                "FAIL: streaming did not clearly improve on the batch path "
                f"({_mb(streaming_delta)} vs {_mb(batch_delta)})."
            )
    print("Overall verdict: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
