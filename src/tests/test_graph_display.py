"""Tests for graph display limits and target-line visibility."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from main import (
    GRAPH_WINDOW_S,
    MAX_OUTPUT_PERCENT,
    MAX_TEMPERATURE_DISPLAY_C,
    OvenWindow,
)


class GraphDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.window = OvenWindow()
        self.window.timer.stop()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def test_graph_axes_use_temperature_headroom_and_output_limit(self) -> None:
        self.assertEqual(self.window.graph_item.viewRange()[1], [0.0, MAX_TEMPERATURE_DISPLAY_C])
        self.assertEqual(self.window.duty_view.viewRange()[1], [0.0, MAX_OUTPUT_PERCENT])

    def test_goal_curve_is_semi_transparent(self) -> None:
        self.assertEqual(self.window.goal_curve.opts["pen"].color().alpha(), 160)



    def test_live_graph_scrolls_after_the_visible_window(self) -> None:
        self.window.elapsed_s = 180.0
        self.window._follow_live_graph()

        self.assertEqual(self.window.graph_item.viewRange()[0], [60.0, 180.0])

    def test_live_graph_limits_panning_to_recorded_time(self) -> None:
        self.window.elapsed_s = 180.0
        self.window._follow_live_graph()
        self.window.graph_item.setXRange(180.0, 300.0, padding=0)

        self.assertLessEqual(self.window.graph_item.viewRange()[0][1], 180.0)
    def test_completed_graph_fits_the_full_process(self) -> None:
        self.window.elapsed_s = 180.0
        self.window._fit_graph_to_completed_process()

        self.assertEqual(self.window.graph_item.viewRange()[0], [0.0, 180.0])

if __name__ == "__main__":
    unittest.main()