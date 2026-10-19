"""Tests for E-Stop listening, the E-Stop GUI lock, and shared button commands."""

import os
import tempfile
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

import application
import config
from application import OPERATOR_BUTTON_COMMANDS, Application, ViewState, describe_fault
from control.controller import Controller, State
from hardware.actuators import DoorMotor, Heater, SignalTower
from hardware.sensors import DoorSwitches
from main import KilnWindow


class FakeOutput:
    def __init__(self):
        self.is_active = False

    def on(self):
        self.is_active = True

    def off(self):
        self.is_active = False

    def close(self):
        pass


class FakeInput:
    def __init__(self, is_active=False):
        self.is_active = is_active


class FakeButton:
    def __init__(self):
        self.when_pressed = None

    def press(self):
        self.when_pressed()


class FakeSensor:
    def __init__(self, temperature_c=22.0):
        self.temperature_c = temperature_c

    def update(self, now):
        pass

    def temperature(self):
        return self.temperature_c

    def seconds_since_valid(self, now):
        return 0.0


class FakeHardware:
    def __init__(self):
        self.door_open, self.door_closed = FakeInput(), FakeInput(is_active=True)
        self.switches = DoorSwitches(self.door_open, self.door_closed)
        self.motor_in1, self.motor_in2 = FakeOutput(), FakeOutput()
        self.motor = DoorMotor(self.motor_in1, self.motor_in2, None, self.switches, 10.0)
        self.heater_output = FakeOutput()
        self.heater = Heater(self.heater_output, 1.0)
        self.tower = SignalTower({name: FakeOutput() for name in config.TOWER_PINS})
        self.sensor = FakeSensor()
        self.buttons = {name: FakeButton() for name in OPERATOR_BUTTON_COMMANDS}
        self.estop_device = SimpleNamespace(when_activated=None, when_deactivated=None)
        self.estop_engaged = False

    def estop(self):
        return self.estop_engaged

    def close(self):
        pass


def wait_for(predicate, timeout_s=3.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class ControllerEmergencyStopTests(unittest.TestCase):
    def test_emergency_stop_leaves_outputs_idle_without_locking_heater(self) -> None:
        hardware = FakeHardware()
        controller = Controller(hardware, config)
        hardware.heater.set_percent(80.0)

        controller.emergency_stop()

        self.assertEqual(controller.state, State.E_STOP)
        self.assertEqual(hardware.heater.percent, 0.0)
        self.assertFalse(hardware.heater.locked)
        self.assertFalse(hardware.heater_output.is_active)

    def test_repeated_emergency_stop_keeps_first_reason(self) -> None:
        controller = Controller(FakeHardware(), config)

        controller.emergency_stop("first")
        controller.emergency_stop("second")

        self.assertEqual(controller.fault_reason, "first")


class DescribeFaultTests(unittest.TestCase):
    def test_known_reason_has_plain_description(self) -> None:
        self.assertEqual(describe_fault("door_motor_timeout"),
                         "Door did not reach its limit switch in time")

    def test_worker_error_keeps_its_message(self) -> None:
        self.assertEqual(describe_fault("worker_error: SPI read failed"),
                         "Control error: SPI read failed")

    def test_unknown_reason_is_made_readable(self) -> None:
        self.assertEqual(describe_fault("door_motor_jammed"), "Door motor jammed")


class ApplicationEmergencyStopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_directory = tempfile.TemporaryDirectory()
        test_config = SimpleNamespace(**{
            name: getattr(config, name) for name in dir(config) if name.isupper()
        })
        test_config.DATA_DIRECTORY = self.data_directory.name
        self.hardware = FakeHardware()
        with mock.patch.object(application, "create_hardware", return_value=self.hardware):
            self.app = Application(test_config)

    def tearDown(self) -> None:
        self.app.close()
        self.data_directory.cleanup()

    def state(self) -> str:
        return self.app.snapshot()[0].state

    def test_physical_buttons_queue_the_operator_commands(self) -> None:
        with mock.patch.object(self.app, "command") as command:
            for name in OPERATOR_BUTTON_COMMANDS:
                self.hardware.buttons[name].press()

        self.assertEqual([call.args for call in command.call_args_list],
                         [(name,) for name in OPERATOR_BUTTON_COMMANDS])

    def test_physical_start_button_starts_a_run(self) -> None:
        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].can_start))

        self.hardware.buttons["start"].press()

        self.assertTrue(wait_for(lambda: self.state() == "heating"))

    def test_engaged_estop_ends_run_without_locking_outputs(self) -> None:
        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].can_start))
        self.app.command("start")
        self.assertTrue(wait_for(lambda: self.state() == "heating"))

        self.hardware.estop_engaged = True

        self.assertTrue(wait_for(lambda: self.state() == "e_stop"))
        view = self.app.snapshot()[0]
        self.assertTrue(view.estop_active)
        self.assertFalse(self.hardware.heater.locked)
        self.assertEqual(self.hardware.heater.percent, 0.0)
        self.assertIn("emergency_stop", [event["type"] for event in view.events])

    def test_estop_edge_callback_ends_run(self) -> None:
        callback = (self.hardware.estop_device.when_deactivated if config.ESTOP_NORMALLY_CLOSED
                    else self.hardware.estop_device.when_activated)

        callback()

        self.assertTrue(wait_for(lambda: self.state() == "e_stop"))

    def test_door_commands_rejected_until_estop_reset(self) -> None:
        self.hardware.estop_engaged = True
        self.assertTrue(wait_for(lambda: self.state() == "e_stop"))

        self.app.command("open")
        self.assertTrue(wait_for(lambda: "Emergency stop is active" in self.app.snapshot()[0].error))
        self.assertEqual(self.hardware.motor.state.value, "idle")

        self.app.command("reset")
        self.assertTrue(wait_for(lambda: "Cannot reset" in self.app.snapshot()[0].error))
        self.assertEqual(self.state(), "e_stop")

        self.hardware.estop_engaged = False
        self.app.command("reset")
        self.assertTrue(wait_for(lambda: self.state() == "ready"))


