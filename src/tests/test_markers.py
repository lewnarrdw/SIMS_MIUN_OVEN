"""Tests for the end-of-run markers' labels, colours and placement."""

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtGui, QtWidgets

import gui
from main import KilnWindow
from tests.test_toolbar_availability import SampleService

SAMPLES = tuple(
    {"elapsed_s": float(second), "temperature_c": 30.0, "target_temperature_c": 30.0, "heater_output_percent": 20.0}
    for second in range(450)
)


class EndOfRunMarkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.service = SampleService()
        self.service.samples = SAMPLES
        self.window = KilnWindow(self.service)
        self.window._timer.stop()
        self.window.resize(1280, 800)
        self.window.show()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def show(self, **changes) -> None:
        self.service.view = replace(self.service.view, **changes)
        self.window.refresh()
        self.app.processEvents()

    def markers(self) -> dict[str, QtGui.QColor]:
        """Label text and marker colour of each marker on the graph."""
        items = self.window.temperature_marker_items
        return {label.textItem.toPlainText(): point.opts["brush"].color()
                for point, label in zip(items[::2], items[1::2])}

    def show_completed_run(self) -> None:
        self.show(state="complete", target_c=30.0, peak_time_s=298.9, peak_c=30.07, hold_min_time_s=380.0,
                  hold_min_c=29.97, hold_reached_s=358.0, cooldown_s=27.7, elapsed_s=445.8)

    def test_completed_run_labels_carry_their_times(self) -> None:
        self.show_completed_run()

        self.assertEqual(set(self.markers()), {"Peak error +0.07 °C at 04:58.9", "Hold started at 05:58.0",
                                               "Cooled down at 07:25.8 (28 s)"})

    def test_each_marker_has_its_own_colour(self) -> None:
        self.show_completed_run()
        colours = {label.split(" ")[0]: colour.name() for label, colour in self.markers().items()}

        self.assertEqual(colours, {"Peak": gui.MARKER_COLORS["peak_error"], "Hold": gui.MARKER_COLORS["hold"],
                                   "Cooled": gui.MARKER_COLORS["cooled"]})
        self.assertEqual(len(set(gui.MARKER_COLORS.values())), len(gui.MARKER_COLORS))

    def test_stopped_run_label_carries_its_time(self) -> None:
        self.show(state="aborted", stop_state="heating", stop_elapsed_s=192.4, elapsed_s=192.4)

        self.assertIn("Stopped during Heating at 03:12.4", self.markers())
        self.assertEqual(self.markers()["Stopped during Heating at 03:12.4"].name(), gui.MARKER_COLORS["stopped"])

    def test_labels_stay_inside_the_plot(self) -> None:
        self.show_completed_run()
        bounds = self.window.graph_item.vb.sceneBoundingRect()

        for label in self.window.temperature_marker_items[1::2]:
            rect = label.mapRectToScene(label.boundingRect())
            self.assertTrue(bounds.contains(rect), label.textItem.toPlainText())


if __name__ == "__main__":
    unittest.main()
