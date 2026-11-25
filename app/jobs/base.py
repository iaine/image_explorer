"""Shared background-job contract used by every pipeline.

A Job runs on a QThreadPool thread and talks to the GUI only through
JobSignals. Signals emitted from the worker thread are delivered to slots in
the GUI thread via queued connections, so the UI never blocks.

Outcome signals (exactly one fires per run, followed by ``finished``):
    success(result)            - job completed normally
    failure(message)           - expected, user-facing problem (raise JobFailed)
                                 or the job was cancelled
    error(message, traceback)  - unexpected exception
"""
from __future__ import annotations

import threading
import time
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal


class JobFailed(Exception):
    """Raise inside ``Job.execute`` for expected, user-facing failures."""


class JobCancelled(Exception):
    """Raised by ``Job.check_cancelled`` to unwind a cancelled job quickly."""


class JobSignals(QObject):
    started = Signal()
    progress = Signal(int, int)  # done, total
    eta = Signal(float)  # seconds remaining (negative = unknown)
    message = Signal(str)  # log line
    item = Signal(object)  # streamed partial result
    success = Signal(object)  # final result
    failure = Signal(str)  # expected problem / cancelled
    error = Signal(str, str)  # message, traceback
    finished = Signal()  # always fires last


class Job(QRunnable):
    """Base class for background work. Subclasses implement ``execute``."""

    #: Human-readable name shown in the status bar and log.
    title = "Job"
    #: Maximum progress emissions per second (final update is always sent).
    progress_hz = 15.0

    def __init__(self) -> None:
        super().__init__()
        # The JobRunner keeps the Python reference alive; don't let Qt delete
        # the C++ object out from under it.
        self.setAutoDelete(False)
        self.signals = JobSignals()
        self._cancel = threading.Event()
        self._last_progress = 0.0

    # ----- cancellation -------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancelled(self) -> None:
        """Call inside loops; raises JobCancelled if the user cancelled."""
        if self._cancel.is_set():
            raise JobCancelled()

    # ----- helpers for subclasses --------------------------------------
    def report_progress(self, done: int, total: int, eta: float | None = None) -> None:
        """Throttled progress (+ optional ETA) emission."""
        now = time.monotonic()
        is_final = done >= total
        if not is_final and now - self._last_progress < 1.0 / self.progress_hz:
            return
        self._last_progress = now
        self.signals.progress.emit(done, total)
        if eta is not None:
            self.signals.eta.emit(float(eta))

    def cancel_message(self) -> str:
        """Failure text when cancelled; must start with 'Cancelled'."""
        return "Cancelled by user"

    def summarize(self, result) -> str:
        """One-line, human-readable description of a successful result."""
        return f"{self.title} finished" if result is None else f"{self.title} finished: {result}"

    def log(self, text: str) -> None:
        self.signals.message.emit(text)

    # ----- QRunnable ----------------------------------------------------
    def run(self) -> None:  # noqa: D401 - Qt override
        self.signals.started.emit()
        try:
            result = self.execute()
            if self.cancelled:
                self.signals.failure.emit(self.cancel_message())
            else:
                self.signals.success.emit(result)
        except JobCancelled:
            self.signals.failure.emit(self.cancel_message())
        except JobFailed as exc:
            self.signals.failure.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - report everything else
            self.signals.error.emit(f"{type(exc).__name__}: {exc}", traceback.format_exc())
        finally:
            self.signals.finished.emit()

    def execute(self):
        raise NotImplementedError