class FakeService:
    last_run_record = None

    def __init__(self, close_error=None):
        self.view = ViewState(False, 0, 0.0, "", "Ready to run", 0.0, config.MIN_SETPOINT, 0.0,
                              "closed", "ready", None, None, None, None, False)
        self.commands = []
        self.close_calls = 0
        self.close_error = close_error

    def snapshot(self):
        return self.view, ()

    def command(self, name, values=None):
        self.commands.append(name)

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class EmergencyStopWindowTests(unittest.TestCase):
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

    def toolbar_actions_enabled(self) -> list[bool]:
        return [action.isEnabled() for action in self.window.process_toolbar.actions()]

    def test_engaged_estop_shows_banner_and_locks_every_control(self) -> None:
        self.set_view(state="e_stop", estop_active=True)

        self.assertFalse(self.window.estop_banner.isHidden())
        self.assertIn("EMERGENCY STOP", self.window.estop_banner.text())
        self.assertTrue(self.window.centralWidget().property("emergencyStop"))
        self.assertFalse(any(self.toolbar_actions_enabled()))
        self.assertFalse(self.window.controls_box.isEnabled())

    def test_released_estop_offers_only_reset(self) -> None:
        self.set_view(state="e_stop", estop_active=True)
        self.set_view(state="e_stop", estop_active=False)

        self.assertIn("Press Reset", self.window.estop_banner.text())
        self.assertTrue(self.window.reset_action.isEnabled())
        enabled = [action for action in self.window.process_toolbar.actions() if action.isEnabled()]
        self.assertEqual(enabled, [self.window.reset_action])
        self.assertFalse(self.window.controls_box.isEnabled())

    def test_cleared_estop_restores_controls(self) -> None:
        before = self.toolbar_actions_enabled()
        self.set_view(state="e_stop", estop_active=True)
        self.set_view(state="ready", estop_active=False)

        self.assertTrue(self.window.estop_banner.isHidden())
        self.assertFalse(self.window.centralWidget().property("emergencyStop"))
        self.assertEqual(self.toolbar_actions_enabled(), before)
        self.assertTrue(self.window.controls_box.isEnabled())

    def test_gui_buttons_send_the_physical_button_commands(self) -> None:
        self.window.start_process()
        self.window.stop_process()
        self.window.manual_actions["Open door"].trigger()
        self.window.manual_actions["Close door"].trigger()

        self.assertEqual(tuple(self.service.commands[-4:]), OPERATOR_BUTTON_COMMANDS)

    def test_run_started_elsewhere_clears_previous_graph(self) -> None:
        self.window.temperature_curve.setData([0.0, 1.0], [20.0, 21.0])

        self.set_view(state="heating", running=True, run_started_at="2026-10-19T10:00:00",
                      run_settings={"hold_duration_s": 60.0})

        x_data, _y_data = self.window.temperature_curve.getData()
        self.assertTrue(x_data is None or len(x_data) == 0)

    def test_fault_cause_is_shown_in_stage_panel(self) -> None:
        self.set_view(state="fault", detail="sensor_timeout")

        self.assertEqual(self.window.stage_detail_label.text(),
                         "Cause: Temperature sensor is not responding. Fix it, then press Reset.")
        self.assertIn("#ff8a80", self.window.stage_detail_label.styleSheet())

    def test_fault_cause_clears_after_reset(self) -> None:
        self.set_view(state="fault", detail="door_error")
        self.set_view(state="ready", detail="")

        self.assertEqual(self.window.stage_detail_label.text(), "")
        self.assertIn("#9aa1ab", self.window.stage_detail_label.styleSheet())

    def test_quit_closes_window_and_service(self) -> None:
        self.window.show()

        self.window.quit_action.trigger()

        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.service.close_calls, 1)
        self.assertIn(self.window.quit_action, self.window.file_menu.actions())

    def test_quit_shortcut_works_during_estop(self) -> None:
        self.set_view(state="e_stop", estop_active=True)

        self.assertTrue(self.window.quit_action.isEnabled())

    def test_window_closes_even_if_worker_stop_fails(self) -> None:
        service = FakeService(close_error=RuntimeError("Control worker did not stop cleanly"))
        window = KilnWindow(service)
        window._timer.stop()
        window.show()

        with mock.patch("sys.stderr"):
            window.close()

        self.assertFalse(window.isVisible())
        self.assertEqual(service.close_calls, 1)
        window.deleteLater()


if __name__ == "__main__":
    unittest.main()
