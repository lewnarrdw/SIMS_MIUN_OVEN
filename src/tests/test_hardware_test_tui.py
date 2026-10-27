"""Tests for standalone hardware diagnostic output actions."""

import sys
import unittest
from types import ModuleType
from unittest import mock

import hardware_test_tui


class FakeDevice:
    def __init__(self, pin, **kwargs):
        self.pin = pin
        self.kwargs = kwargs
        self.off_calls = 0
        self.is_active = False
        self.is_pressed = False

    def on(self):
        self.is_active = True

    def off(self):
        self.off_calls += 1
        self.is_active = False

    def close(self):
        pass


def fake_gpiozero() -> ModuleType:
    module = ModuleType("gpiozero")
    module.Button = FakeDevice
    module.DigitalInputDevice = FakeDevice
    module.DigitalOutputDevice = FakeDevice
    return module


class BuzzerHighTest(unittest.TestCase):
    def test_low_buzzer_test_uses_the_active_high_output_off_state(self) -> None:
        with mock.patch.dict(sys.modules, {"gpiozero": fake_gpiozero()}):
            diagnostics = hardware_test_tui.PiDiagnostics()

        buzzer = diagnostics.outputs["Tower buzzer"]
        self.assertTrue(buzzer.kwargs["active_high"])

        with mock.patch.object(hardware_test_tui.time, "sleep") as sleep:
            diagnostics.test_buzzer_low()

        self.assertEqual(buzzer.off_calls, 2)
        sleep.assert_called_once_with(1.0)


if __name__ == "__main__":
    unittest.main()
