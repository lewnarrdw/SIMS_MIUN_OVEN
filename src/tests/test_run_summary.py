"""Tests for the run summary metrics and dialog."""

import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from data_manager import DataManager
from main import KilnWindow
from run_summary import attach_summary, format_duration, record_summary, summarize, summary_text
from run_summary_dialog import RunSummaryDialog
from tests.test_toolbar_availability import SampleService

TARGET = 30.0


def completed_record() -> dict:
    """10 Hz samples: heat 20 -> 30 °C in 200 s, hold 60 s at 30 ± 0.05, cool for 40 s."""
    samples = []
    for step in range(3001):
        t = step / 10
        if t < 200:
            temperature, output = 20.0 + t / 20, 100.0
        elif t < 320:
            temperature, output = TARGET + (0.05 if step % 2 else -0.05), 20.0
        else:
            temperature, output = TARGET - (t - 320) / 60, 0.0
        samples.append({"elapsed_s": t, "temperature_c": temperature, "target_temperature_c": TARGET,
                        "heater_output_percent": output, "kp": 33.0, "ki": 0.183, "kd": 660.0})
    return {
        "schema_version": 1, "started_at": "2026-10-26T12:31:05.000+01:00",
        "settings": {"target_temperature_c": TARGET, "hold_duration_s": 60.0, "kp": 33.0, "ki": 0.183, "kd": 660.0},
        "result": {"state": "complete", "status": "Run complete", "duration_s": 300.0, "hold_reached_s": 240.0,
                   "hold_elapsed_s": 60.0, "cooldown_s": 0.0, "reason": ""},
        "progress": {"stage": 4, "fraction": 1.0, "detail": ""},
        "metrics": {"peak": {"temperature_c": 30.05, "elapsed_s": 201.0},
                    "hold_minimum": {"temperature_c": 29.95, "elapsed_s": 240.0}},
        "events": [{"elapsed_s": 0.0, "type": "run_started", "description": "Run started"},
                   {"elapsed_s": 5.0, "type": "attention", "description": "x", "severity": "warning"},
                   {"elapsed_s": 300.0, "type": "stage_started", "description": "Cool down started"}],
        "samples": samples,
    }


def values(record: dict) -> dict[str, str]:
    return {metric.label: metric.value for metric in summarize(record, 0.1)}


def levels(record: dict) -> dict[str, str | None]:
    return {metric.label: metric.level for metric in summarize(record, 0.1)}


