"""Tests for process and PID settings reset behavior."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets, QtTest

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

    def test_clicking_outside_hold_field_clears_editor_focus(self) -> None:
        self.window.show()
        self.app.processEvents()
        editor = self.window.hold_input.lineEdit()
        editor.setFocus()
        self.app.processEvents()
        self.assertTrue(editor.hasFocus())

        QtTest.QTest.mouseClick(
            self.window.stage_label,
            QtCore.Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()

        self.assertFalse(editor.hasFocus())

    def test_hold_single_field_tabs_between_segments_and_accepts_digits(self) -> None:
        hold_input = self.window.hold_input
        self.window.show()
        self.app.processEvents()
        hold_input.setValue(62)

        editor = hold_input.lineEdit()
        QtTest.QTest.mouseClick(editor, QtCore.Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(editor.hasFocus())
        self.assertFalse(editor.isReadOnly())
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Tab)
        self.assertEqual(editor.selectionStart(), editor.text().index(":") + 1)
        QtTest.QTest.keyClicks(editor, "45")
        self.app.processEvents()
        self.assertEqual(hold_input.value(), 105)
        self.assertEqual(editor.text(), "1:45")

        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Backtab)
        self.assertEqual(editor.selectionStart(), 0)
        QtTest.QTest.keyClicks(editor, "2")
        self.app.processEvents()
        self.assertEqual(hold_input.value(), 120)
        self.assertEqual(editor.text(), "2:00")
        QtTest.QTest.keyClicks(editor, "1")
        self.assertEqual(hold_input.value(), 60)
        self.assertEqual(editor.text(), "1:00")

        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Tab)
        QtTest.QTest.keyClicks(editor, "05")
        self.assertEqual(hold_input.value(), 65)
        self.assertEqual(editor.text(), "1:05")

        hold_input.setValue(34)
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Backtab)
        QtTest.QTest.keyClicks(editor, "1")
        self.assertEqual(hold_input.value(), 94)
        self.assertEqual(editor.text(), "1:34")

        hold_input.setValue(71)
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Tab)
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Backtab)
        QtTest.QTest.keyClicks(editor, "2")
        self.assertEqual(hold_input.value(), 120)
        self.assertEqual(editor.text(), "2:00")
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Backspace)
        self.assertEqual(hold_input.value(), 1)
        self.assertEqual(editor.text(), "0:01")

        hold_input.setValue(60)
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Backspace)
        self.assertEqual(hold_input.value(), 1)
        self.assertEqual(editor.text(), "0:01")

        QtWidgets.QApplication.clipboard().setText("1:23")
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_V, QtCore.Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(hold_input.value(), 83)
        QtWidgets.QApplication.clipboard().setText("2:00001")
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_V, QtCore.Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(hold_input.value(), 83)

    def test_hold_single_field_formats_seconds_and_steps_across_minute_boundaries(self) -> None:
        hold_input = self.window.hold_input
        hold_input.setValue(5)
        self.assertEqual(hold_input.lineEdit().text(), "0:05")
        hold_input.setValue(59)
        hold_input._set_active_segment("seconds", select=True)
        hold_input.stepBy(1)
        self.assertEqual(hold_input.value(), 60)
        self.assertEqual(hold_input.lineEdit().text(), "1:00")
        hold_input.setValue(120)
        hold_input.stepBy(-1)
        self.assertEqual(hold_input.value(), 119)
        self.assertEqual(hold_input.lineEdit().text(), "1:59")
        hold_input._set_active_segment("minutes", select=True)
        hold_input.stepBy(1)
        self.assertEqual(hold_input.value(), 120)
        hold_input.stepBy(-1)
        self.assertEqual(hold_input.value(), 60)


if __name__ == "__main__":
    unittest.main()
