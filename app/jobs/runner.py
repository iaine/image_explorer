"""JobRunner: starts jobs on the thread pool and keeps them alive until done.

The runner re-emits each job's signals with the job attached, so the UI can
wire progress bars, logs and message boxes in one place.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThreadPool, Signal

from .base import Job


class JobRunner(QObject):
    job_started = Signal(object)  # job
    job_progress = Signal(object, int, int)  # job, done, total
    job_eta = Signal(object, float)  # job, seconds
    job_message = Signal(object, str)  # job, text
    job_success = Signal(object, object)  # job, result
    job_failure = Signal(object, str)  # job, message
    job_error = Signal(object, str, str)  # job, message, traceback
    job_finished = Signal(object)  # job
    busy_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None, pool: QThreadPool | None = None) -> None:
        super().__init__(parent)
        self.pool = pool or QThreadPool.globalInstance()
        self._active: list[Job] = []

    @property
    def active_jobs(self) -> list[Job]:
        return list(self._active)

    def is_busy(self) -> bool:
        return bool(self._active)

    def start(self, job: Job) -> Job:
        s = job.signals
        s.started.connect(lambda: self.job_started.emit(job))
        s.progress.connect(lambda d, t: self.job_progress.emit(job, d, t))
        s.eta.connect(lambda e: self.job_eta.emit(job, e))
        s.message.connect(lambda m: self.job_message.emit(job, m))
        s.success.connect(lambda r: self.job_success.emit(job, r))
        s.failure.connect(lambda m: self.job_failure.emit(job, m))
        s.error.connect(lambda m, tb: self.job_error.emit(job, m, tb))
        s.finished.connect(lambda: self._on_finished(job))

        was_busy = self.is_busy()
        self._active.append(job)  # keep a Python reference while it runs
        if not was_busy:
            self.busy_changed.emit(True)
        self.pool.start(job)
        return job

    def cancel_all(self) -> None:
        for job in self._active:
            job.cancel()

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """Cancel everything and wait for workers. Returns True if all stopped."""
        self.cancel_all()
        return self.pool.waitForDone(timeout_ms)

    def _on_finished(self, job: Job) -> None:
        self.job_finished.emit(job)
        if job in self._active:
            self._active.remove(job)
        if not self._active:
            self.busy_changed.emit(False)
