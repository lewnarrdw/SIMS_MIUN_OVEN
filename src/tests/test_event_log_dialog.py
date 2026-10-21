"""Tests that the run event dialog follows the dark GUI theme on any platform."""

import os
import unittest
from dataclasses import replace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtGui, QtWidgets

from main import KilnWindow
from tests.test_emergency_stop import FakeService


def light_palette() -> QtGui.QPalette:
    """A light system palette like Raspberry Pi OS uses by default."""
    palette = QtGui.QPalette()
    for role in (QtGui.QPalette.ColorRole.Window, QtGui.QPalette.ColorRole.Base,
                 QtGui.QPalette.ColorRole.Button):
        palette.setColor(role, QtGui.QColor("white"))
    for role in (QtGui.QPalette.ColorRole.WindowText, QtGui.QPalette.ColorRole.Text,
                 QtGui.QPalette.ColorRole.ButtonText):
        palette.setColor(role, QtGui.QColor("black"))
    return palette


class EventLogDialogThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.original_palette = self.app.palette()
        self.app.setPalette(light_palette())
        self.window = KilnWindow(FakeService())
        self.window._timer.stop()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.setPalette(self.original_palette)

    def open_event_dialog(self) -> QtWidgets.QDialog:
        opened = []
        with mock.patch.object(QtWidgets.QDialog, "exec", lambda dialog: opened.append(dialog) or 0):
            self.window.show_run_events()
        return opened[0]

    def test_event_dialog_is_dark_with_a_light_system_palette(self) -> None:
        dialog = self.open_event_dialog()
        event_text = dialog.findChild(QtWidgets.QPlainTextEdit)

        dialog_background = dialog.grab().toImage().pixelColor(2, 2)
        text_background = event_text.viewport().grab().toImage().pixelColor(4, 4)

        self.assertLess(dialog_background.lightness(), 60)
        self.assertLess(text_background.lightness(), 60)

    def test_event_severity_tags_use_event_severity_and_older_types(self) -> None:
        self.window.service.view = replace(self.window.service.view, events=(
            {"elapsed_s": 1.0, "type": "run_aborted", "severity": "warning",
             "description": "Run aborted: stopped by operator"},
            {"elapsed_s": 2.0, "type": "run_faulted", "description": "Fault: over temperature"},
            {"elapsed_s": 3.0, "type": "estop_released", "description": "E-Stop released"},
        ))

        text = self.open_event_dialog().findChild(QtWidgets.QPlainTextEdit).toPlainText()

        self.assertIn("00:01.00  [WARNING] Run aborted: stopped by operator", text)
        self.assertIn("00:02.00  [ERROR] Fault: over temperature", text)
        self.assertIn("00:03.00  E-Stop released", text)

    def test_event_text_is_light_on_its_dark_background(self) -> None:
        dialog = self.open_event_dialog()
        event_text = dialog.findChild(QtWidgets.QPlainTextEdit)
        event_text.ensurePolished()

        text_color = event_text.palette().color(QtGui.QPalette.ColorRole.Text)
        background = event_text.viewport().grab().toImage().pixelColor(4, 4)

        self.assertGreater(text_color.lightness() - background.lightness(), 120)


if __name__ == "__main__":
    unittest.main()
