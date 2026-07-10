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

import pytest

from nonvisualaudio.audio.ffmpeg_runner import (
    FFmpegError,
    run_split_streams,
    run_split_streams_streaming,
)

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
    assert b"line one\n" in lines
    assert b"line two\n" in lines


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
    # Child closes its pipe file descriptors, then sleeps: stdout EOF
    # arrives quickly and the wait() afterwards must trip the timeout.
    # (os.close, not sys.stdout.close(): the std-stream wrappers keep
    # the underlying descriptor open.)
    prog = (
        "import os, time\n"
        "os.close(1); os.close(2)\n"
        "time.sleep(30)\n"
    )
    with pytest.raises(FFmpegError) as exc_info:
        run_split_streams([sys.executable, "-c", prog], timeout=0.5)
    assert str(exc_info.value).startswith("timeout:")


def test_split_streams_streaming_timeout_kills_and_raises() -> None:
    prog = (
        "import os, time\n"
        "os.close(1); os.close(2)\n"
        "time.sleep(30)\n"
    )
    with pytest.raises(FFmpegError) as exc_info:
        run_split_streams_streaming(
            [sys.executable, "-c", prog],
            stdout_chunk_handler=lambda _b: None,
            timeout=0.5,
        )
    assert str(exc_info.value).startswith("timeout:")


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
