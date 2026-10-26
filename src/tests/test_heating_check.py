"""Tests for detecting a heater or temperature probe that does not respond during warm-up."""

import math
import os
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

import application
import config
from application import Application
from control.controller import Controller, State
from main import KilnWindow
from tests.test_emergency_stop import FakeHardware, FakeService, wait_for

WINDOW_S = config.HEATING_CHECK_WINDOW_S
MIN_RISE_C = config.HEATING_CHECK_MIN_RISE_C


class HeatingCheckControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hardware = FakeHardware()
        self.controller = Controller(self.hardware, config)

    def run_warm_up(self, start_c: float, rise_c_per_s: float, duration_s: float,
                    target_c: float = 45.0) -> None:
        """Step the controller once per second with a linear temperature trace."""
        self.controller.start(0.0, target_c, 60.0, (config.PID_KP, config.PID_KI, config.PID_KD), start_c)
        for second in range(int(duration_s) + 1):
            temperature = start_c + rise_c_per_s * second
            self.controller.step(float(second), temperature, 0.0, False, True)
            if self.controller.state != State.HEATING:
                return

    def test_flat_temperature_at_full_output_faults_after_window(self) -> None:
        self.run_warm_up(22.0, 0.0, WINDOW_S + 5)

        self.assertEqual(self.controller.state, State.FAULT)
        self.assertEqual(self.controller.fault_reason, "heater_not_heating")
        self.assertTrue(self.hardware.heater.locked)
        self.assertEqual(self.hardware.heater.percent, 0.0)

    def test_no_fault_before_the_window_has_elapsed(self) -> None:
        self.run_warm_up(22.0, 0.0, WINDOW_S - 1)

        self.assertEqual(self.controller.state, State.HEATING)

    def test_rising_temperature_does_not_fault(self) -> None:
        # Stay below the target so the heater output remains in the checked range.
        self.run_warm_up(22.0, 3 * MIN_RISE_C / WINDOW_S, 3 * WINDOW_S)

        self.assertEqual(self.controller.state, State.HEATING)

    def test_rise_just_below_minimum_faults(self) -> None:
        self.run_warm_up(22.0, (MIN_RISE_C * 0.8) / WINDOW_S, WINDOW_S + 1)

        self.assertEqual(self.controller.fault_reason, "heater_not_heating")

    def test_rise_at_minimum_does_not_fault(self) -> None:
        self.run_warm_up(22.0, (MIN_RISE_C * 1.2) / WINDOW_S, WINDOW_S + 1)

        self.assertEqual(self.controller.state, State.HEATING)

    def test_slow_starting_heater_does_not_fault(self) -> None:
        # Heater element lag and probe dead time: almost no rise in the first 20 s.
        gain_c, oven_lag_s, heater_lag_s, dead_time_s = 60.0, 600.0, 60.0, 8.0

        def lagged_rise(elapsed_s: float) -> float:
            t = max(0.0, elapsed_s - dead_time_s)
            return gain_c * (1 - (oven_lag_s * math.exp(-t / oven_lag_s)
                                  - heater_lag_s * math.exp(-t / heater_lag_s)) / (oven_lag_s - heater_lag_s))

        self.assertLess(lagged_rise(20.0), 0.5)
        self.controller.start(0.0, 45.0, 60.0, (config.PID_KP, config.PID_KI, config.PID_KD), 22.0)
        for second in range(int(2 * WINDOW_S)):
            self.controller.step(float(second), 22.0 + lagged_rise(second), 0.0, False, True)

        self.assertEqual(self.controller.state, State.HEATING)

    def test_temperature_stalling_after_rising_faults_in_next_window(self) -> None:
        self.controller.start(0.0, 45.0, 60.0, (config.PID_KP, config.PID_KI, config.PID_KD), 22.0)
        temperature = 22.0
        for second in range(int(3 * WINDOW_S)):
            if second <= WINDOW_S:
                temperature = 22.0 + 0.1 * second
            self.controller.step(float(second), temperature, 0.0, False, True)
            if self.controller.state != State.HEATING:
                break

        self.assertEqual(self.controller.fault_reason, "heater_not_heating")

    def test_low_output_near_target_is_not_checked(self) -> None:
        # Holding 30 °C needs only about 20 % output, so near the target the check stays off.
        self.run_warm_up(29.7, 0.0, 3 * WINDOW_S, target_c=30.0)

        self.assertLess(self.hardware.heater.percent, config.HEATING_CHECK_OUTPUT_PERCENT)
        self.assertEqual(self.controller.state, State.HEATING)

    def test_start_clears_the_latched_heater_fault(self) -> None:
        self.run_warm_up(22.0, 0.0, WINDOW_S + 1)
        self.assertEqual(self.controller.state, State.FAULT)

        self.controller.start(WINDOW_S + 2, 40.0, 60.0, (config.PID_KP, config.PID_KI, config.PID_KD), 22.0)

        self.assertEqual(self.controller.state, State.HEATING)
        self.assertEqual(self.controller.fault_reason, "")

    def test_start_refuses_a_fault_that_is_not_latched(self) -> None:
        self.controller.fault("door_motor_timeout")

        with self.assertRaises(RuntimeError):
            self.controller.start(0.0, 40.0, 60.0, (config.PID_KP, config.PID_KI, config.PID_KD), 22.0)
        self.assertEqual(self.controller.state, State.FAULT)

    def test_failed_start_keeps_the_latched_fault(self) -> None:
        self.run_warm_up(22.0, 0.0, WINDOW_S + 1)

        with self.assertRaises(RuntimeError):
            self.controller.start(WINDOW_S + 2, 40.0, 60.0, (config.PID_KP, config.PID_KI, config.PID_KD), None)
        self.assertEqual(self.controller.state, State.FAULT)
        self.assertEqual(self.controller.fault_reason, "heater_not_heating")

    def test_new_run_starts_a_fresh_check(self) -> None:
        self.run_warm_up(22.0, 0.0, WINDOW_S - 2)
        self.controller.stop(WINDOW_S - 1)

        self.run_warm_up(22.0, 0.0, WINDOW_S - 2)

        self.assertEqual(self.controller.state, State.HEATING)


class HeatingCheckApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_directory = tempfile.TemporaryDirectory()
        test_config = SimpleNamespace(**{
            name: getattr(config, name) for name in dir(config) if name.isupper()
        })
        test_config.DATA_DIRECTORY = self.data_directory.name
        test_config.HEATING_CHECK_WINDOW_S = 0.5
        test_config.LOOP_PERIOD_S = 0.1
        self.hardware = FakeHardware()
        with mock.patch.object(application, "create_hardware", return_value=self.hardware):
            self.app = Application(test_config)
        self.assertTrue(wait_for(lambda: self.state() == "ready"))

    def tearDown(self) -> None:
        self.app.close()
        self.data_directory.cleanup()

    def state(self) -> str:
        return self.app.snapshot()[0].state

    def test_heater_fault_stays_latched_until_a_new_run(self) -> None:
        self.app.command("start")

        self.assertTrue(wait_for(lambda: self.state() == "fault"))
        self.assertEqual(self.app.snapshot()[0].detail, "heater_not_heating")
        self.assertFalse(wait_for(lambda: self.state() != "fault", timeout_s=1.0))
        faulted = [event for event in self.app.snapshot()[0].events if event["type"] == "run_faulted"]
        self.assertEqual(len(faulted), 1)
        self.assertIn("temperature did not rise while heating", faulted[0]["description"])
        self.assertTrue(self.app.snapshot()[0].can_start)

        self.app.command("start")

        self.assertTrue(wait_for(lambda: self.state() == "heating"))
        self.assertFalse(self.hardware.heater.locked)

    def enter_heater_fault(self) -> None:
        self.app.command("start")
        self.assertTrue(wait_for(lambda: self.state() == "fault"))
        self.assertEqual(self.app.snapshot()[0].detail, "heater_not_heating")

    def rejected_starts(self) -> list[dict]:
        return [event for event in self.app.snapshot()[0].events if event["type"] == "command_rejected"]

    def test_start_cannot_clear_the_fault_while_a_live_condition_remains(self) -> None:
        self.enter_heater_fault()
        self.hardware.sensor.seconds_since_valid = lambda now: config.SENSOR_FAULT_TIMEOUT_S + 1.0
        self.assertTrue(wait_for(lambda: not self.app.snapshot()[0].can_start))
        self.assertEqual(self.app.snapshot()[0].ready_reason, "Temperature sensor is not responding")

        self.app.command("start")

        self.assertTrue(wait_for(lambda: len(self.rejected_starts()) == 1))
        self.assertEqual(self.state(), "fault")
        self.assertEqual(self.app.snapshot()[0].detail, "heater_not_heating")
        self.assertTrue(self.hardware.heater.locked)

    def test_heater_on_does_not_clear_the_fault(self) -> None:
        self.enter_heater_fault()

        self.app.command("heater_on")

        self.assertTrue(wait_for(lambda: len(self.rejected_starts()) == 1))
        self.assertEqual(self.state(), "fault")
        self.assertEqual(self.hardware.heater.percent, 0.0)

    def test_manual_heating_faults_on_over_temperature(self) -> None:
        self.app.command("heater_on")
        self.assertTrue(wait_for(lambda: self.hardware.heater.percent == 100.0))

        self.hardware.sensor.temperature_c = config.MAX_TEMPERATURE + 5.0

        self.assertTrue(wait_for(lambda: self.state() == "fault"))
        self.assertEqual(self.app.snapshot()[0].detail, "over_temperature")
        self.assertEqual(self.hardware.heater.percent, 0.0)
        self.assertFalse(self.hardware.heater_output.is_active)

    def test_manual_heating_faults_when_sensor_stops_responding(self) -> None:
        self.app.command("heater_on")
        self.assertTrue(wait_for(lambda: self.hardware.heater.percent == 100.0))

        self.hardware.sensor.seconds_since_valid = lambda now: config.SENSOR_FAULT_TIMEOUT_S + 1.0

        self.assertTrue(wait_for(lambda: self.state() == "fault"))
        self.assertEqual(self.app.snapshot()[0].detail, "sensor_timeout")
        self.assertEqual(self.hardware.heater.percent, 0.0)


class HeatingFaultPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.service = FakeService()
        self.window = KilnWindow(self.service)
        self.window._timer.stop()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def test_heater_fault_asks_for_a_new_run(self) -> None:
        self.service.view = replace(self.service.view, state="fault", detail="heater_not_heating")

        self.window.refresh()

        self.assertEqual(
            self.window.stage_detail_label.text(),
            "Cause: Temperature did not rise while heating (check the heater and temperature probe). "
            "Fix it, then start a new run.",
        )


if __name__ == "__main__":
    unittest.main()
