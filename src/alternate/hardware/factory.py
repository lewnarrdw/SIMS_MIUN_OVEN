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

    def button(pin):
        device = Button(pin, pull_up=True, bounce_time=0.05)
        devices.append(device)
        return device

    try:
        heater = Heater(output(config.HEATER_PIN, not config.RELAY_ACTIVE_LOW), config.HEATER_WINDOW_S)
        opened = DigitalInputDevice(config.DOOR_LIMIT_OPEN_PIN, pull_up=True, active_state=False)
        closed = DigitalInputDevice(config.DOOR_LIMIT_CLOSED_PIN, pull_up=True, active_state=False)
        devices.extend((opened, closed))
        switches = DoorSwitches(opened, closed)
        motor = DoorMotor(
            output(config.MOTOR_IN1_PIN), output(config.MOTOR_IN2_PIN),
            output(config.MOTOR_SLEEP_PIN), switches, config.DOOR_TIMEOUT_S,
        )
        buttons = {
            name: button(pin) for name, pin in {
                "start": config.BUTTON_START_PIN, "stop": config.BUTTON_STOP_PIN,
                "open": config.BUTTON_OPEN_PIN, "close": config.BUTTON_CLOSE_PIN,
            }.items()
        }
        estop_device = button(config.ESTOP_SENSE_PIN)
        estop = lambda: (
            not estop_device.is_pressed if config.ESTOP_NORMALLY_CLOSED
            else estop_device.is_pressed
        )
        tower_outputs = {
            name: output(pin, not config.RELAY_ACTIVE_LOW)
            for name, pin in config.TOWER_PINS.items()
        }
        tower = SignalTower(tower_outputs)
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
