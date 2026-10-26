"""Decides when the oven temperature has settled at its target."""
from collections import deque
import statistics


class StabilityDetector:
    """Settled means the last window_s of readings sit at the target.

    Their median must lie within tolerance of the target and at least in_band_share
    of them inside the band. A stray reading cannot restart the wait, while an
    oscillation or a slow drift through the band does not pass.
    """

    def __init__(self, tolerance: float, window_s: float, in_band_share: float) -> None:
        self.tolerance, self.window_s, self.in_band_share = tolerance, window_s, in_band_share
        self.readings: deque[tuple[float, float]] = deque()
        self.started: float | None = None

    def reset(self) -> None:
        self.readings.clear()
        self.started = None

    def update(self, now: float, temperature: float, target: float) -> bool:
        """Add a reading; True once the full window shows a settled temperature."""
        if self.started is None:
            self.started = now
        self.readings.append((now, temperature))
        while now - self.readings[0][0] > self.window_s:
            self.readings.popleft()
        if now - self.started < self.window_s:
            return False  # Not a full window of readings yet.
        values = [value for _, value in self.readings]
        inside = sum(abs(value - target) <= self.tolerance for value in values)
        return (abs(statistics.median(values) - target) <= self.tolerance
                and inside >= self.in_band_share * len(values))

    def settled_s(self, target: float) -> float:
        """Seconds of the window inside the band, for progress display."""
        if len(self.readings) < 2:
            return 0.0
        span = self.readings[-1][0] - self.readings[0][0]
        inside = sum(abs(value - target) <= self.tolerance for _, value in self.readings)
        return span * inside / len(self.readings)
