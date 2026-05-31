"""Locate and run the bundled (or system) ffmpeg/ffprobe binary.

This wrapper is intentionally minimal and never passes arguments through a
shell — every call uses an argument list so filenames with spaces or unusual
characters are safe.
"""

from __future__ import annotations

import functools
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Sequence

from nonvisualaudio.cancellation import Cancellation, CancelledError
from nonvisualaudio.errors import MissingFFmpegError
from nonvisualaudio.localization import t

log = logging.getLogger("nonvisualaudio.ffmpeg")

# Set by find_ffmpeg() the first time it resolves a working binary. The
# diagnostic report reads this so a support log makes it obvious whether
# the bundled binary or a system fallback was actually used. Tuple of
# (path, "bundled" | "system").
_active_info: tuple[str, str] | None = None


def _subprocess_env() -> dict[str, str]:
    """Build a minimal subprocess env that preserves dynamic-linker hints.

    The bundled ffmpeg ships statically linked on every platform — but a
    system fallback (or a developer running from source against a
    Homebrew/MacPorts ffmpeg) may need ``DYLD_LIBRARY_PATH`` /
    ``LD_LIBRARY_PATH`` to resolve its dynamic dependencies. Stripping
    those out surfaces as cryptic "image not found" errors at the first
    analysis run, which is exactly the kind of failure this hardening
    pass exists to prevent.
    """
    env: dict[str, str] = {"PATH": os.environ.get("PATH", "")}
    forward: tuple[str, ...]
    if sys.platform == "darwin":
        forward = ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH")
    elif sys.platform.startswith("linux"):
        forward = ("LD_LIBRARY_PATH",)
    else:
        forward = ()
    for name in forward:
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


class FFmpegError(RuntimeError):
    """Raised when an ffmpeg invocation fails for a recoverable reason.

    The audio layer re-wraps these into user-facing errors with filename
    context before they reach the UI.
    """


def _platform_dir() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "win"
    return "linux"


def _bundled_binary(name: str) -> Path | None:
    """Return the bundled binary path if present, else None."""
    exe = f"{name}.exe" if sys.platform.startswith("win") else name
    here = Path(__file__).resolve().parent.parent
    candidate = here / "resources" / "bin" / _platform_dir() / exe
    if candidate.is_file():
        return candidate
    return None


def _install_hint() -> str:
    if sys.platform == "darwin":
        return t("error.ffmpeg.install.darwin")
    if sys.platform.startswith("win"):
        return t("error.ffmpeg.install.windows")
    return t("error.ffmpeg.install.linux")