class SummaryMetricTests(unittest.TestCase):
    def test_run_section(self) -> None:
        result = values(completed_record())

        self.assertEqual(result["Outcome"], "Completed")
        self.assertEqual(result["Started"], "2026-10-26 12:31:05")
        self.assertEqual(result["Duration"], "05:00.0")
        self.assertEqual(result["Target"], "30.0 °C")
        self.assertEqual(result["PID gains"], "Kp 33 · Ki 0.183 · Kd 660")

    def test_timing(self) -> None:
        result = values(completed_record())

        self.assertEqual(result["Start temperature"], "20.00 °C")
        self.assertEqual(result["Time to reach ±0.1 °C"], "03:18.1")  # 29.9 °C sits on the band edge
        self.assertEqual(result["Settling (band to hold)"], "00:41.9")
        self.assertEqual(result["Warm-up (start to hold)"], "04:00.0")
        self.assertEqual(result["Hold time"], "01:00.0")
        self.assertEqual(result["Average heating rate"], "+3.00 °C/min")

    def test_accuracy(self) -> None:
        result = values(completed_record())

        self.assertEqual(result["Overshoot"], "0.05 °C")
        self.assertEqual(result["Peak error"], "+0.05 °C at 03:21.0")
        self.assertEqual(result["Hold mean"], "30.000 °C")
        self.assertEqual(result["Hold standard deviation"], "0.050 °C")
        self.assertEqual(result["Hold min / max"], "29.95 / 30.05 °C")
        self.assertEqual(result["Hold RMS error"], "0.050 °C")
        self.assertEqual(result["Hold time within ±0.1 °C"], "100.0 %")

    def test_heater_and_data(self) -> None:
        result = values(completed_record())

        self.assertEqual(result["Mean output, hold"], "20.0 %")
        self.assertEqual(result["Time at full power"], "03:20.0")
        self.assertEqual(result["Samples"], "3 001")
        self.assertEqual(result["Sample rate"], "10.0 Hz")
        self.assertEqual(result["Largest gap between samples"], "0.10 s")
        self.assertEqual(result["Events"], "3 (1 warnings, 0 errors)")

    def test_good_and_bad_values_are_flagged(self) -> None:
        record = completed_record()
        self.assertEqual(levels(record)["Overshoot"], "good")

        record["metrics"]["peak"]["temperature_c"] = 30.6

        self.assertEqual(levels(record)["Overshoot"], "warning")
        self.assertEqual(levels(record)["Peak error"], "warning")

    def test_stopped_run_without_a_hold(self) -> None:
        record = completed_record()
        record["samples"] = record["samples"][:1000]
        record["result"] = {"state": "aborted", "stop_state": "heating", "duration_s": 99.9,
                            "hold_reached_s": None, "hold_elapsed_s": 0.0, "cooldown_s": None}
        record["metrics"] = {"peak": None, "hold_minimum": None}

        result = values(record)

        self.assertEqual(result["Outcome"], "Stopped during heating")
        self.assertEqual(result["Hold statistics"], "No hold stage in this run")
        self.assertEqual(result["Warm-up (start to hold)"], "—")
        self.assertEqual(result["Peak error"], "—")

    def test_fault_outcome_is_described(self) -> None:
        record = completed_record()
        record["result"].update(state="fault", reason="sensor_timeout")

        self.assertEqual(values(record)["Outcome"], "Fault: Temperature sensor is not responding")
        self.assertEqual(levels(record)["Outcome"], "error")

    def test_duration_format(self) -> None:
        self.assertEqual(format_duration(358.04), "05:58.0")
        self.assertEqual(format_duration(None), "—")

    def test_text_for_the_clipboard_groups_sections(self) -> None:
        text = summary_text(summarize(completed_record(), 0.1))

        self.assertTrue(text.startswith("Run\n  Outcome: Completed"))
        self.assertIn("\n\nAccuracy\n", text)


class SummaryDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_dialog_lists_sections_and_metrics(self) -> None:
        dialog = RunSummaryDialog(summarize(completed_record(), 0.1), live=False)

        cells = [dialog.table.item(row, 0).text() for row in range(dialog.table.rowCount())]
        for section in ("Run", "Timing", "Accuracy", "Heater", "Data"):
            self.assertIn(section, cells)
        self.assertIs(dialog.pages.currentWidget(), dialog.table)
        dialog.deleteLater()

    def test_empty_dialog_centres_a_message_and_hides_copy(self) -> None:
        dialog = RunSummaryDialog([], live=True)

        self.assertIs(dialog.pages.currentWidget(), dialog.empty_page)
        self.assertEqual(dialog.empty_title.text(), "No run summary yet")
        self.assertTrue(dialog.empty_title.alignment() & QtCore.Qt.AlignmentFlag.AlignHCenter)
        self.assertTrue(dialog.copy_button.isHidden())
        self.assertIn("run in progress", dialog.windowTitle())
        dialog.deleteLater()

    def test_close_has_no_shortcut_underline(self) -> None:
        dialog = RunSummaryDialog([], live=False)

        self.assertEqual(dialog.close_button.text(), "Close")
        dialog.deleteLater()

    def test_copy_puts_the_summary_on_the_clipboard(self) -> None:
        dialog = RunSummaryDialog(summarize(completed_record(), 0.1), live=False)

        dialog.copy_to_clipboard()

        self.assertIn("Peak error: +0.05 °C", QtWidgets.QApplication.clipboard().text())
        dialog.deleteLater()


