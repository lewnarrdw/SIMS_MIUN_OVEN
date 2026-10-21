"""Tests for the target temperature shown below the temperature readout."""

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


class ApplicationStartTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_directory = tempfile.TemporaryDirectory()
        test_config = SimpleNamespace(**{
            name: getattr(config, name) for name in dir(config) if name.isupper()
        })
        test_config.DATA_DIRECTORY = self.data_directory.name
        self.hardware = FakeHardware()
        with mock.patch.object(application, "create_hardware", return_value=self.hardware):
            self.app = Application(test_config)

    def tearDown(self) -> None:
        self.app.close()
        self.data_directory.cleanup()

    def settings(self, target_c: float) -> dict:
        return dict(target_temperature_c=target_c, hold_duration_s=60.0,
                    kp=config.PID_KP, ki=config.PID_KI, kd=config.PID_KD)

    def test_view_reports_the_start_target_before_any_run(self) -> None:
        self.assertEqual(self.app.snapshot()[0].start_target_c, 45.0)
        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].state == "ready"))

        self.assertEqual(self.app.snapshot()[0].start_target_c, 45.0)

    def test_start_target_follows_the_settings_command(self) -> None:
        self.app.command("settings", self.settings(40.0))

        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].start_target_c == 40.0))

    def test_start_target_is_reported_during_estop(self) -> None:
        self.hardware.estop_engaged = True

        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].state == "e_stop"))
        self.assertEqual(self.app.snapshot()[0].start_target_c, 45.0)


class TargetLabelTests(unittest.TestCase):
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

    def show_view(self, **changes) -> str:
        self.service.view = replace(self.service.view, **changes)
        self.window.refresh()
        return self.window.target_status_label.text()

    def test_label_shows_start_target_while_ready(self) -> None:
        text = self.show_view(target_c=config.MIN_SETPOINT, start_target_c=45.0)

        self.assertEqual(text, "Target 45.0 °C")

    def test_label_shows_start_target_during_estop(self) -> None:
        text = self.show_view(state="e_stop", estop_active=True,
                              target_c=config.MIN_SETPOINT, start_target_c=45.0)

        self.assertEqual(text, "Target 45.0 °C")

    def test_label_shows_run_target_during_a_run(self) -> None:
        text = self.show_view(running=True, state="heating", target_c=42.0, start_target_c=45.0)

        self.assertEqual(text, "Target 42.0 °C")

    def test_label_falls_back_to_controller_target_without_start_target(self) -> None:
        text = self.show_view(target_c=38.0, start_target_c=None)

        self.assertEqual(text, "Target 38.0 °C")


if __name__ == "__main__":
    unittest.main()
