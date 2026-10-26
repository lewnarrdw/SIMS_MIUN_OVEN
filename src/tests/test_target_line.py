"""Tests for the dashed target line on the temperature graph."""

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from main import KilnWindow
from tests.test_toolbar_availability import SAMPLES, SampleService


class TargetLineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.service = SampleService()
        self.window = KilnWindow(self.service)
        self.window._timer.stop()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def show(self, **changes) -> None:
        self.service.view = replace(self.service.view, **changes)
        self.window.refresh()

    @staticmethod
    def line(curve) -> list[float]:
        _x, y = curve.getData()
        return [] if y is None else [float(value) for value in y]

    def visible_target(self) -> list[float]:
        return self.line(self.window.goal_curve) or self.line(self.window.planned_curve)

    def test_idle_graph_shows_the_planned_target(self) -> None:
        self.window.target_input.setValue(40.0)
        self.show()

        self.assertEqual(self.visible_target(), [40.0, 40.0])

    def test_run_shows_its_own_target_only(self) -> None:
        self.service.samples = SAMPLES
        self.show(running=True, state="heating", run_started_at="2026-10-26T12:00:00")

        self.assertEqual(self.line(self.window.goal_curve), [40.0, 40.0])
        self.assertEqual(self.line(self.window.planned_curve), [])

    def test_line_returns_when_the_run_data_is_cleared(self) -> None:
        self.service.samples = SAMPLES
        self.show(running=True, state="heating", run_started_at="2026-10-26T12:00:00")
        self.window.target_input.setValue(42.0)  # Changed while the old run is still shown.

        self.service.samples = ()
        self.show(running=False, state="ready")

        self.assertEqual(self.visible_target(), [42.0, 42.0])

    def test_line_spans_the_visible_time_range(self) -> None:
        self.show()
        x_min, x_max = self.window.graph_item.vb.viewRange()[0]

        x, _y = self.window.planned_curve.getData()

        self.assertEqual([float(x[0]), float(x[-1])], [x_min, x_max])

    def test_window_has_no_reset_button(self) -> None:
        labels = [action.text() for action in self.window.process_toolbar.actions()]

        self.assertNotIn("Reset", labels)
        self.assertFalse(hasattr(self.window, "reset_action"))
        self.assertFalse(hasattr(self.window, "reset_process"))


if __name__ == "__main__":
    unittest.main()
