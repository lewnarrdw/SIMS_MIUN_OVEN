"""Tests for the stage indicator: completed, active, failed and waiting stages."""

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from gui import StageProgress
from main import KilnWindow
from tests.test_emergency_stop import FakeService

STAGES = ("Ready", "Heating", "Holding", "Cooling", "Done")


class StageIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.service = FakeService()
        self.window = KilnWindow(self.service)
        self.window._timer.stop()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def show(self, **changes) -> None:
        self.service.view = replace(self.service.view, **changes)
        self.window.refresh()

    def statuses(self) -> dict[str, str]:
        return {stage: self.window.progress.stage_status(index) for index, stage in enumerate(STAGES)}

    def expect(self, *statuses: str) -> None:
        self.assertEqual(self.statuses(), dict(zip(STAGES, statuses)))

    def test_ready_to_start_checks_ready_without_an_active_stage(self) -> None:
        self.show(state="ready", can_start=True)

        self.expect("completed", "pending", "pending", "pending", "pending")
        self.assertEqual(self.window.stage_label.text(), "Current stage: Ready to run")
        self.assertEqual(self.window.stage_detail_label.text(), "")

    def test_blocked_start_leaves_ready_waiting_and_says_why(self) -> None:
        self.show(state="ready", can_start=False, ready_reason="Release the E-Stop before starting")

        self.expect("pending", "pending", "pending", "pending", "pending")
        self.assertEqual(self.window.stage_label.text(), "Current stage: Not ready")
        self.assertEqual(self.window.stage_detail_label.text(),
                         "Cannot start: Release the E-Stop before starting")
        self.assertIn("#e8b33d", self.window.stage_detail_label.styleSheet())

    def test_running_stage_is_active_and_pulses(self) -> None:
        self.show(state="holding", running=True)

        self.expect("completed", "completed", "active", "pending", "pending")
        self.assertTrue(self.window.progress._pulse_timer.isActive())

    def test_closing_the_door_counts_as_heating(self) -> None:
        self.show(state="door_moving", running=True)

        self.expect("completed", "active", "pending", "pending", "pending")

    def test_completed_run_checks_every_stage_including_done(self) -> None:
        self.show(state="holding", running=True)
        self.show(state="complete", running=False)

        self.expect("completed", "completed", "completed", "completed", "completed")
        self.assertFalse(self.window.progress._pulse_timer.isActive())

    def test_stopped_run_is_crossed_where_it_stopped(self) -> None:
        self.show(state="aborted", stop_state="holding", stop_stage=2)

        self.expect("completed", "completed", "failed", "pending", "pending")

    def test_stop_while_closing_the_door_is_crossed_at_heating(self) -> None:
        self.show(state="aborted", stop_state="door_moving", stop_stage=1)

        self.expect("completed", "failed", "pending", "pending", "pending")

    def test_fault_during_a_run_is_crossed_at_that_stage(self) -> None:
        self.show(state="cooling", running=True)
        self.show(state="fault", running=False, detail="over_temperature")

        self.expect("completed", "completed", "completed", "failed", "pending")

    def test_estop_during_a_run_is_crossed_at_that_stage(self) -> None:
        self.show(state="heating", running=True)
        self.show(state="e_stop", running=False, estop_active=True)

        self.expect("completed", "failed", "pending", "pending", "pending")

    def test_fault_without_a_run_crosses_ready(self) -> None:
        self.show(state="fault", detail="sensor_timeout")

        self.expect("failed", "pending", "pending", "pending", "pending")

    def test_recovery_clears_the_cross(self) -> None:
        self.show(state="heating", running=True)
        self.show(state="fault", running=False, detail="sensor_timeout")
        self.show(state="ready", detail="", can_start=True)

        self.expect("completed", "pending", "pending", "pending", "pending")


class StageProgressWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_no_pulse_without_an_active_stage(self) -> None:
        widget = StageProgress()

        widget.set_progress(2, active=False, failed_stage=2)

        self.assertFalse(widget._pulse_timer.isActive())

    def test_painting_every_status_does_not_fail(self) -> None:
        widget = StageProgress()
        widget.resize(400, 70)
        for args in ((0, True, None), (5, False, None), (2, False, 2), (0, False, None)):
            widget.set_progress(args[0], active=args[1], failed_stage=args[2])
            self.assertFalse(widget.grab().isNull())


if __name__ == "__main__":
    unittest.main()
