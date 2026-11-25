"""Rolling-average ETA estimator (used by the video slicer in milestone 5)."""
from __future__ import annotations

import time
from collections import deque


class EtaEstimator:
    """Estimate time remaining from the duration of the last ``window`` steps."""

    def __init__(self, total: int, window: int = 10) -> None:
        self.total = total
        self._times: deque[float] = deque(maxlen=window)
        self._last = time.monotonic()
        self.done = 0

    def step(self, n: int = 1) -> float:
        """Record ``n`` completed steps; return seconds remaining (-1 if unknown)."""
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        for _ in range(n):
            self._times.append(elapsed / n)
        self.done += n
        return self.remaining()

    def remaining(self) -> float:
        if not self._times:
            return -1.0
        avg = sum(self._times) / len(self._times)
        return max(0.0, avg * (self.total - self.done))


def format_duration(seconds: float) -> str:
    """Format seconds as '1 h 02 min', '2 min 05 s' or '12 s'."""
    if seconds < 0:
        return "estimating…"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"
