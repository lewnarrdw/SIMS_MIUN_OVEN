"""Tests for the Manual controls buzzer test."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

import application
import config
from application import Application
from hardware.actuators import SignalTower
from main import KilnWindow
from tests.test_emergency_stop import FakeHardware, FakeOutput, FakeService, wait_for


def make_tower() -> SignalTower:
    tower = SignalTower({name: FakeOutput() for name in config.TOWER_PINS})
    tower.show("ready")
    return tower


def lit(tower: SignalTower) -> set[str]:
    return {name for name, output in tower.outputs.items() if output.is_active}


class SignalTowerBuzzerTests(unittest.TestCase):
    def test_buzzer_test_sounds_until_duration_ends(self) -> None:
        tower = make_tower()

        tower.test_buzzer(10.0, 1.0)
        self.assertEqual(lit(tower), {"green", "buzzer"})
        tower.update(10.9)
        self.assertIn("buzzer", lit(tower))
        tower.update(11.0)

        self.assertEqual(lit(tower), {"green"})

    def test_state_change_during_test_keeps_buzzer_on(self) -> None:
        tower = make_tower()
        tower.test_buzzer(0.0, 1.0)

        tower.show("heating")

        # Green and orange blink while warming up and are in the on half of their blink at whole seconds.
        self.assertEqual(lit(tower), {"green", "orange", "buzzer"})
        tower.update(1.0)
        self.assertEqual(lit(tower), {"green", "orange"})

    def test_e_stop_silences_buzzer_even_during_buzzer_test(self) -> None:
        tower = make_tower()
        tower.show("e_stop")
        tower.test_buzzer(0.0, 1.0)

        tower.update(2.0)

        self.assertEqual(lit(tower), {"red"})

    def test_repeated_buzzer_test_restarts_the_duration(self) -> None:
        tower = make_tower()
        tower.test_buzzer(0.0, 1.0)
        tower.test_buzzer(0.8, 1.0)

        tower.update(1.2)
        self.assertIn("buzzer", lit(tower))
        tower.update(1.8)
        self.assertNotIn("buzzer", lit(tower))

    def test_update_without_test_leaves_outputs_unchanged(self) -> None:
        tower = make_tower()

        tower.update(100.0)

        self.assertEqual(lit(tower), {"green"})


class ApplicationBuzzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_directory = tempfile.TemporaryDirectory()
        test_config = SimpleNamespace(**{
            name: getattr(config, name) for name in dir(config) if name.isupper()
        })
        test_config.DATA_DIRECTORY = self.data_directory.name
        test_config.BUZZER_TEST_S = 0.3
        self.hardware = FakeHardware()
        with mock.patch.object(application, "create_hardware", return_value=self.hardware):
            self.app = Application(test_config)

    def tearDown(self) -> None:
        self.app.close()
        self.data_directory.cleanup()

    def buzzer_on(self) -> bool:
        return self.hardware.tower.outputs["buzzer"].is_active

    def test_test_buzzer_command_sounds_then_silences_buzzer(self) -> None:
        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].state == "ready"))

        self.app.command("test_buzzer")

        self.assertTrue(wait_for(self.buzzer_on))
        self.assertTrue(wait_for(lambda: not self.buzzer_on()))
        self.assertEqual(self.app.snapshot()[0].state, "ready")
        self.assertEqual(self.app.snapshot()[0].error, "")


class ManualControlsMenuTests(unittest.TestCase):
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

    def test_menu_offers_test_buzzer_instead_of_read_temperature(self) -> None:
        labels = [action.text() for action in self.window.manual_menu.actions()]

        self.assertIn("Test buzzer", labels)
        self.assertNotIn("Read temperature", labels)

    def test_test_buzzer_sends_the_buzzer_command(self) -> None:
        self.service.commands.clear()

        self.window.manual_actions["Test buzzer"].trigger()

        self.assertEqual(self.service.commands, ["test_buzzer"])


if __name__ == "__main__":
    unittest.main()
