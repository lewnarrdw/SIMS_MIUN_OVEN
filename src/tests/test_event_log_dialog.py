"""Tests for the colour-coded run event dialog and its formatting."""

import os
import unittest
from dataclasses import replace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtGui, QtWidgets

import event_log
from event_log import EventLogDialog, format_clock_time, format_run_time, possible_messages
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


def event(index: int, **details) -> dict:
    values = {"elapsed_s": float(index), "type": "test", "description": f"Event {index}",
              "time": f"2026-10-23T14:{index // 60:02d}:{index % 60:02d}.000+02:00",
              "run_elapsed_s": float(index)}
    values.update(details)
    return values


class EventFormattingTests(unittest.TestCase):
    def test_clock_time_shows_time_of_day(self) -> None:
        self.assertEqual(format_clock_time({"time": "2026-10-23T14:03:22.418+02:00"}), "14:03:22")

    def test_clock_time_is_blank_for_older_run_files(self) -> None:
        self.assertEqual(format_clock_time({"elapsed_s": 3.0}), "")
        self.assertEqual(format_clock_time({"time": "not a time"}), "")

    def test_run_time_counts_from_the_run_start(self) -> None:
        self.assertEqual(format_run_time({"time": "x", "run_elapsed_s": 135.3}), "+02:15.3")

    def test_run_time_is_blank_outside_a_run(self) -> None:
        self.assertEqual(format_run_time({"time": "x", "elapsed_s": 12.0}), "")

    def test_older_run_files_use_elapsed_time_as_run_time(self) -> None:
        self.assertEqual(format_run_time({"elapsed_s": 61.0}), "+01:01.0")

    def test_every_fault_description_is_a_possible_message(self) -> None:
        messages = possible_messages()
        for description in event_log.FAULT_DESCRIPTIONS.values():
            self.assertIn(f"Safety fault: {description}", messages)


class EventLogDialogTests(unittest.TestCase):
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

    def set_events(self, events) -> None:
        self.window.service.view = replace(self.window.service.view, events=tuple(events))

    def open_event_dialog(self) -> EventLogDialog:
        opened = []
        with mock.patch.object(QtWidgets.QDialog, "exec", lambda dialog: opened.append(dialog) or 0):
            self.window.show_run_events()
        return opened[0]

    def rows(self, dialog: EventLogDialog) -> list[list[str]]:
        table = dialog.table
        return [[table.item(row, column).text() for column in range(table.columnCount())]
                for row in range(table.rowCount())]

    def test_event_dialog_is_dark_with_a_light_system_palette(self) -> None:
        self.set_events([event(1)])
        dialog = self.open_event_dialog()

        dialog_background = dialog.grab().toImage().pixelColor(2, 2)
        table_background = dialog.table.viewport().grab().toImage().pixelColor(4, 300)

        self.assertLess(dialog_background.lightness(), 60)
        self.assertLess(table_background.lightness(), 60)

    def test_levels_use_event_severity_and_older_types(self) -> None:
        self.set_events([
            {"elapsed_s": 1.0, "type": "run_aborted", "severity": "warning",
             "description": "Run aborted: stopped by operator"},
            {"elapsed_s": 2.0, "type": "run_faulted", "description": "Fault: over temperature"},
            {"elapsed_s": 3.0, "type": "estop_released", "description": "E-Stop released"},
        ])

        rows = self.rows(self.open_event_dialog())

        self.assertEqual(rows, [
            ["", "+00:01.0", "WARNING", "Run aborted: stopped by operator"],
            ["", "+00:02.0", "ERROR", "Fault: over temperature"],
            ["", "+00:03.0", "OK", "E-Stop released"],
        ])

    def test_levels_are_colour_coded(self) -> None:
        self.set_events([event(1, severity="error"), event(2, severity="warning"),
                         event(3, type="run_completed"), event(4)])
        table = self.open_event_dialog().table

        level_colors = [table.item(row, 2).foreground().color().name() for row in range(4)]
        message_colors = [table.item(row, 3).foreground().color().name() for row in range(4)]

        levels = ("error", "warning", "success", "info")
        self.assertEqual(level_colors, [event_log.LEVEL_STYLES[level][0] for level in levels])
        self.assertEqual(len(set(level_colors)), 4)
        self.assertEqual(message_colors, [event_log.LEVEL_STYLES[level][0] for level in levels[:3]]
                         + [event_log.INFO_MESSAGE_COLOR])

    def test_success_events_are_green_and_info_is_grey(self) -> None:
        self.set_events([event(1, type="fault_cleared"), event(2, type="door_opened")])
        table = self.open_event_dialog().table

        success = table.item(0, 2).foreground().color()
        info = table.item(1, 2).foreground().color()

        self.assertEqual(table.item(0, 2).text(), "OK")
        self.assertGreater(success.green(), max(success.red(), success.blue()))
        self.assertLess(max(info.red(), info.green(), info.blue()) - min(info.red(), info.green(), info.blue()), 30)

    def test_only_run_end_and_recovery_events_are_success(self) -> None:
        for event_type in ("run_completed", "fault_cleared", "estop_released"):
            self.assertEqual(event_log.event_level({"type": event_type}), "success")
        for event_type in ("stage_completed", "door_closed", "run_started", "reset"):
            self.assertEqual(event_log.event_level({"type": event_type}), "info")

    def test_success_events_do_not_raise_the_attention_badge(self) -> None:
        self.set_events([event(1, type="run_completed")])

        self.window._update_attention_badge(self.window.service.view)

        self.assertFalse(any(item.get("type") == "run_completed"
                             for item in self.window._unread_attention.values()))

    def test_event_text_is_light_on_its_dark_background(self) -> None:
        self.set_events([event(1)])
        dialog = self.open_event_dialog()

        text_color = dialog.table.item(0, 3).foreground().color()
        background = dialog.table.viewport().grab().toImage().pixelColor(4, 300)

        self.assertGreater(text_color.lightness() - background.lightness(), 120)

    def test_dialog_opens_scrolled_to_the_latest_event(self) -> None:
        self.set_events([event(index) for index in range(200)])
        dialog = self.open_event_dialog()
        dialog.show()
        self.app.processEvents()
        dialog.scroll_to_latest()

        bar = dialog.table.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.assertEqual(bar.value(), bar.maximum())
        dialog.close()

    def test_open_dialog_shows_new_live_events(self) -> None:
        self.set_events([event(1)])
        dialog = self.open_event_dialog()

        self.set_events([event(1), event(2, description="Door open")])
        dialog.refresh()

        self.assertEqual(self.rows(dialog)[-1][3], "Door open")

    def test_event_column_fits_the_longest_possible_message(self) -> None:
        self.set_events([event(1)])
        dialog = self.open_event_dialog()
        metrics = QtGui.QFontMetrics(dialog.table.font())
        longest = max(metrics.horizontalAdvance(message) for message in possible_messages())
        screen = dialog.screen().availableGeometry().width()
        fixed = sum(dialog.table.columnWidth(column) for column in range(3))

        self.assertGreaterEqual(dialog.width(), min(fixed + longest, int(screen * 0.95)))

    def test_events_are_shown_in_wall_clock_order(self) -> None:
        self.set_events([event(5), event(2)])

        rows = self.rows(self.open_event_dialog())

        self.assertEqual([row[0] for row in rows], ["14:00:02", "14:00:05"])

    def test_empty_log_says_so(self) -> None:
        self.set_events([])
        dialog = self.open_event_dialog()

        self.assertTrue(dialog.table.isHidden())
        self.assertFalse(dialog.empty_label.isHidden())


if __name__ == "__main__":
    unittest.main()
