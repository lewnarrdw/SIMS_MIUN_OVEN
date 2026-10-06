"""Tests for process and PID settings reset behavior."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from main import (
    DEFAULT_HOLD_DURATION_S,
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_TARGET_TEMPERATURE_C,
    OvenWindow,
)


class SettingsControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.window = OvenWindow()
        self.window.timer.stop()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def test_pid_defaults_can_reset_while_running(self) -> None:
        self.window.start_process()
        self.window.kp_input.setValue(0.5)
        self.window.restore_pid_defaults()

        self.assertEqual(self.window.kp_input.value(), DEFAULT_KP)
        self.assertEqual(self.window.ki_input.value(), DEFAULT_KI)
        self.assertEqual(self.window.kd_input.value(), DEFAULT_KD)

    def test_process_controls_are_disabled_while_running(self) -> None:
        self.window.start_process()

        self.assertFalse(self.window.target_input.isEnabled())
        self.assertFalse(self.window.hold_input.isEnabled())
        self.assertFalse(self.window.restore_process_button.isEnabled())

    def test_elapsed_time_stops_and_resets(self) -> None:
        self.window.start_process()
        self.window.elapsed_s = 12.4
        self.window._update_status()
        self.assertEqual(self.window.elapsed_label.text(), "Elapsed: 00:12.4")

        self.window.stop_process()
        self.assertEqual(self.window.elapsed_label.text(), "Elapsed: 00:12.4")

        self.window.reset_process()
        self.assertEqual(self.window.elapsed_label.text(), "Elapsed: 00:00.0")
    def test_process_defaults_restore_when_stopped(self) -> None:
        self.window.target_input.setValue(55.0)
        self.window.hold_input.setValue(90.0)
        self.window.restore_process_defaults()

        self.assertEqual(self.window.target_input.value(), DEFAULT_TARGET_TEMPERATURE_C)
        self.assertEqual(self.window.hold_input.value(), DEFAULT_HOLD_DURATION_S)


if __name__ == "__main__":
    unittest.main()