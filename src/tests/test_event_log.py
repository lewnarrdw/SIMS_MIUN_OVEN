"""Tests for the events the application records in the run event log."""

import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import application
import config
from application import Application
from tests.test_emergency_stop import FakeHardware, wait_for


class EventLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_directory = tempfile.TemporaryDirectory()
        test_config = SimpleNamespace(**{
            name: getattr(config, name) for name in dir(config) if name.isupper()
        })
        test_config.DATA_DIRECTORY = self.data_directory.name
        self.hardware = FakeHardware()
        with mock.patch.object(application, "create_hardware", return_value=self.hardware):
            self.app = Application(test_config)
        self.assertTrue(wait_for(lambda: self.state() == "ready"))

    def tearDown(self) -> None:
        self.app.close()
        self.data_directory.cleanup()

    def state(self) -> str:
        return self.app.snapshot()[0].state

    def events(self) -> list[dict]:
        return list(self.app.snapshot()[0].events)

    def events_of(self, event_type: str) -> list[dict]:
        return [event for event in self.events() if event["type"] == event_type]

    def wait_for_event(self, event_type: str, count: int = 1) -> list[dict]:
        self.assertTrue(wait_for(lambda: len(self.events_of(event_type)) >= count),
                        f"no {event_type} event in {self.events()}")
        return self.events_of(event_type)

    def start_run(self) -> None:
        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].can_start))
        self.app.command("start")
        self.assertTrue(wait_for(lambda: self.state() == "heating"))

    def run_end_events(self) -> list[dict]:
        return [event for event in self.events()
                if event["type"] in ("run_aborted", "run_faulted", "run_completed")]

    def test_application_start_is_logged(self) -> None:
        self.assertEqual(self.events()[0]["description"], "Application started")

    def test_operator_stop_aborts_the_run_with_its_own_cause(self) -> None:
        self.start_run()

        self.app.command("stop")

        aborted = self.wait_for_event("run_aborted")
        self.assertEqual(aborted[0]["description"], "Run aborted: stopped by operator")
        self.assertEqual(aborted[0]["cause"], "operator_stop")
        self.assertEqual(aborted[0]["severity"], "warning")
        self.assertEqual(aborted[0]["stopped_during"], "heating")
        self.assertEqual(len(self.run_end_events()), 1)

    def test_estop_during_a_run_is_distinct_from_an_operator_stop(self) -> None:
        self.start_run()

        self.hardware.estop_engaged = True

        aborted = self.wait_for_event("run_aborted")
        self.assertEqual(aborted[0]["description"], "Run aborted: emergency stop (E-Stop)")
        self.assertEqual(aborted[0]["cause"], "emergency_stop")
        self.assertEqual(aborted[0]["severity"], "error")
        self.assertEqual([event["description"] for event in self.events_of("emergency_stop")],
                         ["E-Stop engaged"])
        self.assertEqual(len(self.run_end_events()), 1)

    def test_estop_while_idle_is_logged_without_a_run_abort(self) -> None:
        self.hardware.estop_engaged = True
        self.wait_for_event("emergency_stop")

        self.hardware.estop_engaged = False

        self.wait_for_event("estop_released")
        self.assertEqual(self.run_end_events(), [])

    def test_estop_release_after_a_run_is_kept_in_the_run_log(self) -> None:
        self.start_run()
        self.hardware.estop_engaged = True
        self.wait_for_event("run_aborted")

        self.hardware.estop_engaged = False

        released = self.wait_for_event("estop_released")
        self.assertEqual(released[0]["description"], "E-Stop released")
        self.assertEqual(self.events_of("run_started")[0]["description"], "Run started")

    def test_restart_after_operator_stop_names_the_previous_outcome(self) -> None:
        self.start_run()
        self.app.command("stop")
        self.assertTrue(wait_for(lambda: self.state() == "aborted"))

        self.start_run()

        started = self.wait_for_event("run_started")
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["description"],
                         "Run restarted after previous run ended: stopped by operator")
        self.assertEqual(started[0]["previous_run"], "stopped by operator")

    def test_restart_after_estop_names_the_estop(self) -> None:
        self.start_run()
        self.hardware.estop_engaged = True
        self.wait_for_event("run_aborted")
        self.hardware.estop_engaged = False
        self.wait_for_event("estop_released")

        self.start_run()

        started = self.wait_for_event("run_started")
        self.assertEqual(started[0]["description"],
                         "Run restarted after previous run ended: emergency stop (E-Stop)")

    def test_door_opened_during_a_run_is_logged_as_the_cause(self) -> None:
        self.start_run()

        self.hardware.door_closed.is_active = False
        self.hardware.door_open.is_active = True

        aborted = self.wait_for_event("run_aborted")
        self.assertEqual(aborted[0]["description"], "Run aborted: door opened during heating")
        self.assertEqual(aborted[0]["cause"], "door_opened")
        self.assertEqual(len(self.run_end_events()), 1)

    def test_manual_heater_off_during_a_run_is_logged_as_the_cause(self) -> None:
        self.start_run()

        self.app.command("heater_off")

        aborted = self.wait_for_event("run_aborted")
        self.assertEqual(aborted[0]["description"], "Run aborted: heater turned off manually")
        self.assertEqual(aborted[0]["cause"], "manual_heater_off")
        self.assertIn("Manual: heater off", [event["description"] for event in self.events()])

    def test_fault_during_a_run_ends_it_and_clearing_is_logged(self) -> None:
        self.start_run()

        self.hardware.sensor.temperature_c = 60.0

        faulted = self.wait_for_event("run_faulted")
        self.assertEqual(faulted[0]["description"],
                         "Run aborted: temperature reached the safety limit")
        self.assertEqual(faulted[0]["severity"], "error")
        self.assertEqual(len(self.run_end_events()), 1)
        self.hardware.sensor.temperature_c = 22.0
        cleared = self.wait_for_event("fault_cleared")
        self.assertEqual(cleared[0]["description"],
                         "Fault cleared: Temperature reached the safety limit")

    def test_fault_while_idle_is_a_safety_fault_not_a_run_end(self) -> None:
        self.hardware.sensor.temperature_c = 60.0

        fault = self.wait_for_event("safety_fault")

        self.assertEqual(fault[0]["severity"], "error")
        self.assertEqual(self.run_end_events(), [])

    def test_rejected_command_is_logged_as_a_warning(self) -> None:
        self.hardware.estop_engaged = True
        self.wait_for_event("emergency_stop")

        self.app.command("start")

        rejected = self.wait_for_event("command_rejected")
        self.assertTrue(rejected[0]["description"].startswith("Start rejected: Emergency stop is active"))
        self.assertEqual(rejected[0]["severity"], "warning")
        self.assertEqual(rejected[0]["command"], "start")

    def test_manual_controls_are_logged(self) -> None:
        self.app.command("test_buzzer")
        self.app.command("open")

        self.wait_for_event("manual_control", count=2)

        descriptions = [event["description"] for event in self.events_of("manual_control")]
        self.assertEqual(descriptions, ["Manual: buzzer test", "Manual: door opening"])

    def test_events_record_wall_clock_time(self) -> None:
        event = self.events()[0]

        self.assertEqual(datetime.fromisoformat(event["time"]).date(), datetime.now().date())
        self.assertNotIn("run_elapsed_s", event)

    def test_run_events_record_time_since_the_run_started(self) -> None:
        self.start_run()

        started = self.wait_for_event("run_started")[0]

        self.assertEqual(started["run_elapsed_s"], started["elapsed_s"])

    def test_start_during_cool_down_is_rejected_with_a_reason(self) -> None:
        self.app.close()
        self.data_directory.cleanup()
        self.data_directory = tempfile.TemporaryDirectory()
        test_config = SimpleNamespace(**{name: getattr(config, name) for name in dir(config) if name.isupper()})
        test_config.DATA_DIRECTORY = self.data_directory.name
        test_config.STABLE_TIME_S = 0.1
        self.hardware = FakeHardware()
        self.hardware.sensor.temperature_c = 45.0
        with mock.patch.object(application, "create_hardware", return_value=self.hardware):
            self.app = Application(test_config)
        self.app.command("settings", {"target_temperature_c": 45.0, "hold_duration_s": 1.0,
                                      "kp": config.PID_KP, "ki": config.PID_KI, "kd": config.PID_KD})
        self.start_run()
        self.assertTrue(wait_for(lambda: self.state() == "cooling", timeout_s=5.0))

        self.app.command("start")

        rejected = self.wait_for_event("command_rejected")
        self.assertEqual(rejected[0]["description"],
                         "Start rejected: Wait for the cool-down to finish before starting a new run")
        self.assertEqual(self.state(), "cooling")
        self.assertEqual(self.app.snapshot()[0].ready_reason, "Wait for the cool-down to finish")

    def test_reset_keeps_the_fault_history(self) -> None:
        self.start_run()
        self.app.controller.heat_check_since = None
        self.hardware.sensor.temperature_c = 60.0
        self.wait_for_event("run_faulted")
        self.hardware.sensor.temperature_c = 22.0
        self.wait_for_event("fault_cleared")

        self.app.command("reset")

        reset = self.wait_for_event("reset")[0]
        self.assertIn("run_faulted", [event["type"] for event in self.events()])
        self.assertNotIn("run_elapsed_s", reset)

    def test_door_between_limit_switches_is_its_own_event(self) -> None:
        self.hardware.door_closed.is_active = False

        between = self.wait_for_event("door_between")

        self.assertEqual(between[0]["description"], "Door between limit switches")
        self.assertEqual(self.events_of("door_closed"), [])

    def test_event_log_is_capped(self) -> None:
        self.app.close()

        with mock.patch.object(application, "MAX_EVENTS", 5):
            for index in range(8):
                self.app._append_event("test", f"Event {index}")

        self.assertEqual([event["description"] for event in self.app._events],
                         [f"Event {index}" for index in range(3, 8)])


if __name__ == "__main__":
    unittest.main()
