"""Tests for importing runs with tuned gains, the status bar and the file dialog folder."""

import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

import config
import gui
from data_manager import DataManager
from main import KilnWindow
from run_summary import attach_summary, record_summary, summarize
from tests.test_run_summary import completed_record
from tests.test_toolbar_availability import SampleService


class ImportTunedRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.window = KilnWindow(SampleService())
        self.window._timer.stop()
        self.window.data = DataManager(self.directory.name)

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.directory.cleanup()

    def import_record(self, record: dict) -> None:
        path = DataManager(self.directory.name).export_run(record, Path(self.directory.name) / "run.json")
        with mock.patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=(str(path), "")):
            self.window.import_run()

    def test_run_with_the_tuned_gains_imports(self) -> None:
        self.import_record(attach_summary(completed_record(), 0.1))

        self.assertIsNotNone(self.window._historical_record)
        self.assertEqual(self.window.statusBar().currentMessage(), "Run and settings imported")
        self.assertEqual((self.window.kp_input.value(), self.window.ki_input.value(), self.window.kd_input.value()),
                         (33.0, 0.183, 660.0))

    def test_gains_beyond_the_input_limits_are_refused(self) -> None:
        record = completed_record()
        record["settings"]["kd"] = gui.MAX_KD + 1.0

        self.import_record(record)

        self.assertIsNone(self.window._historical_record)
        self.assertEqual(self.window.statusBar().currentMessage(), "Run PID gains are outside the supported range")

    def test_configured_gains_are_within_the_import_limits(self) -> None:
        self.window._validate_settings({"target_temperature_c": config.MIN_SETPOINT,
                                        "hold_duration_s": 60.0, "kp": config.PID_KP,
                                        "ki": config.PID_KI, "kd": config.PID_KD})


def detailed_record() -> dict:
    """A completed run with a summary, an event with extra details and text that looks numeric."""
    record = attach_summary(completed_record(), 0.1)
    record["events"][0]["settings"] = dict(record["settings"])
    record["events"][0]["time"] = "2026-10-26T12:31:05.000+01:00"
    record["result"].update(status="42", stop_state=None, hold_completed=True)
    record["progress"]["detail"] = "Cool down, 1, 2"
    record["metrics"]["hold_minimum"] = None
    return record


class RunCsvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)
        self.data = DataManager(self.folder)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def rows(self, path: Path) -> list[list[str]]:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            return list(csv.reader(stream))

    def test_a_run_is_saved_as_csv_by_default(self) -> None:
        self.assertEqual(self.data.export_run(completed_record()).suffix, ".csv")
        self.assertEqual(self.data.export_run(completed_record(), self.folder / "run").suffix, ".csv")

    def test_graph_data_is_in_the_leftmost_columns(self) -> None:
        rows = self.rows(self.data.export_run(detailed_record(), self.folder / "run.csv"))

        self.assertEqual(tuple(rows[0][:len(DataManager.SAMPLE_FIELDS)]), DataManager.SAMPLE_FIELDS)
        self.assertEqual(rows[1][:4], ["0.0", "20.0", "30.0", "100.0"])
        self.assertEqual(len(rows), 1 + len(completed_record()["samples"]))

    def test_csv_keeps_the_summary_events_and_run_details(self) -> None:
        record = detailed_record()
        csv_path = self.data.export_run(record, self.folder / "run.csv")
        json_path = self.data.export_run(record, self.folder / "run.json")

        imported = DataManager.import_run(csv_path)

        self.assertEqual(imported, DataManager.import_run(json_path))
        self.assertEqual(imported["summary"], record["summary"])
        self.assertEqual(imported["events"][0]["settings"], record["settings"])
        self.assertEqual(imported["result"]["status"], "42")
        self.assertIsNone(imported["metrics"]["hold_minimum"])
        self.assertIs(imported["result"]["hold_completed"], True)

    def test_a_run_without_a_summary_imports_and_is_summarised(self) -> None:
        imported = DataManager.import_run(self.data.export_run(completed_record(), self.folder / "run.csv"))

        self.assertNotIn("summary", imported)
        self.assertEqual(record_summary(imported, 0.1), summarize(completed_record(), 0.1))

    def test_json_export_is_still_available(self) -> None:
        path = self.data.export_run(completed_record(), self.folder / "run", "json")

        self.assertEqual(path.suffix, ".json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 1)

    def test_a_typed_suffix_overrides_the_chosen_format(self) -> None:
        self.assertEqual(self.data.export_run(completed_record(), self.folder / "run.json", "csv").suffix, ".json")

    def test_an_unknown_format_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.data.export_run(completed_record(), self.folder / "run", "xml")

    def test_a_csv_without_run_columns_is_refused(self) -> None:
        path = self.folder / "settings.csv"
        DataManager.export_settings(completed_record()["settings"], path)

        with self.assertRaisesRegex(ValueError, "Unsupported or invalid kiln run file"):
            DataManager.import_run(path)

    def test_a_damaged_sample_in_a_csv_is_refused(self) -> None:
        path = self.data.export_run(completed_record(), self.folder / "run.csv")
        rows = self.rows(path)
        rows[2][1] = "hot"
        with path.open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows(rows)

        with self.assertRaises(ValueError):
            DataManager.import_run(path)

    def test_damaged_event_details_are_refused(self) -> None:
        path = self.data.export_run(detailed_record(), self.folder / "run.csv")
        rows = self.rows(path)
        rows[1][rows[0].index("event_details")] = "{not json"
        with path.open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows(rows)

        with self.assertRaisesRegex(ValueError, "event details"):
            DataManager.import_run(path)


class RunExportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)
        self.window = KilnWindow(SampleService())
        self.window._timer.stop()
        self.window.data = DataManager(self.folder)
        self.window._historical_record = detailed_record()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.directory.cleanup()

    def export(self, name: str, selected_filter: str) -> mock.MagicMock:
        with mock.patch.object(QtWidgets.QFileDialog, "getSaveFileName",
                               return_value=(str(self.folder / name), selected_filter)) as dialog:
            self.window.export_run_data()
        return dialog

    def test_csv_is_the_suggested_file_type(self) -> None:
        dialog = self.export("", "")

        self.assertTrue(dialog.call_args.args[2].endswith(".csv"))
        self.assertTrue(dialog.call_args.args[3].startswith("CSV"))

    def test_the_json_file_type_saves_json(self) -> None:
        self.export("run", "JSON files (*.json)")

        self.assertTrue((self.folder / "run.json").is_file())

    def test_the_csv_file_type_saves_csv(self) -> None:
        self.export("run", "CSV files (*.csv)")

        self.assertTrue((self.folder / "run.csv").is_file())

    def test_an_exported_csv_imports_with_its_summary(self) -> None:
        self.export("run.csv", "CSV files (*.csv)")
        self.window._historical_record = None
        with mock.patch.object(QtWidgets.QFileDialog, "getOpenFileName",
                               return_value=(str(self.folder / "run.csv"), "")):
            self.window.import_run()

        self.assertEqual(self.window.statusBar().currentMessage(), "Run and settings imported")
        self.assertEqual(self.window._historical_record["summary"], detailed_record()["summary"])


class StatusBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_a_message_does_not_resize_the_graph(self) -> None:
        window = KilnWindow(SampleService())
        window._timer.stop()
        window.resize(1280, 800)
        window.show()
        self.app.processEvents()
        height = window.graph.height()

        window.statusBar().showMessage("Run and settings imported", 5000)
        self.app.processEvents()

        self.assertTrue(window.statusBar().isVisible())
        self.assertEqual(window.statusBar().height(), gui.STATUS_BAR_HEIGHT)
        self.assertEqual(window.graph.height(), height)
        window.close()
        window.deleteLater()


class DataFolderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name) / "data"
        self.window = KilnWindow(SampleService())
        self.window._timer.stop()
        self.window.data = DataManager(self.folder)

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.directory.cleanup()

    def start_folder(self, dialog_name: str, action) -> Path:
        with mock.patch.object(QtWidgets.QFileDialog, dialog_name, return_value=("", "")) as dialog:
            action()
        return Path(dialog.call_args.args[2])

    def test_import_run_starts_in_the_data_folder(self) -> None:
        self.assertEqual(self.start_folder("getOpenFileName", self.window.import_run), self.folder.resolve())

    def test_import_settings_starts_in_the_data_folder(self) -> None:
        self.assertEqual(self.start_folder("getOpenFileName", self.window.import_settings), self.folder.resolve())

    def test_export_settings_suggests_a_file_in_the_data_folder(self) -> None:
        suggested = self.start_folder("getSaveFileName", self.window.export_settings)

        self.assertEqual(suggested.parent, self.folder.resolve())
        self.assertTrue(suggested.name.startswith("kiln-settings-"))

    def test_export_run_suggests_a_file_in_the_data_folder(self) -> None:
        self.window._historical_record = completed_record()

        suggested = self.start_folder("getSaveFileName", self.window.export_run_data)

        self.assertEqual(suggested.parent, self.folder.resolve())
        self.assertTrue(suggested.name.startswith("kiln-run-"))

    def test_missing_data_folder_is_created(self) -> None:
        self.assertFalse(self.folder.exists())

        self.start_folder("getOpenFileName", self.window.import_run)

        self.assertTrue(self.folder.is_dir())


if __name__ == "__main__":
    unittest.main()
