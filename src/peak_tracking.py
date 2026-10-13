"""Track the highest temperature measured during an oven run."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PeakTracker:
    """Keep the peak temperature and when it was measured."""

    peak_temperature_c: float | None = None
    peak_time_s: float | None = None

    def update(self, temperature_c: float, elapsed_s: float) -> bool:
        """Record a sample; return True if it establishes a new peak."""
        if self.peak_temperature_c is None or temperature_c > self.peak_temperature_c:
            self.peak_temperature_c = temperature_c
            self.peak_time_s = elapsed_s
            return True
        return False

    def annotation(self, target_temperature_c: float) -> str:
        """Format the completed-run peak annotation."""
        if self.peak_temperature_c is None or self.peak_time_s is None:
            raise ValueError("No temperature samples have been recorded")

        difference_c = self.peak_temperature_c - target_temperature_c
        sign = "+" if difference_c >= 0 else "−"
        minutes, seconds = divmod(self.peak_time_s, 60.0)
        return (
            f"Peak {self.peak_temperature_c:.2f} °C at "
            f"{int(minutes):02d}:{seconds:04.1f} "
            f"({sign}{abs(difference_c):.2f} °C vs target)"
        )
