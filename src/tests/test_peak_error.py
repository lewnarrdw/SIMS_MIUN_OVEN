"""Tests for the end-of-run peak error marker."""

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from gui import format_error_c, peak_error
from main import KilnWindow
from tests.test_toolbar_availability import SAMPLES, SampleService

TARGET = 30.0


class PeakErrorTests(unittest.TestCase):
    def test_overshoot_larger_than_hold_dip_wins(self) -> None:
        self.assertEqual(peak_error(TARGET, (300.0, 30.19), (700.0, 29.95)), (300.0, 30.19))

    def test_hold_dip_larger_than_overshoot_wins(self) -> None:
        self.assertEqual(peak_error(TARGET, (300.0, 30.05), (700.0, 29.88)), (700.0, 29.88))

    def test_missing_hold_low_uses_the_peak(self) -> None:
        self.assertEqual(peak_error(TARGET, (300.0, 30.4), None), (300.0, 30.4))

    def test_nothing_measured_gives_no_error(self) -> None:
        self.assertIsNone(peak_error(TARGET, None, None))

    def test_low_above_target_is_not_a_negative_error(self) -> None:
        # The hold never dipped below target, so only the overshoot counts.
        self.assertEqual(peak_error(TARGET, (300.0, 30.08), (700.0, 30.02)), (300.0, 30.08))

    def test_sign_follows_the_direction(self) -> None:
        self.assertEqual(format_error_c(0.19), "+0.19 °C")
        self.assertEqual(format_error_c(-0.12), "−0.12 °C")
        self.assertEqual(format_error_c(0.0), "+0.00 °C")


class PeakErrorMarkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.service = SampleService()
        self.service.samples = SAMPLES
        self.window = KilnWindow(self.service)
        self.window._timer.stop()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def labels(self) -> list[str]:
        return [item.textItem.toPlainText() for item in self.window.temperature_marker_items
                if hasattr(item, "textItem")]

    def show(self, **changes) -> None:
        values = dict(target_c=TARGET, peak_time_s=5.0, peak_c=30.19, hold_min_time_s=8.0, hold_min_c=29.95)
        self.service.view = replace(self.service.view, **(values | changes))
        self.window.refresh()

    def test_completed_run_shows_one_signed_peak_error(self) -> None:
        self.show(state="complete")

        markers = [label for label in self.labels() if label.startswith(("Peak", "Hold low"))]
        self.assertEqual(markers, ["Peak error +0.19 °C at 00:05.0"])

    def test_negative_peak_error_from_the_hold_low(self) -> None:
        self.show(state="complete", peak_c=30.04, hold_min_c=29.82)

        self.assertIn("Peak error −0.18 °C at 00:08.0", self.labels())

    def test_no_peak_error_during_or_after_an_aborted_run(self) -> None:
        for state in ("heating", "holding", "aborted", "fault"):
            self.show(state=state)
            self.assertFalse([label for label in self.labels() if label.startswith(("Peak", "Hold low"))], state)


if __name__ == "__main__":
    unittest.main()
