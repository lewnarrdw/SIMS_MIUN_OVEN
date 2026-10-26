"""Tests for dimming unavailable toolbar buttons."""

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from main import KilnWindow
from tests.test_emergency_stop import FakeService

SAMPLES = tuple(
    {"elapsed_s": float(second), "temperature_c": 25.0 + second, "target_temperature_c": 40.0,
     "heater_output_percent": 50.0}
    for second in range(10)
)


class SampleService(FakeService):
    """A service whose snapshot also carries graph samples."""

    def __init__(self):
        super().__init__()
        self.samples = ()

    def snapshot(self):
        return self.view, self.samples


class WindowTestCase(unittest.TestCase):
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


class ToolbarAvailabilityTests(WindowTestCase):
    def enabled(self) -> dict[str, bool]:
        return {
            "run": self.window.run_action.isEnabled(),
            "stop": self.window.stop_action.isEnabled(),
            "manual": self.window.manual_controls_action.isEnabled(),
            "manual_button": self.window.manual_button.isEnabled(),
        }

    def test_idle_offers_run_and_manual_controls_but_not_stop(self) -> None:
        self.show()

        self.assertEqual(self.enabled(), {"run": True, "stop": False, "manual": True, "manual_button": True})

    def test_run_dims_run_and_manual_controls_and_offers_stop(self) -> None:
        self.show(running=True, state="heating")

        self.assertEqual(self.enabled(), {"run": False, "stop": True, "manual": False, "manual_button": False})

    def test_cool_down_still_counts_as_a_run(self) -> None:
        self.show(running=True, state="cooling")

        self.assertFalse(self.window.run_action.isEnabled())
        self.assertTrue(self.window.stop_action.isEnabled())

    def test_manual_heating_keeps_stop_available(self) -> None:
        self.show(output_percent=100.0)

        self.assertTrue(self.window.stop_action.isEnabled())
        self.assertTrue(self.window.run_action.isEnabled())

    def test_buttons_return_after_a_run(self) -> None:
        self.show(running=True, state="heating")

        self.show(running=False, state="aborted", output_percent=0.0)

        self.assertEqual(self.enabled(), {"run": True, "stop": False, "manual": True, "manual_button": True})

    def test_dimmed_stop_shortcut_sends_no_command(self) -> None:
        self.show()
        self.service.commands.clear()

        self.window.stop_action.trigger()

        self.assertEqual(self.service.commands, [])

    def test_estop_lock_is_not_undone_by_refresh(self) -> None:
        self.show(estop_active=True, state="e_stop")

        self.window.refresh()

        self.assertFalse(self.window.run_action.isEnabled())
        self.assertFalse(self.window.manual_controls_action.isEnabled())

    def test_estop_release_restores_the_live_availability(self) -> None:
        self.show(running=True, state="heating")
        self.show(estop_active=True, running=False, state="e_stop")

        self.show(estop_active=False, state="ready")

        self.assertEqual(self.enabled(), {"run": True, "stop": False, "manual": True, "manual_button": True})


if __name__ == "__main__":
    unittest.main()
