"""Milestone 1 tests: threading + messaging contract."""
import threading

import pytest

from app.jobs import Job, JobFailed, JobRunner
from app.jobs.demo_job import DemoJob
from app.jobs.eta import EtaEstimator, format_duration
from app.ui.main_window import MainWindow


class ThreadCheckJob(Job):
    def execute(self):
        return threading.current_thread() is threading.main_thread()


@pytest.fixture
def runner(qtbot):
    r = JobRunner()
    yield r
    r.shutdown()


def test_job_runs_off_main_thread(qtbot, runner):
    job = ThreadCheckJob()
    with qtbot.waitSignal(job.signals.success, timeout=3000) as blocker:
        runner.start(job)
    assert blocker.args == [False]


def test_success_emits_progress_and_result(qtbot, runner):
    job = DemoJob(steps=20, step_seconds=0.005, outcome="success")
    progress, results = [], []
    job.signals.progress.connect(lambda d, t: progress.append((d, t)))
    job.signals.success.connect(results.append)
    with qtbot.waitSignals([job.signals.success, job.signals.finished], timeout=3000):
        runner.start(job)
    assert results == [{"steps": 20}]
    assert progress[-1] == (20, 20)  # final update is never throttled


def test_failure_is_reported_not_raised(qtbot, runner):
    job = DemoJob(steps=10, step_seconds=0.005, outcome="failure")
    with qtbot.waitSignal(job.signals.failure, timeout=3000) as blocker:
        runner.start(job)
    assert "Simulated failure" in blocker.args[0]


def test_error_includes_traceback(qtbot, runner):
    job = DemoJob(steps=10, step_seconds=0.005, outcome="error")
    with qtbot.waitSignal(job.signals.error, timeout=3000) as blocker:
        runner.start(job)
    message, tb = blocker.args
    assert "KeyError" in message
    assert "Traceback" in tb


def test_cancel_reports_failure(qtbot, runner):
    job = DemoJob(steps=1000, step_seconds=0.01)
    with qtbot.waitSignal(job.signals.started, timeout=3000):
        runner.start(job)
    with qtbot.waitSignal(job.signals.failure, timeout=3000) as blocker:
        job.cancel()
    assert blocker.args == ["Cancelled by user"]


def test_runner_busy_state(qtbot, runner):
    states = []
    runner.busy_changed.connect(states.append)
    job = DemoJob(steps=5, step_seconds=0.005)
    with qtbot.waitSignal(job.signals.finished, timeout=3000):
        runner.start(job)
    qtbot.waitUntil(lambda: not runner.is_busy(), timeout=1000)
    assert states == [True, False]


def test_main_window_logs_all_outcomes(qtbot):
    win = MainWindow(show_dialogs=False)
    qtbot.addWidget(win)
    for outcome in ("success", "failure", "error"):
        job = DemoJob(steps=6, step_seconds=0.005, outcome=outcome)
        with qtbot.waitSignal(job.signals.finished, timeout=3000):
            win.start_job(job)
    qtbot.waitUntil(lambda: not win.runner.is_busy(), timeout=1000)
    log = "\n".join(win.log_panel.lines())
    assert "finished" in log and "failed" in log and "error" in log
    assert not win.cancel_btn.isVisible()


def test_eta_estimator_and_format():
    eta = EtaEstimator(total=10)
    assert eta.remaining() == -1.0
    eta.step()
    assert eta.remaining() >= 0
    assert format_duration(-1) == "estimating…"
    assert format_duration(5) == "5 s"
    assert format_duration(125) == "2 min 05 s"
    assert format_duration(3725) == "1 h 02 min"
