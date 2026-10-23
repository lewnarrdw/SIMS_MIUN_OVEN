"""Tests for the hold-window shading and the minimise/exit window controls."""

import os
import unittest
from dataclasses import replace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

import gui
from gui import hold_window_span, minimize_available
from main import KilnWindow
from tests.test_emergency_stop import FakeService


class HoldWindowSpanTests(unittest.TestCase):
    def test_hidden_before_and_during_a_run(self) -> None:
        for state in ("ready", "door_moving", "heating", "holding", "cooling"):
            with self.subTest(state=state):
                self.assertIsNone(hold_window_span(state, 40.0, 20.0, 90.0))

    def test_completed_run_uses_measured_hold(self) -> None:
        self.assertEqual(hold_window_span("complete", 42.5, 60.0, 180.0), (42.5, 102.5))

    def test_run_stopped_during_hold_shows_partial_hold(self) -> None:
        self.assertEqual(hold_window_span("aborted", 40.0, 12.0, 52.0), (40.0, 52.0))

    def test_run_ended_before_hold_shows_nothing(self) -> None:
        for state in ("aborted", "fault", "e_stop"):
            with self.subTest(state=state):
                self.assertIsNone(hold_window_span(state, None, 0.0, 30.0))

    def test_fault_during_hold_ends_at_run_end(self) -> None:
        self.assertEqual(hold_window_span("fault", 40.0, 0.0, 47.0), (40.0, 47.0))

    def test_empty_hold_shows_nothing(self) -> None:
        self.assertIsNone(hold_window_span("fault", 40.0, None, 40.0))


class MinimizeAvailableTests(unittest.TestCase):
    def test_windows_always_offers_minimise(self) -> None:
        self.assertTrue(minimize_available("win32", {}))

    def test_linux_desktop_session_offers_minimise(self) -> None:
        self.assertTrue(minimize_available("linux", {"XDG_CURRENT_DESKTOP": "LXDE-pi-labwc"}))

    def test_linux_kiosk_without_desktop_hides_minimise(self) -> None:
        self.assertFalse(minimize_available("linux", {}))

    def test_override_wins(self) -> None:
        self.assertFalse(minimize_available("win32", {}, override=False))
        self.assertTrue(minimize_available("linux", {}, override=True))


class WindowControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.service = FakeService()
        self.window = KilnWindow(self.service)
        self.window._timer.stop()
        self.window.refresh()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def set_view(self, **changes) -> None:
        self.service.view = replace(self.service.view, **changes)
        self.window.refresh()

    def test_hold_window_hidden_until_run_finishes(self) -> None:
        self.assertFalse(self.window.hold_region.isVisible())
        self.assertTrue(self.window.hold_legend_item.isHidden())
        self.set_view(running=True, state="holding", hold_reached_s=40.0, hold_elapsed_s=10.0, elapsed_s=50.0)
        self.assertFalse(self.window.hold_region.isVisible())
        self.assertTrue(self.window.hold_legend_item.isHidden())

    def test_hold_window_shown_at_measured_position_after_completion(self) -> None:
        self.set_view(running=False, state="complete", hold_reached_s=40.0, hold_elapsed_s=60.0, elapsed_s=160.0)
        self.assertTrue(self.window.hold_region.isVisible())
        self.assertFalse(self.window.hold_legend_item.isHidden())
        self.assertEqual(tuple(self.window.hold_region.getRegion()), (40.0, 100.0))

    def test_hold_window_hidden_again_when_next_run_starts(self) -> None:
        self.set_view(running=False, state="complete", hold_reached_s=40.0, hold_elapsed_s=60.0, elapsed_s=160.0)
        self.set_view(running=True, state="heating", hold_reached_s=None, hold_elapsed_s=0.0, elapsed_s=1.0)
        self.assertFalse(self.window.hold_region.isVisible())

    def test_exit_when_idle_closes_without_asking(self) -> None:
        with mock.patch.object(KilnWindow, "_confirm_exit_during_run") as confirm:
            self.window.request_exit()
        confirm.assert_not_called()
        self.assertEqual(self.service.close_calls, 1)

    def test_exit_during_run_can_be_cancelled(self) -> None:
        self.set_view(running=True, state="heating")
        with mock.patch.object(KilnWindow, "_confirm_exit_during_run", return_value=False):
            self.window.exit_button.click()
        self.assertEqual(self.service.close_calls, 0)

    def test_exit_during_run_closes_after_confirmation(self) -> None:
        self.set_view(running=True, state="heating")
        with mock.patch.object(KilnWindow, "_confirm_exit_during_run", return_value=True):
            self.window.quit_action.trigger()
        self.assertEqual(self.service.close_calls, 1)

    def test_minimise_button_minimises(self) -> None:
        with mock.patch.object(KilnWindow, "showMinimized") as minimise:
            self.window.minimize_button.click()
        minimise.assert_called_once()

    def test_window_controls_stay_usable_during_estop(self) -> None:
        self.set_view(state="e_stop", estop_active=True)
        self.assertTrue(self.window.window_control_action.isEnabled())

    def _tooltip_colours(self, widget) -> tuple:
        centre = widget.rect().center()
        event = QtGui.QHelpEvent(QtCore.QEvent.Type.ToolTip, centre, widget.mapToGlobal(centre))
        QtWidgets.QApplication.sendEvent(widget, event)
        tip = next(top for top in QtWidgets.QApplication.topLevelWidgets()
                   if top.inherits("QTipLabel") and top.isVisible())
        palette = tip.palette()
        colours = (palette.color(QtGui.QPalette.ColorRole.ToolTipBase).name(),
                   palette.color(QtGui.QPalette.ColorRole.ToolTipText).name(),
                   tip.font().family(), tip.font().pointSizeF())
        QtWidgets.QToolTip.hideText()
        return colours

    def test_window_control_tooltips_match_other_toolbar_tooltips(self) -> None:
        self.window.show()
        reference = self._tooltip_colours(self.window.file_button)
        for button in (self.window.exit_button, self.window.minimize_button):
            with self.subTest(button=button.kind):
                self.assertEqual(self._tooltip_colours(button), reference)

    @staticmethod
    def _left_border_colour(widget) -> QtGui.QColor:
        # Draw over the toolbar colour so the round buttons' transparent corners do not count.
        pixmap = QtGui.QPixmap(widget.size())
        pixmap.fill(QtGui.QColor("#1a1d22"))
        widget.render(pixmap, QtCore.QPoint(), QtGui.QRegion(), QtWidgets.QWidget.RenderFlag.DrawChildren)
        return pixmap.toImage().pixelColor(0, widget.height() // 2)

    def assert_colour_close(self, actual: QtGui.QColor, expected: QtGui.QColor) -> None:
        # A curved edge blends slightly with the background, so allow a few levels per channel.
        for channel in ("red", "green", "blue"):
            self.assertLessEqual(abs(getattr(actual, channel)() - getattr(expected, channel)()), 8,
                                 f"{actual.name()} != {expected.name()}")

    def test_window_control_borders_match_toolbar_buttons(self) -> None:
        self.window.show()
        reference = self.window.process_toolbar.widgetForAction(self.window.reset_action)
        expected = {}
        for state in ("rest", "hover", "pressed"):
            reference.setAttribute(QtCore.Qt.WidgetAttribute.WA_UnderMouse, state != "rest")
            reference.setDown(state == "pressed")
            reference.style().unpolish(reference)
            reference.style().polish(reference)
            expected[state] = self._left_border_colour(reference)
        for button in (self.window.minimize_button, self.window.exit_button):
            for state in ("rest", "hover", "pressed"):
                with self.subTest(button=button.kind, state=state):
                    button._set_hover(0.0 if state == "rest" else 1.0)
                    button.setDown(state == "pressed")
                    self.assert_colour_close(self._left_border_colour(button), expected[state])
            button.setDown(False)

    def test_unknown_window_control_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            gui.WindowControlButton("maximize")


if __name__ == "__main__":
    unittest.main()
