"""Tests for the PID's integral zone, anti-windup and tuned defaults."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

import config
from control.pid import PID
from main import KilnWindow
from tests.test_emergency_stop import FakeService


class IntegralZoneTests(unittest.TestCase):
    def test_no_integration_outside_the_zone(self) -> None:
        pid = PID(0.0, 0.5, 0.0, integral_zone=1.0)

        pid.update(40.0, 35.0, 1.0)

        self.assertEqual(pid.integral, 0.0)

    def test_integration_inside_the_zone(self) -> None:
        pid = PID(0.0, 0.5, 0.0, integral_zone=1.0)

        pid.update(40.0, 39.5, 1.0)

        self.assertAlmostEqual(pid.integral, 0.25)

    def test_without_a_zone_the_integral_always_accumulates(self) -> None:
        pid = PID(0.0, 0.5, 0.0)

        pid.update(40.0, 35.0, 1.0)

        self.assertAlmostEqual(pid.integral, 2.5)


class AntiWindupTests(unittest.TestCase):
    def test_full_power_heat_up_does_not_drive_the_integral_negative(self) -> None:
        # Back-calculation used to pull the integral to -100 here, starving the heater later.
        pid = PID(33.0, 0.183, 0.0)
        pid.integral = 3.0

        for _ in range(300):
            self.assertEqual(pid.update(40.0, 25.0, 1.0, feedforward=42.0), 100.0)

        self.assertEqual(pid.integral, 3.0)

    def test_output_leaves_the_limit_as_soon_as_the_error_shrinks(self) -> None:
        pid = PID(33.0, 0.183, 0.0)
        for _ in range(300):
            pid.update(40.0, 25.0, 1.0, feedforward=42.0)

        # 1.5 °C below target: P adds 49.5 % to the 42 % feed-forward.
        self.assertAlmostEqual(pid.update(40.0, 38.5, 1.0, feedforward=42.0), 91.5, delta=0.5)

    def test_saturated_low_does_not_wind_down(self) -> None:
        pid = PID(33.0, 0.183, 0.0)

        for _ in range(300):
            self.assertEqual(pid.update(40.0, 45.0, 1.0, feedforward=10.0), 0.0)

        self.assertEqual(pid.integral, 0.0)


class TunedDefaultTests(unittest.TestCase):
    def test_feed_forward_matches_the_fitted_oven_gain(self) -> None:
        # The run fit gave 0.47 °C per % output: holding 10 °C above room needs about 21 %.
        self.assertAlmostEqual(config.FF_PERCENT_PER_DEGREE * 10.0, 10.0 / 0.473, delta=0.5)

    def test_controller_uses_the_integral_zone(self) -> None:
        from control.controller import Controller
        from tests.test_emergency_stop import FakeHardware

        controller = Controller(FakeHardware(), config)

        self.assertEqual(controller.pid.integral_zone, config.PID_INTEGRAL_ZONE_C)


class PidInputRangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.window = KilnWindow(FakeService())
        self.window._timer.stop()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def test_inputs_start_at_the_tuned_gains(self) -> None:
        self.assertAlmostEqual(self.window.kp_input.value(), config.PID_KP)
        self.assertAlmostEqual(self.window.ki_input.value(), config.PID_KI)
        self.assertAlmostEqual(self.window.kd_input.value(), config.PID_KD)

    def test_inputs_accept_gains_well_above_the_tuned_ones(self) -> None:
        for value_input, value in ((self.window.kp_input, 3 * config.PID_KP),
                                   (self.window.ki_input, 3 * config.PID_KI),
                                   (self.window.kd_input, 2 * config.PID_KD)):
            value_input.setValue(value)
            self.assertAlmostEqual(value_input.value(), value, places=1)


if __name__ == "__main__":
    unittest.main()
