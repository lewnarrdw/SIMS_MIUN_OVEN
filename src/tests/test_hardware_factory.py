"""Tests for how the Pi hardware factory configures GPIO inputs."""

import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest import mock

import config
from hardware import factory


class FakeDevice:
    created: list["FakeDevice"] = []

    def __init__(self, pin, **kwargs):
        self.pin, self.kwargs = pin, kwargs
        self.is_active = False
        FakeDevice.created.append(self)

    def on(self):
        self.is_active = True

    def off(self):
        self.is_active = False

    def close(self):
        pass


def fake_gpiozero() -> ModuleType:
    module = ModuleType("gpiozero")
    module.Button = type("Button", (FakeDevice,), {})
    module.DigitalInputDevice = type("DigitalInputDevice", (FakeDevice,), {})
    module.DigitalOutputDevice = type("DigitalOutputDevice", (FakeDevice,), {})
    return module


class EstopInputTests(unittest.TestCase):
    def create_estop_device(self, active_low: bool) -> FakeDevice:
        FakeDevice.created = []
        settings = SimpleNamespace(**{
            name: getattr(config, name) for name in dir(config) if name.isupper()
        })
        settings.ESTOP_ACTIVE_LOW = active_low
        with mock.patch.dict(sys.modules, {"gpiozero": fake_gpiozero()}), \
                mock.patch.object(factory, "Max31865Probe"):
            factory.create_hardware(settings)
        return next(device for device in FakeDevice.created if device.pin == config.ESTOP_SENSE_PIN)

    def test_active_low_estop_uses_internal_pull_up(self) -> None:
        device = self.create_estop_device(active_low=True)

        self.assertIs(device.kwargs.get("pull_up"), True)
        self.assertNotIn("active_state", device.kwargs)

    def test_active_high_estop_uses_internal_pull_down(self) -> None:
        device = self.create_estop_device(active_low=False)

        self.assertIs(device.kwargs.get("pull_up"), False)
        self.assertNotIn("active_state", device.kwargs)


if __name__ == "__main__":
    unittest.main()