class SavedSummaryTests(unittest.TestCase):
    def test_saved_run_reopens_with_the_same_summary(self) -> None:
        record = attach_summary(completed_record(), 0.1)
        with tempfile.TemporaryDirectory() as directory:
            path = DataManager(directory).export_run(record, Path(directory) / "run.json")
            imported = DataManager.import_run(path)

        self.assertEqual(record_summary(imported, 0.1), summarize(completed_record(), 0.1))

    def test_saved_summary_is_shown_even_if_the_tolerance_changes_later(self) -> None:
        record = attach_summary(completed_record(), 0.1)

        labels = [metric.label for metric in record_summary(record, 0.5)]

        self.assertIn("Time to reach ±0.1 °C", labels)

    def test_older_runs_without_a_summary_are_summarised_when_opened(self) -> None:
        record = completed_record()

        self.assertEqual(record_summary(record, 0.1), summarize(record, 0.1))

    def test_import_rejects_a_damaged_summary(self) -> None:
        record = attach_summary(completed_record(), 0.1)
        record["summary"][0]["level"] = "purple"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.json"
            path.write_text(json.dumps(record), encoding="utf-8")

            with self.assertRaises(ValueError):
                DataManager.import_run(path)

    def test_no_samples_means_no_summary(self) -> None:
        self.assertEqual(record_summary({"samples": []}, 0.1), [])
        self.assertEqual(record_summary(None, 0.1), [])


class SummaryWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.service = SampleService()
        self.window = KilnWindow(self.service)
        self.window._timer.stop()
        self.record = completed_record()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def show(self, **changes) -> None:
        self.service.view = replace(self.service.view, **changes)
        self.window.refresh()

    def finish_run(self, state: str) -> None:
        self.service.samples = tuple(self.record["samples"])
        self.show(state="heating", running=True, run_started_at="2026-10-26T12:31:05")
        self.show(state=state, running=False)

    def test_summary_sits_next_to_the_event_log(self) -> None:
        labels = [action.text() for action in self.window.process_toolbar.actions()]

        self.assertEqual(labels[labels.index("Event log") + 1], "Run summary")

    def test_summary_is_dimmed_without_run_data(self) -> None:
        self.show()

        self.assertFalse(self.window.summary_action.isEnabled())

    def test_summary_is_available_with_run_data(self) -> None:
        self.service.samples = tuple(self.record["samples"])
        self.show(state="heating", running=True, run_started_at="2026-10-26T12:31:05")

        self.assertTrue(self.window.summary_action.isEnabled())

    def test_summary_stays_available_during_estop_when_there_is_data(self) -> None:
        self.service.samples = tuple(self.record["samples"])
        self.show(state="e_stop", estop_active=True, run_started_at="2026-10-26T12:31:05")

        self.assertTrue(self.window.summary_action.isEnabled())

    def test_summary_opens_by_itself_when_a_run_completes(self) -> None:
        self.finish_run("complete")

        self.assertIsNotNone(self.window._summary_dialog)
        self.assertTrue(self.window._summary_dialog.isVisible())
        self.assertTrue(self.window._summary_dialog.metrics)

    def test_summary_opens_by_itself_when_a_run_is_stopped(self) -> None:
        self.finish_run("aborted")

        self.assertIsNotNone(self.window._summary_dialog)

    def test_faults_do_not_open_the_summary(self) -> None:
        for state in ("fault", "e_stop"):
            self.finish_run(state)
            self.assertIsNone(self.window._summary_dialog, state)

    def test_summary_opens_only_once_per_run(self) -> None:
        self.finish_run("complete")
        self.window._summary_dialog.close()

        self.window.refresh()

        self.assertIsNone(self.window._summary_dialog)

    def test_finished_run_uses_the_saved_summary(self) -> None:
        saved = attach_summary(copy.deepcopy(self.record), 0.1)
        saved["started_at"] = "2026-10-26T12:31:05"
        saved["summary"][0]["value"] = "Saved copy"
        self.service.last_run_record = saved

        self.finish_run("complete")

        self.assertEqual(self.window._summary_dialog.metrics[0].value, "Saved copy")

    def test_imported_run_has_a_summary(self) -> None:
        self.window._historical_record = attach_summary(copy.deepcopy(self.record), 0.1)
        self.window.refresh()

        self.assertTrue(self.window.summary_action.isEnabled())
        self.window.show_run_summary()
        self.assertEqual(self.window._summary_dialog.windowTitle(), "Run summary")
        self.assertTrue(self.window._summary_dialog.metrics)


if __name__ == "__main__":
    unittest.main()
