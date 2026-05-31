"""Cooperative cancellation for a running analysis.

The analysis runs on a background thread and spends almost all of its
wall-clock time inside ffmpeg subprocesses. A bare :class:`threading.Event`
is not enough to stop it promptly: a thread blocked in ``proc.stdout.read()``
or ``subprocess.run`` does not notice a flag being set. So a
:class:`Cancellation` carries *both* an event (polled by the cheap CPU-bound
loops) *and* a reference to the subprocess that is running right now, so
:meth:`cancel` can terminate it directly — closing its pipes, which unblocks
the reader immediately.

Nothing here changes any analysis value: when no :class:`Cancellation` is
passed (the default everywhere), the code paths behave byte-for-byte as
before.
"""

from __future__ import annotations

import logging
import subprocess
import threading

log = logging.getLogger("nonvisualaudio.cancellation")

# How long to wait for a graceful terminate() before escalating to kill().
_TERMINATE_GRACE_SECONDS = 1.0


class CancelledError(Exception):
    """Raised inside the analysis pipeline when the user cancels a run.

    Deliberately *not* a :class:`UserFacingError` and *not* an
    :class:`~nonvisualaudio.audio.ffmpeg_runner.FFmpegError`: cancellation
    is a normal outcome, not a technical failure, so it must never reach
    an error dialog. The worker catches it, logs it at INFO level, and
    stays silent on the UI side.
    """


class Cancellation:
    """A one-shot cancellation token shared between the UI and the worker.

    Created by the worker, handed down through the analysis call chain,
    and signalled from the UI thread via :meth:`cancel`. The object is
    cheap and thread-safe; once cancelled it stays cancelled.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        # The process a background teardown thread is already handling, so
        # repeated cancel() calls do not spawn a second thread for it.
        self._teardown_proc: subprocess.Popen | None = None

    # ------------------------------------------------------------------ #
    # Signalling (UI thread)
    # ------------------------------------------------------------------ #

    def cancel(self) -> None:
        """Request cancellation and terminate any running subprocess.

        Safe to call repeatedly and from any thread. Returns immediately:
        the event is set synchronously (so the worker's polling loops see
        it at once), but the blocking ``terminate()`` → ``wait()`` →
        ``kill()`` sequence runs on a short-lived daemon thread so the
        caller — typically the UI thread — is never stalled by the grace
        window. The teardown still escalates exactly as before.
        """
        self._event.set()
        with self._lock:
            proc = self._proc
            # Nothing registered, or a teardown for this exact process is
            # already in flight: do not spawn another thread for it.
            if proc is None or proc is self._teardown_proc:
                return
            self._teardown_proc = proc
        # Pass ``proc`` as a local snapshot so a concurrent clear_process()
        # resetting self._proc cannot redirect or strand this teardown.
        threading.Thread(
            target=self._terminate,
            args=(proc,),
            daemon=True,
            name="nva-cancel",
        ).start()

    # ------------------------------------------------------------------ #
    # Polling (worker thread)
    # ------------------------------------------------------------------ #

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise :class:`CancelledError` if cancellation was requested."""
        if self._event.is_set():
            raise CancelledError()

    # ------------------------------------------------------------------ #
    # Subprocess registration (worker thread)
    # ------------------------------------------------------------------ #

    def bind_process(self, proc: subprocess.Popen) -> None:
        """Register ``proc`` as the subprocess to kill on cancel.

        Re-checks the event under the lock so a :meth:`cancel` that
        landed between ``Popen(...)`` and this call still terminates the
        freshly spawned process instead of leaking it.
        """
        with self._lock:
            self._proc = proc
            already = self._event.is_set()
        if already:
            self._terminate(proc)

    def clear_process(self) -> None:
        """Forget the current subprocess (called when it has exited)."""
        with self._lock:
            self._proc = None

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
        except Exception as exc:  # noqa: BLE001 — best effort, never raise on cancel
            log.debug("terminate() raised during cancel: %s", exc)
        try:
            proc.wait(timeout=_TERMINATE_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass
        except Exception as exc:  # noqa: BLE001
            log.debug("wait() raised during cancel: %s", exc)
        try:
            proc.kill()
        except Exception as exc:  # noqa: BLE001
            log.debug("kill() raised during cancel: %s", exc)
