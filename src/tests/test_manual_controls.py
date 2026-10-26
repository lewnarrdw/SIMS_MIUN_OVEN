"""Tests for when the Manual controls menu items are available."""

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
from main import KilnWindow
from tests.test_emergency_stop import FakeHardware, FakeService, wait_for

LIGHT_TESTS = ["Test all lights", "Test red light (GPIO17)", "Test orange light (GPIO24)",
               "Test yellow light (GPIO22)", "Test green light (GPIO23)"]
NO_LIGHT_TESTS = dict.fromkeys(LIGHT_TESTS, False)
ALL_ENABLED = {"Open door": True, "Close door": True, "Test buzzer": True,
               "Heater on": True, "Heater off": True} | dict.fromkeys(LIGHT_TESTS, True)


class ManualControlsAvailabilityTests(unittest.TestCase):
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

    def availability(self, **changes) -> dict[str, bool]:
        self.service.view = replace(self.service.view, **changes)
        self.window.refresh()
        return {label: action.isEnabled() for label, action in self.window.manual_actions.items()}

    def test_everything_is_available_while_idle(self) -> None:
        self.assertEqual(self.availability(), ALL_ENABLED)

    def test_door_and_heater_on_are_greyed_out_during_a_run(self) -> None:
        self.assertEqual(self.availability(running=True, state="heating"),
                         ALL_ENABLED | {"Open door": False, "Close door": False, "Heater on": False}
                         | NO_LIGHT_TESTS)

    def test_door_buttons_are_greyed_out_while_the_door_opens(self) -> None:
        self.assertEqual(self.availability(door_motor="opening", door="between"),
                         ALL_ENABLED | {"Open door": False, "Close door": False})

    def test_door_buttons_are_greyed_out_while_the_door_closes(self) -> None:
        self.assertEqual(self.availability(door_motor="closing", door="between"),
                         ALL_ENABLED | {"Open door": False, "Close door": False})

    def test_door_buttons_return_when_the_door_stops(self) -> None:
        self.availability(door_motor="opening", door="between")

        self.assertEqual(self.availability(door_motor="idle", door="open"), ALL_ENABLED)

    def test_door_stays_usable_but_heater_on_and_light_tests_are_greyed_out_during_a_fault(self) -> None:
        self.assertEqual(self.availability(state="fault", detail="over_temperature"),
                         ALL_ENABLED | {"Heater on": False} | NO_LIGHT_TESTS)
        self.assertFalse(self.window.light_tower_menu.isEnabled())

    def test_light_tower_menu_returns_after_a_run(self) -> None:
        self.availability(running=True, state="heating")

        self.assertEqual(self.availability(running=False, state="complete"), ALL_ENABLED)
        self.assertTrue(self.window.light_tower_menu.isEnabled())

    def test_heater_on_is_greyed_out_during_estop(self) -> None:
        availability = self.availability(state="e_stop", estop_active=True)

        self.assertFalse(availability["Heater on"])
        self.assertFalse(self.window.manual_button.isEnabled())

    def test_greyed_out_door_button_sends_no_command(self) -> None:
        self.availability(door_motor="closing", door="between")
        self.service.commands.clear()

        self.window.manual_actions["Open door"].trigger()

        self.assertEqual(self.service.commands, [])


class DoorMovingCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_directory = tempfile.TemporaryDirectory()
        test_config = SimpleNamespace(**{
            name: getattr(config, name) for name in dir(config) if name.isupper()
        })
        test_config.DATA_DIRECTORY = self.data_directory.name
        self.hardware = FakeHardware()
        with mock.patch.object(application, "create_hardware", return_value=self.hardware):
            self.app = Application(test_config)
        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].state == "ready"))

    def tearDown(self) -> None:
        self.app.close()
        self.data_directory.cleanup()

    def test_view_reports_the_door_motor_state(self) -> None:
        self.assertEqual(self.app.snapshot()[0].door_motor, "idle")

        self.app.command("open")

        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].door_motor == "opening"))

    def test_physical_door_button_cannot_reverse_a_moving_door(self) -> None:
        self.app.command("open")
        self.assertTrue(wait_for(lambda: self.hardware.motor.state.value == "opening"))

        self.app.command("close")

        rejected = lambda: [event for event in self.app.snapshot()[0].events
                            if event["type"] == "command_rejected"]
        self.assertTrue(wait_for(lambda: len(rejected()) == 1))
        self.assertEqual(rejected()[0]["description"],
                         "Close door rejected: The door is already moving; wait for it to stop")
        self.assertEqual(self.hardware.motor.state.value, "opening")
        self.assertTrue(self.hardware.motor_in1.is_active)
        self.assertFalse(self.hardware.motor_in2.is_active)


if __name__ == "__main__":
    unittest.main()
