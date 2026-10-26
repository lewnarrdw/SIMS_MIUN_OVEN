"""Tests for importing runs with tuned gains, the status bar and the file dialog folder."""

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
from run_summary import attach_summary
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