@functools.lru_cache(maxsize=8)
def _binary_runs(path: str) -> bool:
    """Return True if the binary at ``path`` actually launches.

    A bundled binary can be present on disk yet be unusable — e.g. a
    macOS build whose dynamic-library dependencies are missing or built
    for the wrong architecture. ``-version`` is cheap and exercises the
    dynamic loader, so a clean exit means the binary is genuinely
    runnable. The result is cached: ``find_ffmpeg`` is called several
    times per analysis and the answer cannot change mid-run.
    """
    try:
        proc = subprocess.run(
            [path, "-version"],
            capture_output=True,
            timeout=10.0,
            env=_subprocess_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("ffmpeg probe of %s failed: %s", path, exc)
        return False
    return proc.returncode == 0


def find_ffmpeg() -> str:
    """Return the path to a working ffmpeg.

    Prefer the bundled binary, but verify it actually launches before
    committing to it: a bundled ffmpeg can be present yet unusable
    (missing or wrong-architecture dylibs). If it fails to start, fall
    back to a system ffmpeg on PATH so the analysis still runs — and
    log that prominently so the situation shows up in support reports.
    The decision is cached for the life of the process; both paths get
    re-probed only on the first call.
    """
    global _active_info
    if _active_info is not None:
        return _active_info[0]
    bundled = _bundled_binary("ffmpeg")
    system = shutil.which("ffmpeg")
    if bundled is not None and _binary_runs(str(bundled)):
        _active_info = (str(bundled), "bundled")
        log.info("ffmpeg resolved: bundled (%s)", bundled)
        return str(bundled)
    if bundled is not None:
        log.error(
            "bundled ffmpeg at %s did not launch — bundle may be stale or "
            "platform-mismatched; attempting system PATH fallback",
            bundled,
        )
    if system and _binary_runs(system):
        _active_info = (system, "system")
        # Loud on purpose: a system fallback means the bundled install is
        # either missing or broken on this user's machine. Catching this
        # in a support log lets us refresh the bundle before more users
        # hit the same wall.
        log.error(
            "ffmpeg resolved: system PATH (%s) — bundled binary was not usable",
            system,
        )
        return system
    raise MissingFFmpegError(
        title=t("error.ffmpeg.missing.title"),
        body=t("error.ffmpeg.missing.body"),
        hint=_install_hint(),
    )


def active_ffmpeg_info() -> tuple[str, str] | None:
    """Return ``(path, source)`` for the resolved ffmpeg, or None.

    ``source`` is ``"bundled"`` or ``"system"``. Returns None until
    :func:`find_ffmpeg` has been called at least once (no analysis run).
    """
    return _active_info


def run_split_streams(
    args: Sequence[str],
    *,
    timeout: float = 600.0,
    stderr_line_callback: Callable[[bytes], None] | None = None,
    cancel: Cancellation | None = None,
) -> tuple[bytes, str]:
    """Run ``args`` capturing stdout and stderr through separate pipes.

    The two-stream variant exists for the combined decode + ebur128 pass:
    stdout carries gigabytes of PCM, stderr carries the ebur128 progress
    and summary, and the consumer needs both. ``subprocess.run`` would
    work, but reading stderr only after stdout drains can deadlock if the
    OS pipe buffers fill up — so we always read stderr from a worker
    thread.

    ``stderr_line_callback`` — if given — is fired synchronously on the
    stderr thread for every full line. The caller uses this to parse
    ebur128 ``t:`` markers and emit live progress while the ffmpeg pass
    is still running. The callback must be cheap; exceptions are caught
    and logged so a buggy callback cannot wedge the worker thread.

    ``cancel`` — if given — lets the caller stop the run mid-flight: the
    process is registered with it so a concurrent :meth:`Cancellation.cancel`
    terminates ffmpeg, and a :class:`CancelledError` is raised instead of
    an :class:`FFmpegError` once the process has been torn down.
    """
    binary = Path(args[0]).name
    log.debug("exec stream %s %s", binary, " ".join(str(a) for a in args[1:]))
    t0 = time.time()
    if cancel is not None:
        cancel.raise_if_cancelled()
    try:
        proc = subprocess.Popen(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_subprocess_env(),
        )
    except FileNotFoundError as exc:
        log.error("%s not found on PATH", binary)
        raise FFmpegError(f"binary_not_found:{args[0]}") from exc
    if cancel is not None:
        cancel.bind_process(proc)

    stderr_chunks: list[bytes] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        try:
            for line in iter(proc.stderr.readline, b""):
                stderr_chunks.append(line)
                if stderr_line_callback is not None:
                    try:
                        stderr_line_callback(line)
                    except Exception:  # noqa: BLE001 — never let a UI callback wedge ffmpeg
                        log.exception("stderr_line_callback raised")
        finally:
            try:
                proc.stderr.close()
            except Exception:  # noqa: BLE001
                pass

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()
    try:
        assert proc.stdout is not None
        stdout_data = proc.stdout.read()
    finally:
        try:
            assert proc.stdout is not None
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        log.error("%s timed out after %.1fs", binary, timeout)
        raise FFmpegError(f"timeout:{timeout}") from exc
    finally:
        if cancel is not None:
            cancel.clear_process()
    # A cancel that fired during the run terminated the process, which
    # surfaces here as a non-zero exit. Report it as cancellation, never
    # as an FFmpegError, so it cannot reach an error dialog.
    if cancel is not None:
        cancel.raise_if_cancelled()
    # Reader thread should be near the end by now; cap the join so a
    # stuck thread cannot freeze the analyser indefinitely.
    stderr_thread.join(timeout=30.0)
    stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    elapsed = time.time() - t0
    if proc.returncode != 0:
        log.error(
            "%s exited %d after %.2fs: %s",
            binary,
            proc.returncode,
            elapsed,
            stderr_text[:400],
        )
        raise FFmpegError(f"exit:{proc.returncode}\n{stderr_text}")
    log.debug(
        "%s done in %.2fs (%d bytes stdout)", binary, elapsed, len(stdout_data)
    )
    return stdout_data, stderr_text


def run_split_streams_streaming(
    args: Sequence[str],
    *,
    timeout: float = 600.0,
    stdout_chunk_handler: Callable[[bytes], None],
    stderr_line_callback: Callable[[bytes], None] | None = None,
    chunk_size: int = 1 << 20,
    cancel: Cancellation | None = None,
) -> str:
    """Streaming variant of :func:`run_split_streams`.

    Instead of buffering stdout into one bytes object, the function reads
    it in ``chunk_size``-sized blocks and hands each block to
    ``stdout_chunk_handler``. The handler is expected to copy the data
    somewhere safe (typically into a preallocated numpy buffer) so the
    chunk can be released before the next one arrives. This keeps peak
    memory bounded by a single buffer instead of doubling it through a
    Python ``bytes`` intermediate — the difference between fitting and
    OOM on multi-hour decodes.

    Returns the full stderr text. Stderr is drained on a worker thread
    so a chatty filter (``ebur128`` writes one progress line per second
    of audio) cannot stall the stdout pipe.
    """
    binary = Path(args[0]).name
    log.debug(
        "exec stream(chunked) %s %s", binary, " ".join(str(a) for a in args[1:])
    )
    t0 = time.time()
    if cancel is not None:
        cancel.raise_if_cancelled()
    try:
        proc = subprocess.Popen(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_subprocess_env(),
        )
    except FileNotFoundError as exc:
        log.error("%s not found on PATH", binary)
        raise FFmpegError(f"binary_not_found:{args[0]}") from exc
    if cancel is not None:
        cancel.bind_process(proc)

    stderr_chunks: list[bytes] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        try:
            for line in iter(proc.stderr.readline, b""):
                stderr_chunks.append(line)
                if stderr_line_callback is not None:
                    try:
                        stderr_line_callback(line)
                    except Exception:  # noqa: BLE001 — never let a UI callback wedge ffmpeg
                        log.exception("stderr_line_callback raised")
        finally:
            try:
                proc.stderr.close()
            except Exception:  # noqa: BLE001
                pass

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()
    total_bytes = 0
    try:
        assert proc.stdout is not None
        while True:
            # Cooperative cancel: a cancel between chunks stops the read
            # loop, tears the process down, and raises CancelledError.
            if cancel is not None and cancel.is_cancelled():
                proc.kill()
                proc.wait()
                raise CancelledError()
            chunk = proc.stdout.read(chunk_size)
            if not chunk:
                break
            total_bytes += len(chunk)
            try:
                stdout_chunk_handler(chunk)
            except Exception:
                # A failing handler must terminate ffmpeg so the parent
                # doesn't keep producing PCM into a dead pipe.
                proc.kill()
                proc.wait()
                raise
    finally:
        try:
            assert proc.stdout is not None
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        log.error("%s timed out after %.1fs", binary, timeout)
        raise FFmpegError(f"timeout:{timeout}") from exc
    finally:
        if cancel is not None:
            cancel.clear_process()
    # A cancel that fired during the run terminated the process, which
    # surfaces here as a non-zero exit. Report it as cancellation, never
    # as an FFmpegError, so it cannot reach an error dialog.
    if cancel is not None:
        cancel.raise_if_cancelled()
    stderr_thread.join(timeout=30.0)
    stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    elapsed = time.time() - t0
    if proc.returncode != 0:
        log.error(
            "%s exited %d after %.2fs: %s",
            binary,
            proc.returncode,
            elapsed,
            stderr_text[:400],
        )
        raise FFmpegError(f"exit:{proc.returncode}\n{stderr_text}")
    log.debug(
        "%s done in %.2fs (%d bytes stdout streamed)",
        binary,
        elapsed,
        total_bytes,
    )
    return stderr_text


def run(
    args: Sequence[str],
    *,
    timeout: float = 300.0,
    cancel: Cancellation | None = None,
) -> subprocess.CompletedProcess:
    """Run a command and return the completed process.

    Captures both stdout and stderr as bytes. Raises ``FFmpegError`` on
    non-zero exit or timeout. The caller is responsible for parsing output
    and, if appropriate, re-wrapping the error with filename context before
    it reaches the user.

    ``cancel`` — if given — makes the (potentially long) run abortable: the
    process is registered so :meth:`Cancellation.cancel` can terminate it,
    and a :class:`CancelledError` is raised instead of an ``FFmpegError``
    once it has been torn down. When ``cancel`` is ``None`` the behaviour is
    identical to the previous ``subprocess.run`` implementation.
    """
    t0 = time.time()
    binary = Path(args[0]).name
    log.debug("exec %s %s", binary, " ".join(str(a) for a in args[1:]))
    if cancel is not None:
        cancel.raise_if_cancelled()
    try:
        # Popen rather than subprocess.run so the running process can be
        # registered for cancellation; subprocess.run hides the handle.
        proc = subprocess.Popen(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Explicit minimal environment: PATH plus the platform's
            # dynamic-linker hints so a non-statically-linked ffmpeg can
            # still resolve its dylibs. See _subprocess_env().
            env=_subprocess_env(),
        )
    except FileNotFoundError as exc:
        log.error("%s not found on PATH", binary)
        raise FFmpegError(f"binary_not_found:{args[0]}") from exc
    if cancel is not None:
        cancel.bind_process(proc)
    try:
        try:
            stdout, stderr_bytes = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.communicate()
            log.error("%s timed out after %.1fs", binary, timeout)
            raise FFmpegError(f"timeout:{timeout}") from exc
    finally:
        if cancel is not None:
            cancel.clear_process()
    # A cancel that fired during the run terminated the process; surface it
    # as cancellation, never as an FFmpegError, so it cannot reach a dialog.
    if cancel is not None:
        cancel.raise_if_cancelled()
    completed = subprocess.CompletedProcess(
        args=list(args),
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr_bytes,
    )
    elapsed = time.time() - t0
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        log.error(
            "%s exited %d after %.2fs: %s",
            binary,
            completed.returncode,
            elapsed,
            stderr[:400],
        )
        raise FFmpegError(
            f"exit:{completed.returncode}\n{stderr}"
        )
    log.debug(
        "%s done in %.2fs (%d bytes stdout)", binary, elapsed, len(completed.stdout)
    )
    return completed
