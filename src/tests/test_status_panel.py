"""Tests for the elapsed time and door rows in the temperature and heater output panel."""

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

import gui
from gui import door_status
from main import KilnWindow
from tests.test_emergency_stop import FakeService


class DoorStatusTests(unittest.TestCase):
    def test_limit_switch_states(self) -> None:
        self.assertEqual(door_status("closed"), ("Closed", gui.DOOR_OK_COLOR))
        self.assertEqual(door_status("open"), ("Open", gui.DOOR_ATTENTION_COLOR))
        self.assertEqual(door_status("between"), ("Partly open", gui.DOOR_ATTENTION_COLOR))

    def test_moving_motor_shows_direction(self) -> None:
        self.assertEqual(door_status("between", "opening"), ("Opening…", gui.DOOR_MOVING_COLOR))
        self.assertEqual(door_status("closed", "closing"), ("Closing…", gui.DOOR_MOVING_COLOR))

    def test_faults_take_priority(self) -> None:
        self.assertEqual(door_status("error", "idle"), ("Switch fault", gui.DOOR_FAULT_COLOR))
        self.assertEqual(door_status("closed", "fault"), ("Motor fault", gui.DOOR_FAULT_COLOR))

    def test_unknown_door_is_not_available(self) -> None:
        for door in ("unknown", None, ""):
            self.assertEqual(door_status(door), ("N/A", gui.DOOR_UNKNOWN_COLOR))


class StatusPanelTests(unittest.TestCase):
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

    def show(self, **values) -> None:
        self.service.view = replace(self.service.view, **values)
        self.window.refresh()

    def test_elapsed_shows_only_the_timer_in_its_own_row(self) -> None:
        self.show(elapsed_s=74.4)

        self.assertEqual(self.window.elapsed_label.text(), "01:14.4")

    def test_elapsed_row_matches_the_hold_remaining_row(self) -> None:
        self.window.show()
        self.app.processEvents()
        elapsed = self.window.elapsed_label
        hold = self.window.hold_remaining_label

        self.assertEqual(elapsed.font().pointSizeF(), hold.font().pointSizeF())
        right_edge = lambda label: label.mapTo(self.window, label.rect().topRight()).x()
        self.assertEqual(right_edge(elapsed), right_edge(hold))

    def test_door_pill_follows_the_live_door_and_motor(self) -> None:
        self.show(door="closed", door_motor="idle")
        self.assertIn("Closed", self.window.door_label.text())

        self.show(door="between", door_motor="opening")
        self.assertIn("Opening…", self.window.door_label.text())
        self.assertIn(gui.DOOR_MOVING_COLOR, self.window.door_label.styleSheet())

        self.show(door="open", door_motor="idle")
        self.assertIn("Open", self.window.door_label.text())
        self.assertIn(gui.DOOR_ATTENTION_COLOR, self.window.door_label.styleSheet())


if __name__ == "__main__":
    unittest.main()
