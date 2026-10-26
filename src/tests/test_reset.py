"""Tests for the optional Reset button."""

import os
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

import application
import config
from application import Application
from main import KilnWindow
from tests.test_emergency_stop import FakeHardware, wait_for
from tests.test_toolbar_availability import SAMPLES, SampleService


class ApplicationResetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_directory = tempfile.TemporaryDirectory()
        test_config = SimpleNamespace(**{name: getattr(config, name) for name in dir(config) if name.isupper()})
        test_config.DATA_DIRECTORY = self.data_directory.name
        test_config.HEATING_CHECK_WINDOW_S = 0.5
        test_config.LOOP_PERIOD_S = 0.1
        self.hardware = FakeHardware()
        with mock.patch.object(application, "create_hardware", return_value=self.hardware):
            self.app = Application(test_config)
        self.assertTrue(wait_for(lambda: self.state() == "ready"))

    def tearDown(self) -> None:
        self.app.close()
        self.data_directory.cleanup()

    def state(self) -> str:
        return self.app.snapshot()[0].state

    def rejected(self) -> list[str]:
        return [event["description"] for event in self.app.snapshot()[0].events
                if event["type"] == "command_rejected"]

    def reset(self) -> None:
        handled = self.app.snapshot()[0].resets_handled
        self.app.command("reset")
        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].resets_handled == handled + 1))

    def stopped_run(self) -> None:
        self.app.command("start")
        self.assertTrue(wait_for(lambda: self.state() == "heating"))
        self.app.command("stop")
        self.assertTrue(wait_for(lambda: self.state() == "aborted"))

    def test_reset_after_a_stopped_run_clears_the_run(self) -> None:
        self.stopped_run()
        self.assertTrue(self.app.snapshot()[1])

        self.reset()

        view, samples = self.app.snapshot()
        self.assertEqual(view.state, "ready")
        self.assertEqual(samples, ())
        self.assertIsNone(view.stop_state)
        self.assertEqual(view.elapsed_s, 0.0)
        self.assertIn("reset", [event["type"] for event in view.events])

    def test_reset_is_refused_during_a_run(self) -> None:
        self.app.command("start")
        self.assertTrue(wait_for(lambda: self.state() == "heating"))

        self.reset()

        self.assertEqual(self.state(), "heating")
        self.assertIn("Reset rejected: Stop or finish the active run before resetting", self.rejected())

    def test_reset_is_refused_during_estop(self) -> None:
        self.hardware.estop_engaged = True
        self.assertTrue(wait_for(lambda: self.state() == "e_stop"))

        self.reset()

        self.assertEqual(self.state(), "e_stop")

    def test_reset_does_not_clear_a_live_fault(self) -> None:
        self.hardware.sensor.temperature_c = config.MAX_TEMPERATURE + 5.0
        self.assertTrue(wait_for(lambda: self.state() == "fault"))

        self.reset()

        self.assertEqual(self.state(), "fault")
        self.assertIn("Reset rejected: The fault clears by itself once its cause is fixed", self.rejected())

    def test_live_fault_clears_without_reset(self) -> None:
        self.hardware.sensor.temperature_c = config.MAX_TEMPERATURE + 5.0
        self.assertTrue(wait_for(lambda: self.state() == "fault"))

        self.hardware.sensor.temperature_c = 22.0

        self.assertTrue(wait_for(lambda: self.state() == "ready"))

    def test_reset_may_clear_a_latched_heater_fault(self) -> None:
        self.app.command("start")
        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].detail == "heater_not_heating"))

        self.reset()

        self.assertEqual(self.state(), "ready")
        self.assertFalse(self.hardware.heater.locked)

    def test_start_still_clears_a_latched_heater_fault_without_reset(self) -> None:
        self.app.command("start")
        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].detail == "heater_not_heating"))

        self.app.command("start")

        self.assertTrue(wait_for(lambda: self.state() == "heating"))


class ResetWindowTests(unittest.TestCase):
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

    def stopped_markers(self) -> list:
        return [item for item in self.window.temperature_marker_items
                if hasattr(item, "textItem") and "Stopped during" in item.textItem.toPlainText()]

    def show_stopped_run(self) -> None:
        self.service.samples = SAMPLES
        self.show(state="aborted", stop_state="heating", stop_elapsed_s=5.0, elapsed_s=9.0)

    def test_toolbar_has_reset_after_stop(self) -> None:
        labels = [action.text() for action in self.window.process_toolbar.actions()]

        self.assertEqual(labels[labels.index("Stop") + 1], "Reset")

    def test_reset_sends_the_reset_command(self) -> None:
        self.service.commands.clear()

        self.window.reset_action.trigger()

        self.assertEqual(self.service.commands, ["reset"])

    def test_snapshot_from_before_the_reset_does_not_redraw_the_marker(self) -> None:
        self.show_stopped_run()
        self.assertEqual(len(self.stopped_markers()), 1)

        self.window.reset_process()
        self.window.refresh()  # The worker has not handled the reset yet.

        self.assertEqual(self.stopped_markers(), [])
        x_data, _y = self.window.temperature_curve.getData()
        self.assertTrue(x_data is None or len(x_data) == 0)

    def test_handled_reset_shows_the_live_state(self) -> None:
        self.show_stopped_run()
        self.window.reset_process()

        self.service.samples = ()
        self.show(state="ready", stop_state=None, stop_elapsed_s=None, resets_handled=1)

        self.assertEqual(self.stopped_markers(), [])

    def test_refused_reset_brings_the_run_back(self) -> None:
        self.show_stopped_run()
        self.window.reset_process()
        self.window.refresh()

        self.show(resets_handled=1)  # Handled, but the state is unchanged: it was refused.

        self.assertEqual(len(self.stopped_markers()), 1)


if __name__ == "__main__":
    unittest.main()
