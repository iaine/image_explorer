"""A dummy job that proves the threading and messaging work end to end."""
from __future__ import annotations

import time

from .base import Job, JobFailed
from .eta import EtaEstimator


class DemoJob(Job):
    """Sleeps through ``steps`` steps, then succeeds, fails or errors.

    outcome: "success" | "failure" | "error"
    """

    title = "Demo job"

    def __init__(self, steps: int = 50, step_seconds: float = 0.05, outcome: str = "success") -> None:
        super().__init__()
        self.steps = steps
        self.step_seconds = step_seconds
        self.outcome = outcome
        self.title = f"Demo job ({outcome})"

    def execute(self):
        self.log(f"Starting {self.steps} steps…")
        eta = EtaEstimator(self.steps)
        for i in range(1, self.steps + 1):
            self.check_cancelled()
            time.sleep(self.step_seconds)
            remaining = eta.step()
            self.report_progress(i, self.steps, remaining)

            if i == self.steps // 2:
                self.log("Halfway there")
                if self.outcome == "failure":
                    raise JobFailed("Simulated failure: the input could not be processed.")
                if self.outcome == "error":
                    _ = {}["simulated-missing-key"]  # unexpected exception

        return {"steps": self.steps}
