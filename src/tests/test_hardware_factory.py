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


class FakeInputDevice(FakeDevice):
    """Mimics gpiozero: with a pull-up the input is active LOW, with a pull-down active HIGH."""

    def __init__(self, pin, **kwargs):
        super().__init__(pin, **kwargs)
        # With nothing driving the pin, the pull resistor sets its level.
        self.level = "high" if kwargs.get("pull_up") else "low"

    @property
    def is_active(self) -> bool:
        return (self.level == "low") if self.kwargs.get("pull_up") else (self.level == "high")

    @is_active.setter
    def is_active(self, _value) -> None:
        pass


def fake_gpiozero() -> ModuleType:
    module = ModuleType("gpiozero")
    module.Button = type("Button", (FakeDevice,), {})
    module.DigitalInputDevice = type("DigitalInputDevice", (FakeInputDevice,), {})
    module.DigitalOutputDevice = type("DigitalOutputDevice", (FakeDevice,), {})
    return module


class EstopInputTests(unittest.TestCase):
    def create_hardware(self, active_low: bool):
        FakeDevice.created = []
        settings = SimpleNamespace(**{
            name: getattr(config, name) for name in dir(config) if name.isupper()
        })
        settings.ESTOP_ACTIVE_LOW = active_low
        with mock.patch.dict(sys.modules, {"gpiozero": fake_gpiozero()}), \
                mock.patch.object(factory, "Max31865Probe"):
            hardware = factory.create_hardware(settings)
        self.addCleanup(hardware.tower.stop_refresh)
        device = next(device for device in FakeDevice.created if device.pin == config.ESTOP_SENSE_PIN)
        return hardware, device

    def test_active_low_estop_pulls_down_so_a_missing_signal_reads_engaged(self) -> None:
        hardware, device = self.create_hardware(active_low=True)

        self.assertIs(device.kwargs.get("pull_up"), False)
        self.assertNotIn("active_state", device.kwargs)
        self.assertTrue(hardware.estop())

    def test_active_low_estop_follows_the_driven_level(self) -> None:
        hardware, device = self.create_hardware(active_low=True)

        device.level = "high"
        self.assertFalse(hardware.estop())
        device.level = "low"
        self.assertTrue(hardware.estop())

    def test_active_high_estop_pulls_up_so_a_missing_signal_reads_engaged(self) -> None:
        hardware, device = self.create_hardware(active_low=False)

        self.assertIs(device.kwargs.get("pull_up"), True)
        self.assertNotIn("active_state", device.kwargs)
        self.assertTrue(hardware.estop())

    def test_active_high_estop_follows_the_driven_level(self) -> None:
        hardware, device = self.create_hardware(active_low=False)

        device.level = "low"
        self.assertFalse(hardware.estop())
        device.level = "high"
        self.assertTrue(hardware.estop())


class TowerPolarityTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeDevice.created = []
        settings = SimpleNamespace(**{
            name: getattr(config, name) for name in dir(config) if name.isupper()
        })
        with mock.patch.dict(sys.modules, {"gpiozero": fake_gpiozero()}),                 mock.patch.object(factory, "Max31865Probe"):
            self.hardware = factory.create_hardware(settings)
        self.addCleanup(self.hardware.tower.stop_refresh)

    def device(self, name: str) -> FakeDevice:
        return next(device for device in FakeDevice.created if device.pin == config.TOWER_PINS[name])

    def test_red_on_the_u56_optoisolator_is_driven_active_high(self) -> None:
        self.assertIs(self.device("red").kwargs["active_high"], True)

    def test_u23_outputs_are_driven_active_low(self) -> None:
        for name in ("orange", "yellow", "green", "buzzer"):
            self.assertIs(self.device(name).kwargs["active_high"], False, name)

    def test_every_tower_output_starts_off(self) -> None:
        for name in config.TOWER_PINS:
            self.assertIs(self.device(name).kwargs["initial_value"], False, name)


if __name__ == "__main__":
    unittest.main()
