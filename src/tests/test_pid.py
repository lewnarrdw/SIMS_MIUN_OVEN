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
    def test_feed_forward_matches_the_measured_hold_output(self) -> None:
        # Holding 30 °C in a 20.1 °C room took 24 % on average (kiln-run-20261026-123831).
        self.assertAlmostEqual(config.FF_PERCENT_PER_DEGREE * (30.0 - 20.1), 24.0, delta=0.5)

    def test_default_is_a_pi_controller(self) -> None:
        self.assertGreater(config.PID_KP, 0.0)
        self.assertGreater(config.PID_KI, 0.0)
        self.assertEqual(config.PID_KD, 0.0)

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

    def test_derivative_is_off_by_default(self) -> None:
        self.assertEqual(config.PID_KD, 0.0)
        self.assertFalse(self.window.kd_enable.isChecked())
        self.assertFalse(self.window.kd_input.isEnabled())
        self.assertTrue(self.window.kp_enable.isChecked())
        self.assertTrue(self.window.ki_enable.isChecked())
        self.assertEqual(self.window._settings()["kd"], 0.0)

    def test_restoring_defaults_switches_derivative_off(self) -> None:
        self.window.kd_enable.setChecked(True)
        self.window.kd_input.setValue(100.0)

        self.window.restore_pid_defaults()

        self.assertFalse(self.window.kd_enable.isChecked())
        self.assertEqual(self.window._settings()["kd"], 0.0)

    def test_each_input_shows_its_range(self) -> None:
        hints = {value_input: hint.text() for value_input, hint in self.window.range_hints.items()}

        self.assertEqual(hints[self.window.target_input],
                         f"{config.MIN_SETPOINT:g}–{config.MAX_SETPOINT:g}")
        self.assertEqual(hints[self.window.hold_input], "0:01–2:00")
        self.assertEqual(hints[self.window.kp_input], "0–200")
        self.assertEqual(hints[self.window.ki_input], "0–5")
        self.assertEqual(hints[self.window.kd_input], "0–2000")

    def test_range_hints_match_what_the_inputs_accept(self) -> None:
        for value_input in (self.window.target_input, self.window.kp_input, self.window.ki_input):
            value_input.setValue(value_input.maximum() + 1000.0)
            low, high = self.window.range_hints[value_input].text().split("–")
            self.assertEqual(value_input.value(), float(high))
            value_input.setValue(-1000.0)
            self.assertEqual(value_input.value(), float(low))


if __name__ == "__main__":
    unittest.main()
