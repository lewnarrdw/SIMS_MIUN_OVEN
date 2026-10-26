"""Build Raspberry Pi hardware without importing Pi packages at module load."""
from .actuators import DoorMotor, Heater, SignalTower
from .sensors import DoorSwitches, Max31865Probe, TemperatureSensor


class Hardware:
    def __init__(self, devices, sensor, probe, switches, motor, heater, tower, buttons, estop, estop_device):
        self.devices = devices
        self.sensor, self.probe = sensor, probe
        self.switches, self.motor, self.heater = switches, motor, heater
        self.tower, self.buttons = tower, buttons
        self.estop, self.estop_device = estop, estop_device

    def close(self):
        self.heater.emergency_off()
        self.motor.stop()
        # Stop the blink thread before the tower pins are released below.
        self.tower.stop_refresh()
        self.tower.show("idle")
        for device in reversed(self.devices):
            try:
                device.close()
            except Exception:
                pass
        self.probe.close()


def create_hardware(config):
    from gpiozero import Button, DigitalInputDevice, DigitalOutputDevice

    probe = Max31865Probe(config)
    devices = []

    def output(pin, active_high=True):
        device = DigitalOutputDevice(pin, active_high=active_high, initial_value=False)
        devices.append(device)
        return device

    def button(pin, active_low=True):
        device = Button(pin, pull_up=active_low, bounce_time=0.05)
        devices.append(device)
        return device

    try:
        heater_active_low = getattr(config, "HEATER_ACTIVE_LOW", config.RELAY_ACTIVE_LOW)
        heater = Heater(output(config.HEATER_PIN, not heater_active_low), config.HEATER_WINDOW_S)
        # With an internal pull-up, gpiozero infers the active-low state.
        # Explicit active_state is only valid when pull_up=None (floating).
        opened = DigitalInputDevice(config.DOOR_LIMIT_OPEN_PIN, pull_up=True)
        closed = DigitalInputDevice(config.DOOR_LIMIT_CLOSED_PIN, pull_up=True)
        devices.extend((opened, closed))
        switches = DoorSwitches(opened, closed)
        enable_pin = getattr(config, "MOTOR_ENABLE_PIN", None)
        motor_enable = output(enable_pin) if enable_pin is not None else None
        motor = DoorMotor(
            output(config.MOTOR_IN1_PIN), output(config.MOTOR_IN2_PIN),
            motor_enable, switches, config.DOOR_TIMEOUT_S,
        )
        buttons = {
            name: button(pin, getattr(config, f"{name.upper()}_BUTTON_ACTIVE_LOW", True))
            for name, pin in {
                "start": config.BUTTON_START_PIN, "stop": config.BUTTON_STOP_PIN,
                "open": config.BUTTON_OPEN_PIN, "close": config.BUTTON_CLOSE_PIN,
            }.items()
        }
        # Avoid Button bounce filtering on the safety input. The E-Stop circuit
        # drives the released level only while powered, so pull toward the
        # engaged level: power loss or a broken wire then reads as engaged. The
        # device is therefore active only while the release signal is present.
        estop_device = DigitalInputDevice(
            config.ESTOP_SENSE_PIN, pull_up=not config.ESTOP_ACTIVE_LOW,
        )
        devices.append(estop_device)
        estop = lambda: not estop_device.is_active
        # The tower outputs sit on two optoisolators with different polarities.
        tower_active_low = getattr(config, "TOWER_ACTIVE_LOW", {})
        tower_outputs = {
            name: output(pin, not tower_active_low.get(name, config.RELAY_ACTIVE_LOW))
            for name, pin in config.TOWER_PINS.items()
        }
        tower = SignalTower(tower_outputs)
        tower.start_refresh()
        return Hardware(
            devices, TemperatureSensor(probe, config.FILTER_WINDOW), probe,
            switches, motor, heater, tower, buttons, estop, estop_device,
        )
    except Exception:
        for device in reversed(devices):
            try:
                device.close()
            except Exception:
                pass
        probe.close()
        raise
