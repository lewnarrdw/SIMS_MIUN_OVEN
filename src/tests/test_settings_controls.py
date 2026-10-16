"""Tests for process and PID settings reset behavior."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from main import (
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
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
        self.assertFalse(self.window.restore_pid_button.isEnabled())

    def test_elapsed_time_stops_and_resets(self) -> None:
        self.window.start_process()
        self.window.elapsed_s = 12.4
        self.window._update_status()
        self.assertEqual(self.window.elapsed_label.text(), "Elapsed: 00:12.4")

        self.window.stop_process()
        self.assertEqual(self.window.elapsed_label.text(), "Elapsed: 00:12.4")

        self.window.reset_process()
        self.assertEqual(self.window.elapsed_label.text(), "Elapsed: 00:00.0")
    def test_pid_inputs_match_process_input_size_and_use_subscripts(self) -> None:
        process_size = self.window.target_input.size()

        for value_input in (self.window.kp_input, self.window.ki_input, self.window.kd_input):
            self.assertEqual(value_input.size(), process_size)

        self.assertEqual(self.window.kp_enable.text(), "Kp")
        self.assertEqual(self.window.ki_enable.text(), "Ki")
        self.assertEqual(self.window.kd_enable.text(), "Kd")
        for checkbox in (
            self.window.kp_enable,
            self.window.ki_enable,
            self.window.kd_enable,
        ):
            self.assertIn("temperature error in °C", checkbox.toolTip())
            self.assertIn("heater output in %", checkbox.toolTip())
        self.assertEqual(self.window.pid_changes_note.text(), "Changes apply immediately")

    def test_numeric_input_selection_clears_when_focus_leaves(self) -> None:
        for value_input in (
            self.window.target_input,
            self.window.kp_input,
            self.window.ki_input,
            self.window.kd_input,
        ):
            value_input.lineEdit().selectAll()
            QtWidgets.QApplication.sendEvent(
                value_input.lineEdit(),
                QtGui.QFocusEvent(
                    QtCore.QEvent.Type.FocusOut,
                    QtCore.Qt.FocusReason.OtherFocusReason,
                ),
            )
            self.assertFalse(value_input.lineEdit().hasSelectedText())


if __name__ == "__main__":
    unittest.main()
