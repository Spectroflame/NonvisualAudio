"""Behaviour tests for the shared ffmpeg process runners.

Regression net for the refactor that pulled the duplicated spawn /
stderr-drain / wait logic of ``run_split_streams`` and
``run_split_streams_streaming`` into shared helpers: both public
functions must keep returning the same data and raising the same
errors as before. Like the cancellation tests, these drive a plain
``python -c ...`` child so they need no ffmpeg binary.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

from nonvisualaudio.audio import ffmpeg_runner
from nonvisualaudio.audio.ffmpeg_runner import (
    FFmpegError,
    run_split_streams,
    run_split_streams_streaming,
)
from nonvisualaudio.cancellation import Cancellation, CancelledError

_ECHO_BOTH = (
    "import sys\n"
    "sys.stdout.buffer.write(b'PCMDATA')\n"
    "sys.stderr.write('line one\\nline two\\n')\n"
)


def test_split_streams_returns_stdout_and_stderr() -> None:
    stdout, stderr = run_split_streams([sys.executable, "-c", _ECHO_BOTH])
    assert stdout == b"PCMDATA"
    assert "line one" in stderr
    assert "line two" in stderr


def test_split_streams_fast_process_finishes_before_deadline() -> None:
    stdout, stderr = run_split_streams(
        [sys.executable, "-c", _ECHO_BOTH], timeout=5.0
    )
    assert stdout == b"PCMDATA"
    assert "line one" in stderr


def test_split_streams_streaming_delivers_chunks_and_stderr() -> None:
    chunks: list[bytes] = []
    stderr = run_split_streams_streaming(
        [sys.executable, "-c", _ECHO_BOTH],
        stdout_chunk_handler=chunks.append,
        chunk_size=4,
    )
    assert b"".join(chunks) == b"PCMDATA"
    assert "line one" in stderr


def test_split_streams_line_callback_gets_each_stderr_line() -> None:
    lines: list[bytes] = []
    run_split_streams(
        [sys.executable, "-c", _ECHO_BOTH],
        stderr_line_callback=lines.append,
    )
    # The callback deliberately exposes raw subprocess bytes, including
    # the child's native LF or CRLF line ending.
    newline = b"\r\n" if sys.platform.startswith("win") else b"\n"
    assert lines == [
        b"line one" + newline,
        b"line two" + newline,
    ]


def test_split_streams_survives_raising_line_callback() -> None:
    def _bad(_line: bytes) -> None:
        raise RuntimeError("buggy UI callback")

    stdout, stderr = run_split_streams(
        [sys.executable, "-c", _ECHO_BOTH],
        stderr_line_callback=_bad,
    )
    # The callback must never wedge or fail the run itself.
    assert stdout == b"PCMDATA"
    assert "line two" in stderr


_FAIL = "import sys\nsys.stderr.write('boom details\\n')\nsys.exit(3)\n"


def test_split_streams_nonzero_exit_raises_with_stderr() -> None:
    with pytest.raises(FFmpegError) as exc_info:
        run_split_streams([sys.executable, "-c", _FAIL])
    message = str(exc_info.value)
    assert message.startswith("exit:3")
    assert "boom details" in message


def test_split_streams_streaming_nonzero_exit_raises_with_stderr() -> None:
    with pytest.raises(FFmpegError) as exc_info:
        run_split_streams_streaming(
            [sys.executable, "-c", _FAIL],
            stdout_chunk_handler=lambda _b: None,
        )
    message = str(exc_info.value)
    assert message.startswith("exit:3")
    assert "boom details" in message


def test_split_streams_missing_binary_raises() -> None:
    with pytest.raises(FFmpegError) as exc_info:
        run_split_streams(["/nonexistent/binary/for/this/test"])
    assert str(exc_info.value).startswith("binary_not_found:")


def test_split_streams_timeout_kills_and_raises() -> None:
    # The child deliberately keeps stdout open without ever writing. The
    # deadline must interrupt the blocking read, not start only afterwards.
    prog = "import time\ntime.sleep(30)\n"
    started = time.perf_counter()
    with pytest.raises(FFmpegError) as exc_info:
        run_split_streams([sys.executable, "-c", prog], timeout=0.3)
    elapsed = time.perf_counter() - started
    assert str(exc_info.value) == "timeout:0.3"
    assert elapsed < 2.0


def test_split_streams_cancel_wins_while_stdout_is_blocked() -> None:
    prog = "import time\ntime.sleep(30)\n"
    cancel = Cancellation()
    request_cancel = threading.Timer(0.2, cancel.cancel)
    request_cancel.daemon = True
    request_cancel.start()
    started = time.perf_counter()
    try:
        with pytest.raises(CancelledError):
            run_split_streams(
                [sys.executable, "-c", prog], timeout=5.0, cancel=cancel
            )
    finally:
        request_cancel.cancel()
    assert time.perf_counter() - started < 2.0


def test_split_streams_cancel_wins_when_deadline_also_fired(monkeypatch) -> None:
    cancel = Cancellation()
    original_wait = ffmpeg_runner._wait_and_collect_stderr

    def _cancel_before_error_check(proc, **kwargs):
        watchdog = kwargs["watchdog"]
        assert watchdog is not None and watchdog.timed_out
        cancel.cancel()
        return original_wait(proc, **kwargs)

    monkeypatch.setattr(
        ffmpeg_runner, "_wait_and_collect_stderr", _cancel_before_error_check
    )

    with pytest.raises(CancelledError):
        run_split_streams(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.3,
            cancel=cancel,
        )


def test_split_streams_streaming_timeout_kills_and_raises() -> None:
    # The child deliberately keeps stdout open without ever writing. The
    # streaming runner's deadline must interrupt its blocking chunk read.
    prog = "import time\ntime.sleep(30)\n"
    started = time.perf_counter()
    with pytest.raises(FFmpegError) as exc_info:
        run_split_streams_streaming(
            [sys.executable, "-c", prog],
            stdout_chunk_handler=lambda _b: None,
            timeout=0.3,
        )
    elapsed = time.perf_counter() - started
    assert str(exc_info.value) == "timeout:0.3"
    assert elapsed < 2.0


def test_split_streams_streaming_trickle_respects_deadline(monkeypatch) -> None:
    # One-byte reads ensure each slow write reaches the handler as a chunk.
    # A per-chunk deadline check must stop this non-blocked, never-ending
    # stream instead of letting each new byte postpone completion forever.
    prog = (
        "import sys, time\n"
        "for _ in range(100):\n"
        "    sys.stdout.buffer.write(b'x')\n"
        "    sys.stdout.buffer.flush()\n"
        "    time.sleep(0.05)\n"
    )

    class _NoopWatchdog:
        """Leave the per-chunk deadline check solely responsible."""

        timed_out = False

        def __init__(self, _proc, _timeout: float) -> None:
            pass

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            pass

    monkeypatch.setattr(ffmpeg_runner, "_Watchdog", _NoopWatchdog)
    chunks: list[bytes] = []
    started = time.perf_counter()
    with pytest.raises(FFmpegError) as exc_info:
        run_split_streams_streaming(
            [sys.executable, "-c", prog],
            stdout_chunk_handler=chunks.append,
            chunk_size=1,
            timeout=0.3,
        )
    elapsed = time.perf_counter() - started
    assert str(exc_info.value) == "timeout:0.3"
    assert chunks
    assert elapsed < 2.0


def test_split_streams_streaming_cancel_wins_when_deadline_also_fired(
    monkeypatch,
) -> None:
    cancel = Cancellation()
    original_wait = ffmpeg_runner._wait_and_collect_stderr

    def _cancel_before_error_check(proc, **kwargs):
        watchdog = kwargs["watchdog"]
        assert watchdog is not None and watchdog.timed_out
        cancel.cancel()
        return original_wait(proc, **kwargs)

    monkeypatch.setattr(
        ffmpeg_runner, "_wait_and_collect_stderr", _cancel_before_error_check
    )

    with pytest.raises(CancelledError):
        run_split_streams_streaming(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout_chunk_handler=lambda _b: None,
            timeout=0.3,
            cancel=cancel,
        )


def test_streaming_failing_chunk_handler_propagates() -> None:
    prog = (
        "import sys\n"
        "sys.stdout.buffer.write(b'x' * 4096)\n"
        "sys.stdout.buffer.flush()\n"
    )

    def _boom(_chunk: bytes) -> None:
        raise ValueError("sink rejected the data")

    with pytest.raises(ValueError, match="sink rejected the data"):
        run_split_streams_streaming(
            [sys.executable, "-c", prog],
            stdout_chunk_handler=_boom,
            chunk_size=1024,
        )
