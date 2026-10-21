"""Tests for faults that interact with the E-Stop and with manual door controls."""

import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import application
import config
from application import Application
from tests.test_emergency_stop import FakeHardware, wait_for


class FaultHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_directory = tempfile.TemporaryDirectory()
        test_config = SimpleNamespace(**{
            name: getattr(config, name) for name in dir(config) if name.isupper()
        })
        test_config.DATA_DIRECTORY = self.data_directory.name
        test_config.HEATING_CHECK_WINDOW_S = 0.5
        test_config.LOOP_PERIOD_S = 0.1
        test_config.DOOR_TIMEOUT_S = 0.3
        self.hardware = FakeHardware()
        self.hardware.motor.timeout_s = test_config.DOOR_TIMEOUT_S
        with mock.patch.object(application, "create_hardware", return_value=self.hardware):
            self.app = Application(test_config)
        self.assertTrue(wait_for(lambda: self.state() == "ready"))

    def tearDown(self) -> None:
        self.app.close()
        self.data_directory.cleanup()

    def state(self) -> str:
        return self.app.snapshot()[0].state

    def detail(self) -> str:
        return self.app.snapshot()[0].detail

    def set_door(self, door: str) -> None:
        self.hardware.door_open.is_active = door == "open"
        self.hardware.door_closed.is_active = door == "closed"

    def cycle_estop(self) -> None:
        self.hardware.estop_engaged = True
        self.assertTrue(wait_for(lambda: self.state() == "e_stop"))
        self.hardware.estop_engaged = False
        self.assertTrue(wait_for(lambda: self.state() != "e_stop"))

    def enter_door_timeout_fault(self) -> None:
        """Start with the door open; the fake door never moves, so closing times out."""
        self.set_door("open")
        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].can_start))
        self.app.command("start")
        self.assertTrue(wait_for(lambda: self.state() == "fault"))
        self.assertEqual(self.detail(), "door_motor_timeout")

    def test_estop_does_not_clear_a_latched_heater_fault(self) -> None:
        self.app.command("start")
        self.assertTrue(wait_for(lambda: self.state() == "fault"))
        self.assertEqual(self.detail(), "heater_not_heating")

        self.cycle_estop()

        self.assertEqual(self.state(), "fault")
        self.assertEqual(self.detail(), "heater_not_heating")
        self.assertFalse(wait_for(lambda: self.state() != "fault", timeout_s=0.5))
        self.app.command("reset")
        self.assertTrue(wait_for(lambda: self.state() == "ready"))

    def test_estop_does_not_clear_a_door_position_fault(self) -> None:
        self.enter_door_timeout_fault()
        # Clear the motor's own fault so only the door position keeps the fault active.
        self.app.command("open")
        self.assertTrue(wait_for(lambda: self.hardware.motor.state.value == "idle"))

        self.cycle_estop()

        self.assertEqual(self.state(), "fault")
        self.assertEqual(self.detail(), "door_motor_timeout")
        self.set_door("closed")
        self.assertTrue(wait_for(lambda: self.state() == "ready"))

    def test_estop_from_a_fault_engages_and_releases(self) -> None:
        self.hardware.sensor.temperature_c = config.MAX_TEMPERATURE + 5.0
        self.assertTrue(wait_for(lambda: self.state() == "fault"))

        self.hardware.estop_engaged = True

        self.assertTrue(wait_for(lambda: self.state() == "e_stop"))
        self.assertTrue(self.app.snapshot()[0].estop_active)
        self.hardware.sensor.temperature_c = 22.0
        self.hardware.estop_engaged = False
        self.assertTrue(wait_for(lambda: self.state() == "ready"))

    def test_door_can_be_closed_manually_during_a_fault(self) -> None:
        self.enter_door_timeout_fault()

        self.app.command("close")

        self.assertTrue(wait_for(lambda: self.hardware.motor_in2.is_active))
        self.assertEqual(self.hardware.motor.state.value, "closing")
        self.set_door("closed")
        self.assertTrue(wait_for(lambda: self.state() == "ready"))
        self.assertFalse(self.hardware.motor_in2.is_active)

    def test_door_can_be_opened_manually_during_a_fault(self) -> None:
        self.hardware.sensor.temperature_c = config.MAX_TEMPERATURE + 5.0
        self.assertTrue(wait_for(lambda: self.state() == "fault"))

        self.app.command("open")

        self.assertTrue(wait_for(lambda: self.hardware.motor_in1.is_active))
        self.assertEqual(self.state(), "fault")
        rejected = [event for event in self.app.snapshot()[0].events if event["type"] == "command_rejected"]
        self.assertEqual(rejected, [])

    def test_start_and_heater_stay_blocked_during_a_fault(self) -> None:
        self.hardware.sensor.temperature_c = config.MAX_TEMPERATURE + 5.0
        self.assertTrue(wait_for(lambda: self.state() == "fault"))

        self.app.command("start")
        self.app.command("heater_on")

        self.assertTrue(wait_for(lambda: len([
            event for event in self.app.snapshot()[0].events if event["type"] == "command_rejected"
        ]) == 2))
        self.assertEqual(self.state(), "fault")
        self.assertEqual(self.hardware.heater.percent, 0.0)


if __name__ == "__main__":
    unittest.main()
