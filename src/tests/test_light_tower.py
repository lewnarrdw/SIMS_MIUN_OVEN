"""Tests for the Manual controls light tower tests."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

import application
import config
from application import Application
from hardware.actuators import SignalTower, light_test_steps, tower_lights
from main import KilnWindow
from tests.test_emergency_stop import FakeHardware, FakeOutput, FakeService, wait_for

LIGHTS = ("red", "orange", "yellow", "green")


def make_tower(state: str = "ready") -> SignalTower:
    tower = SignalTower({name: FakeOutput() for name in config.TOWER_PINS})
    tower.show(state)
    return tower


def lit(tower: SignalTower) -> set[str]:
    return {name for name, output in tower.outputs.items() if output.is_active}


class LightTowerConfigTests(unittest.TestCase):
    def test_lights_follow_the_tested_wiring_with_white_as_orange(self) -> None:
        self.assertEqual(config.TOWER_PINS,
                         {"red": 17, "orange": 24, "yellow": 22, "green": 23, "buzzer": 27})

    def test_lights_are_listed_top_to_bottom(self) -> None:
        self.assertEqual(config.TOWER_LIGHTS, LIGHTS)


class LightTestStepsTests(unittest.TestCase):
    def test_sequence_starts_top_to_bottom_one_light_at_a_time(self) -> None:
        steps = light_test_steps(LIGHTS, 0.6)

        self.assertEqual([lit for lit, _ in steps[:4]], [{"red"}, {"orange"}, {"yellow"}, {"green"}])
        self.assertEqual([duration for _, duration in steps[:4]], [0.6] * 4)

    def test_sequence_lights_every_light_together_and_ends_dark(self) -> None:
        steps = light_test_steps(LIGHTS, 0.6)

        self.assertIn(set(LIGHTS), [lit for lit, _ in steps])
        self.assertEqual(steps[-1][0], set())

    def test_sequence_never_sounds_the_buzzer(self) -> None:
        for lit_lights, duration_s in light_test_steps(LIGHTS, 0.6):
            self.assertLessEqual(lit_lights, set(LIGHTS))
            self.assertGreater(duration_s, 0.0)


class SignalTowerLightTestTests(unittest.TestCase):
    def test_steps_replace_the_state_light_until_they_end(self) -> None:
        tower = make_tower()

        tower.test_lights(10.0, [({"red"}, 1.0), ({"orange", "green"}, 0.5)])

        self.assertEqual(lit(tower), {"red"})
        tower.update(10.99)
        self.assertEqual(lit(tower), {"red"})
        tower.update(11.0)
        self.assertEqual(lit(tower), {"orange", "green"})
        tower.update(11.5)
        self.assertEqual(lit(tower), {"green"})

    def test_late_update_skips_steps_that_already_ended(self) -> None:
        tower = make_tower()
        tower.test_lights(0.0, [({"red"}, 0.2), ({"orange"}, 0.2), ({"yellow"}, 0.2)])

        tower.update(0.45)

        self.assertEqual(lit(tower), {"yellow"})

    def test_state_changes_during_a_test_return_after_it(self) -> None:
        tower = make_tower()
        tower.test_lights(0.0, [({"red"}, 1.0)])

        tower.show("ready")
        tower.set_conditions(door_moving=False, hot=True)

        self.assertEqual(lit(tower), {"red"})
        tower.update(1.0)
        self.assertEqual(lit(tower), {"green", "orange"})

    def test_an_alarm_cancels_the_test_at_once(self) -> None:
        tower = make_tower()
        tower.test_lights(0.0, [({"green"}, 5.0)])

        tower.show("e_stop")

        self.assertEqual(lit(tower), {"red"})
        tower.show("ready")
        self.assertEqual(lit(tower), {"green"})

    def test_test_is_refused_while_an_alarm_shows(self) -> None:
        tower = make_tower("fault")

        with self.assertRaises(RuntimeError):
            tower.test_lights(0.0, [({"green"}, 1.0)])
        self.assertEqual(lit(tower), {"red", "buzzer"})  # On half of the fault blink

    def test_buzzer_test_sounds_alongside_a_light_test(self) -> None:
        tower = make_tower()
        tower.test_lights(0.0, [({"orange"}, 2.0)])

        tower.test_buzzer(0.0, 1.0)

        self.assertEqual(lit(tower), {"orange", "buzzer"})
        tower.update(1.0)
        self.assertEqual(lit(tower), {"orange"})

    def test_empty_test_shows_the_state_light(self) -> None:
        tower = make_tower()

        tower.test_lights(0.0, [])

        self.assertEqual(lit(tower), {"green"})


class TowerLightSchemeTests(unittest.TestCase):
    def test_ready_shows_steady_green(self) -> None:
        for now in (0.0, 0.3, 0.6, 0.9):
            self.assertEqual(tower_lights("ready", now), {"green"})

    def test_aborted_run_shows_steady_green(self) -> None:
        self.assertEqual(tower_lights("aborted", 0.7), {"green"})

    def test_run_blinks_green_at_one_hertz(self) -> None:
        for state in ("door_moving", "heating", "holding", "cooling"):
            self.assertEqual(tower_lights(state, 10.2), {"green"})
            self.assertEqual(tower_lights(state, 10.7), set())

    def test_completed_run_blinks_green_at_two_hertz(self) -> None:
        self.assertEqual(tower_lights("complete", 10.1), {"green"})
        self.assertEqual(tower_lights("complete", 10.3), set())
        self.assertEqual(tower_lights("complete", 10.6), {"green"})

    def test_estop_shows_steady_red_without_green(self) -> None:
        for now in (0.0, 0.7):
            self.assertEqual(tower_lights("e_stop", now), {"red"})

    def test_fault_blinks_red_at_one_hertz(self) -> None:
        self.assertEqual(tower_lights("fault", 3.2), {"red"})
        self.assertEqual(tower_lights("fault", 3.7), set())

    def test_moving_door_blinks_yellow_at_four_hertz(self) -> None:
        on = [tower_lights("ready", now / 1000, door_moving=True) for now in (0, 100, 125, 240, 250, 374)]

        self.assertEqual(["yellow" in lights for lights in on], [True, True, False, False, True, True])

    def test_door_blinks_yellow_in_every_state(self) -> None:
        for state in ("ready", "heating", "complete", "fault", "e_stop"):
            self.assertIn("yellow", tower_lights(state, 0.05, door_moving=True))

    def test_moving_door_shows_only_yellow(self) -> None:
        for state in ("ready", "heating", "door_moving", "complete", "aborted"):
            for now in (0.05, 0.2, 0.55, 0.7):
                lights = tower_lights(state, now, door_moving=True, hot=True)
                self.assertLessEqual(lights, {"yellow"}, (state, now))

    def test_moving_door_still_shows_an_alarm(self) -> None:
        self.assertEqual(tower_lights("e_stop", 0.05, door_moving=True, hot=True), {"red", "yellow"})
        self.assertEqual(tower_lights("fault", 0.05, door_moving=True), {"red", "yellow"})
        self.assertEqual(tower_lights("fault", 0.7, door_moving=True), set())

    def test_lights_return_once_the_door_stops(self) -> None:
        self.assertEqual(tower_lights("ready", 0.2, door_moving=False, hot=True), {"green", "orange"})

    def test_hot_oven_shows_steady_orange_in_every_state(self) -> None:
        for state in ("ready", "heating", "complete", "aborted", "fault", "e_stop"):
            for now in (0.1, 0.6, 0.9):
                self.assertIn("orange", tower_lights(state, now, hot=True))
        self.assertNotIn("orange", tower_lights("ready", 0.0, hot=False))


class SignalTowerSchemeTests(unittest.TestCase):
    def test_moving_door_blinks_yellow_as_the_tower_updates(self) -> None:
        tower = make_tower()
        tower.set_conditions(door_moving=True, hot=False)

        tower.update(0.0)
        self.assertEqual(lit(tower), {"yellow"})
        tower.update(0.13)
        self.assertEqual(lit(tower), set())
        tower.set_conditions(door_moving=False, hot=False)
        tower.update(0.25)
        self.assertEqual(lit(tower), {"green"})

    def test_stale_update_time_does_not_relight_a_blink(self) -> None:
        tower = make_tower()
        tower.set_conditions(door_moving=True, hot=False)
        tower.update(0.13)

        tower.update(0.12)

        self.assertEqual(lit(tower), set())

    def test_completed_run_beeps_three_times(self) -> None:
        tower = make_tower("cooling")
        tower.update(100.0)

        tower.show("complete")
        beeps = []
        for step in range(100):
            tower.update(100.0 + step / 100)
            beeps.append("buzzer" in lit(tower))

        rising_edges = sum(1 for before, after in zip([False] + beeps, beeps) if after and not before)
        self.assertEqual(rising_edges, 3)
        self.assertFalse(beeps[-1])

    def test_repeated_complete_state_does_not_beep_again(self) -> None:
        tower = make_tower("cooling")
        tower.show("complete")
        tower.update(0.0)
        tower.update(1.0)

        tower.show("complete")
        tower.update(1.05)

        self.assertNotIn("buzzer", lit(tower))

    def test_leaving_complete_stops_the_beeps(self) -> None:
        tower = make_tower("cooling")
        tower.show("complete")
        tower.update(0.0)

        tower.show("ready")
        tower.update(0.05)

        self.assertNotIn("buzzer", lit(tower))

    def test_refresh_thread_blinks_without_updates_and_stops_on_close(self) -> None:
        tower = make_tower()
        tower.set_conditions(door_moving=True, hot=False)
        changes = []
        yellow = tower.outputs["yellow"]
        original_on = yellow.on
        yellow.on = lambda: (changes.append("on"), original_on())

        tower.start_refresh(0.005)
        self.assertTrue(wait_for(lambda: len(changes) >= 2, timeout_s=2.0))
        tower.close()
        count = len(changes)
        wait_for(lambda: False, timeout_s=0.3)

        self.assertIsNone(tower._refresh_thread)
        self.assertEqual(len(changes), count)


class ApplicationLightTestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_directory = tempfile.TemporaryDirectory()
        test_config = SimpleNamespace(**{
            name: getattr(config, name) for name in dir(config) if name.isupper()
        })
        test_config.DATA_DIRECTORY = self.data_directory.name
        test_config.SINGLE_LIGHT_TEST_S = 0.3
        test_config.LIGHT_TEST_STEP_S = 0.03
        self.hardware = FakeHardware()
        with mock.patch.object(application, "create_hardware", return_value=self.hardware):
            self.app = Application(test_config)
        self.assertTrue(wait_for(lambda: self.app.snapshot()[0].state == "ready"))

    def tearDown(self) -> None:
        self.app.close()
        self.data_directory.cleanup()

    def lit(self) -> set[str]:
        return lit(self.hardware.tower)

    def events(self, event_type: str) -> list[dict]:
        return [event for event in self.app.snapshot()[0].events if event["type"] == event_type]

    def test_single_light_test_lights_only_that_light_then_restores_green(self) -> None:
        self.app.command("test_light_orange")

        self.assertTrue(wait_for(lambda: self.lit() == {"orange"}))
        self.assertTrue(wait_for(lambda: self.lit() == {"green"}))
        self.assertEqual(self.events("manual_control")[-1]["description"],
                         "Manual: orange light test (BCM GPIO24)")

    def test_full_light_test_runs_and_restores_green(self) -> None:
        self.app.command("test_lights")

        self.assertTrue(wait_for(lambda: self.lit() != {"green"}))
        self.assertTrue(wait_for(lambda: self.lit() == {"green"}, timeout_s=5.0))
        self.assertEqual(self.events("manual_control")[-1]["description"], "Manual: light tower test")

    def test_unknown_light_is_rejected(self) -> None:
        self.app.command("test_light_blue")

        self.assertTrue(wait_for(lambda: len(self.events("command_rejected")) == 1))
        self.assertEqual(self.lit(), {"green"})


class LightTowerMenuTests(unittest.TestCase):
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

    def test_manual_controls_offer_a_light_tower_submenu(self) -> None:
        self.assertIn(self.window.light_tower_menu.menuAction(), self.window.manual_menu.actions())
        labels = [action.text() for action in self.window.light_tower_menu.actions() if not action.isSeparator()]
        self.assertEqual(labels, ["Test all lights", "Test red light (GPIO17)", "Test orange light (GPIO24)",
                                  "Test yellow light (GPIO22)", "Test green light (GPIO23)"])

    def test_light_actions_send_their_commands(self) -> None:
        self.service.commands.clear()

        self.window.manual_actions["Test all lights"].trigger()
        self.window.manual_actions["Test yellow light (GPIO22)"].trigger()

        self.assertEqual(self.service.commands, ["test_lights", "test_light_yellow"])


if __name__ == "__main__":
    unittest.main()
