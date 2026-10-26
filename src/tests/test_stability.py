"""Tests for settling detection before the hold stage."""

import math
import unittest

import config
from control.controller import Controller, State
from control.stability import StabilityDetector
from tests.test_emergency_stop import FakeHardware

TARGET = 40.0


def feed(detector: StabilityDetector, values, start_s: float = 0.0, period_s: float = 0.1) -> bool:
    """Feed one reading per period; return whether the last one reported settled."""
    settled = False
    for index, value in enumerate(values):
        settled = detector.update(start_s + index * period_s, value, TARGET)
    return settled


class StabilityDetectorTests(unittest.TestCase):
    def detector(self) -> StabilityDetector:
        return StabilityDetector(tolerance=0.1, window_s=10.0, in_band_share=0.9)

    def test_steady_temperature_settles_after_a_full_window(self) -> None:
        detector = self.detector()

        self.assertFalse(feed(detector, [TARGET] * 99))  # 9.8 s: not a full window yet
        self.assertTrue(detector.update(10.0, TARGET, TARGET))

    def test_a_stray_reading_does_not_restart_the_wait(self) -> None:
        detector = self.detector()
        readings = [TARGET] * 101
        readings[60] = TARGET + 0.5

        self.assertTrue(feed(detector, readings))

    def test_oscillation_around_the_target_does_not_settle(self) -> None:
        detector = self.detector()
        # Centred on the target (so the median passes) but mostly outside the band.
        readings = [TARGET + 0.3 * math.sin(index / 5) for index in range(300)]

        self.assertFalse(feed(detector, readings))

    def test_offset_temperature_does_not_settle(self) -> None:
        detector = self.detector()

        self.assertFalse(feed(detector, [TARGET + 0.15] * 300))

    def test_drift_through_the_band_does_not_settle(self) -> None:
        detector = self.detector()
        # 0.05 °C per second: crosses the 0.2 °C wide band in about 4 s of the 10 s window.
        readings = [TARGET - 0.4 + 0.005 * index for index in range(200)]

        self.assertFalse(feed(detector, readings))

    def test_reset_starts_a_new_window(self) -> None:
        detector = self.detector()
        feed(detector, [TARGET] * 101)

        detector.reset()

        self.assertFalse(detector.update(20.0, TARGET, TARGET))

    def test_progress_counts_seconds_in_the_band(self) -> None:
        detector = self.detector()
        feed(detector, [TARGET + 0.5] * 50 + [TARGET] * 51)

        self.assertAlmostEqual(detector.settled_s(TARGET), 5.1, delta=0.2)


class ControllerHoldDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hardware = FakeHardware()
        self.controller = Controller(self.hardware, config)
        self.controller.start(0.0, TARGET, 60.0, (config.PID_KP, config.PID_KI, config.PID_KD), TARGET)
        self.period = config.SENSOR_PERIOD_S

    def step_for(self, start_s: float, seconds: float, temperature) -> float:
        """Step at the sensor rate; temperature may be a value or a function of time."""
        now = start_s
        while now < start_s + seconds:
            value = temperature(now) if callable(temperature) else temperature
            self.controller.step(now, value, 0.0, False, True)
            if self.controller.state != State.HEATING:
                break
            now += self.period
        return now

    def test_hold_starts_after_the_stability_window(self) -> None:
        self.step_for(0.0, config.STABLE_TIME_S - 1.0, TARGET)
        self.assertEqual(self.controller.state, State.HEATING)

        self.step_for(config.STABLE_TIME_S - 1.0, 2.0, TARGET)

        self.assertEqual(self.controller.state, State.HOLDING)

    def test_quantization_flicker_at_the_band_edge_still_settles(self) -> None:
        # The MAX31865 steps in 0.034 °C; readings flick across the band edge.
        flicker = lambda now: TARGET + (0.102 if int(now * 15) % 20 == 0 else 0.068)

        self.step_for(0.0, config.STABLE_TIME_S + 1.0, flicker)

        self.assertEqual(self.controller.state, State.HOLDING)

    def test_slow_oscillation_never_starts_the_hold(self) -> None:
        swing = lambda now: TARGET + 0.25 * math.sin(2 * math.pi * now / 90.0)

        self.step_for(0.0, 3 * config.STABLE_TIME_S, swing)

        self.assertEqual(self.controller.state, State.HEATING)

    def test_progress_shows_settled_seconds(self) -> None:
        self.step_for(0.0, 30.0, TARGET)

        self.assertRegex(self.controller.progress_detail, rf"^Stable \d+/{config.STABLE_TIME_S:.0f} s$")

    def test_stopping_clears_the_stability_window(self) -> None:
        self.step_for(0.0, config.STABLE_TIME_S - 5.0, TARGET)
        self.controller.stop(config.STABLE_TIME_S)

        self.controller.start(200.0, TARGET, 60.0, (config.PID_KP, config.PID_KI, config.PID_KD), TARGET)
        self.step_for(200.0, 10.0, TARGET)

        self.assertEqual(self.controller.state, State.HEATING)


if __name__ == "__main__":
    unittest.main()
