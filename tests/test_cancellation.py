"""Tests for cooperative cancellation of a running analysis.

The cancellation layer has three jobs and a test for each:

1. :class:`Cancellation` signals an event and tears down a registered
   subprocess (directly, and the spawn/cancel race via ``bind_process``).
2. The three ffmpeg runners (:func:`run`, :func:`run_split_streams`,
   :func:`run_split_streams_streaming`) stop a running child on cancel
   and raise :class:`CancelledError` — never an :class:`FFmpegError`.
3. The analysis entry points (:func:`pipeline.analyze`,
   :func:`project.analyze_project`) poll the token and abort.

The runner tests drive a plain ``python -c ...`` child rather than ffmpeg,
so they need no audio fixtures and no ffmpeg binary.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from nonvisualaudio.audio.ffmpeg_runner import (
    FFmpegError,
    run,
    run_split_streams,
    run_split_streams_streaming,
)
from nonvisualaudio.cancellation import Cancellation, CancelledError


# --------------------------------------------------------------------------- #
# Cancellation token
# --------------------------------------------------------------------------- #


def test_raise_if_cancelled() -> None:
    c = Cancellation()
    c.raise_if_cancelled()  # no-op before cancel
    assert not c.is_cancelled()
    c.cancel()
    assert c.is_cancelled()
    with pytest.raises(CancelledError):
        c.raise_if_cancelled()


def test_cancel_terminates_registered_process() -> None:
    c = Cancellation()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    c.bind_process(proc)
    c.cancel()
    # Should be reaped well within the grace + kill window.
    proc.wait(timeout=5)
    assert proc.poll() is not None


def test_bind_process_kills_when_already_cancelled() -> None:
    c = Cancellation()
    c.cancel()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    # Simulates a cancel that landed between Popen and registration: the
    # freshly spawned process must still be torn down, not leaked.
    c.bind_process(proc)
    proc.wait(timeout=5)
    assert proc.poll() is not None


def test_cancel_is_idempotent() -> None:
    c = Cancellation()
    c.cancel()
    c.cancel()  # second call must not raise
    assert c.is_cancelled()


def test_cancel_returns_immediately_and_dedups_teardown() -> None:
    # Child ignores SIGTERM, so terminate() will not reap it: the teardown
    # thread has to sit through the full grace window before escalating to
    # kill(). cancel() itself must still return at once, and repeated calls
    # must not pile up extra teardown threads for the same process.
    prog = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(30)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", prog])
    c = Cancellation()
    c.bind_process(proc)

    baseline = sum(1 for t in threading.enumerate() if t.name == "nva-cancel")
    start = time.perf_counter()
    c.cancel()
    c.cancel()
    c.cancel()
    elapsed = time.perf_counter() - start

    # Must not block on the 1 s terminate grace window.
    assert elapsed < 0.5
    # Three cancel() calls, but exactly one teardown thread for this proc.
    active = sum(1 for t in threading.enumerate() if t.name == "nva-cancel")
    assert active - baseline == 1

    # The teardown escalates to kill(), so the process still dies.
    proc.wait(timeout=5)
    assert proc.poll() is not None


# --------------------------------------------------------------------------- #
# ffmpeg runners — pre-cancelled (raise before spawning anything)
# --------------------------------------------------------------------------- #


def test_run_precancelled_raises_cancelled() -> None:
    c = Cancellation()
    c.cancel()
    with pytest.raises(CancelledError):
        run([sys.executable, "-c", "pass"], cancel=c)


def test_run_split_streams_precancelled_raises_cancelled() -> None:
    c = Cancellation()
    c.cancel()
    with pytest.raises(CancelledError):
        run_split_streams([sys.executable, "-c", "pass"], cancel=c)


def test_run_split_streams_streaming_precancelled_raises_cancelled() -> None:
    c = Cancellation()
    c.cancel()
    with pytest.raises(CancelledError):
        run_split_streams_streaming(
            [sys.executable, "-c", "pass"],
            stdout_chunk_handler=lambda _b: None,
            cancel=c,
        )


# --------------------------------------------------------------------------- #
# ffmpeg runners — cancelled mid-run (kill child, raise CancelledError)
# --------------------------------------------------------------------------- #

_SLEEPER = "import time; time.sleep(30)"


def test_run_cancelled_midrun() -> None:
    c = Cancellation()
    threading.Timer(0.3, c.cancel).start()
    with pytest.raises(CancelledError):
        run([sys.executable, "-c", _SLEEPER], timeout=30, cancel=c)


def test_run_split_streams_cancelled_midrun() -> None:
    c = Cancellation()
    threading.Timer(0.3, c.cancel).start()
    with pytest.raises(CancelledError):
        run_split_streams([sys.executable, "-c", _SLEEPER], timeout=30, cancel=c)


def test_run_split_streams_streaming_cancelled_in_read_loop() -> None:
    # Child streams bytes forever; we cancel from inside the chunk handler
    # so the next loop iteration observes the request and tears it down.
    prog = (
        "import sys, time\n"
        "while True:\n"
        "    sys.stdout.buffer.write(b'x' * 1024)\n"
        "    sys.stdout.buffer.flush()\n"
        "    time.sleep(0.01)\n"
    )
    c = Cancellation()

    def _handler(_chunk: bytes) -> None:
        c.cancel()

    with pytest.raises(CancelledError):
        run_split_streams_streaming(
            [sys.executable, "-c", prog],
            stdout_chunk_handler=_handler,
            chunk_size=1024,
            cancel=c,
        )


def test_runners_unaffected_without_cancel() -> None:
    # Regression guard: the cancel plumbing must not change the normal
    # (cancel=None) behaviour of the rewritten run().
    proc = run([sys.executable, "-c", "import sys; sys.stdout.write('hi')"])
    assert proc.returncode == 0
    assert proc.stdout == b"hi"


def test_run_nonzero_exit_still_raises_ffmpeg_error() -> None:
    # A genuine failure (no cancel) must still surface as FFmpegError.
    with pytest.raises(FFmpegError):
        run([sys.executable, "-c", "import sys; sys.exit(3)"])


# --------------------------------------------------------------------------- #
# Analysis entry points
# --------------------------------------------------------------------------- #


def test_pipeline_analyze_raises_cancelled_after_decode(monkeypatch) -> None:
    from nonvisualaudio.analysis import pipeline

    c = Cancellation()
    c.cancel()
    # Fake decode so the test needs no audio file; the post-decode cancel
    # check fires before the return value is ever inspected.
    monkeypatch.setattr(
        pipeline, "decode_and_measure", lambda *a, **k: (object(), object())
    )
    with pytest.raises(CancelledError):
        pipeline.analyze("does-not-matter.wav", cancel=c)


def test_analyze_project_raises_before_first_file(monkeypatch) -> None:
    from nonvisualaudio.analysis import project

    c = Cancellation()
    c.cancel()
    calls = {"n": 0}

    def _decode(*_a, **_k):
        calls["n"] += 1
        return (object(), object())

    monkeypatch.setattr(project, "decode_and_measure", _decode)
    with pytest.raises(CancelledError):
        project.analyze_project(["a.wav", "b.wav"], cancel=c)
    # Cancel is checked at the top of the file loop, so nothing decodes.
    assert calls["n"] == 0
