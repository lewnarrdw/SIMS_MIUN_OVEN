"""The state machine: decides what the kiln does, once per loop."""

from enum import Enum

from hardware.actuators import MotorState


class State(Enum):
    IDLE = "idle"
    DOOR_MOVING = "door_moving"
    HEATING = "heating"
    HOLDING = "holding"
    COOLING = "cooling"
    FAULT = "fault"
    E_STOP = "e_stop"


class Controller:
    """Runs the kiln. Call step(now) once per loop."""

    def __init__(self, sensor, switches, motor, heater, safety, tower):
        self.sensor = sensor
        self.switches = switches
        self.motor = motor
        self.heater = heater
        self.safety = safety
        self.tower = tower
        self.state = State.IDLE
        self.fault_reason = None

    def _enter_fault(self, reason):
        self.heater.off()
        self.motor.stop()
        self.state = State.FAULT
        self.fault_reason = reason

    def step(self, now):
        if self.state in (State.FAULT, State.E_STOP):
            self.heater.off()
            return
        
        temperature = self.sensor.temperature()
        since_valid = self.sensor.seconds_since_valid()
        door = self.switches.state()

        fault = self.safety.check(temperature, since_valid, door, self.motor.fault_reason)
        if fault is not None:
            self._enter_fault(fault)
            return









if __name__ == "__main__":
    from types import SimpleNamespace
    from hardware.simulated import SimulatedPin, SimulatedSwitch
    from hardware.sensors import DoorSwitches
    from hardware.actuators import DoorMotor, Heater, NullTower
    from control.safety import SafetyMonitor

    class FakeSensor:
        """Stands in for TemperatureSensor: tests set temp and age by hand."""
        def __init__(self):
            self.temp = 25.0
            self.age = 0.1
        def temperature(self):
            return self.temp
        def seconds_since_valid(self):
            return self.age

    def make_controller():
        r = SimpleNamespace()
        r.sensor = FakeSensor()
        r.open_sw, r.closed_sw = SimulatedSwitch(), SimulatedSwitch()
        r.closed_sw.is_pressed = True                  # door starts closed
        r.switches = DoorSwitches(r.open_sw, r.closed_sw)
        r.in1, r.in2, r.sleep = SimulatedPin(), SimulatedPin(), SimulatedPin()
        r.motor = DoorMotor(r.in1, r.in2, r.sleep, r.switches)
        r.heater_pin = SimulatedPin()
        r.heater = Heater(r.heater_pin)
        r.controller = Controller(r.sensor, r.switches, r.motor, r.heater,
                                  SafetyMonitor(), NullTower())
        return r
    
    # 1: healthy
    r = make_controller()
    r.controller.step(0.0)
    assert r.controller.state == State.IDLE
    assert r.controller.fault_reason is None

    # 2: over temperature
    r = make_controller()
    r.sensor.temp = 50.0
    r.controller.step(0.0)
    assert r.controller.state == State.FAULT
    assert r.controller.fault_reason == "over_temperature"

    # 3: the heater is cut immediately
    r = make_controller()
    r.heater.set_percent(100)
    r.heater.update(0.0)
    assert r.heater_pin.is_on is True
    r.sensor.temp = 50.0
    r.controller.step(0.0)
    assert r.heater_pin.is_on is False
    assert r.heater.percent == 0

    # 4: sensor timeout
    r = make_controller()
    r.sensor.age = 2.0
    r.controller.step(0.0)
    assert r.controller.fault_reason == "sensor_timeout"

    # 5: unknown temperature with a fresh timestamp is not a fault (startup)
    r = make_controller()
    r.sensor.temp = None
    r.controller.step(0.0)
    assert r.controller.state == State.IDLE

    # 6: door error from the switches
    r = make_controller()
    r.open_sw.is_pressed = True          # closed_sw is already pressed -> both pressed
    r.controller.step(0.0)
    assert r.controller.fault_reason == "door_error"

    # 7: door motor fault (switches look normal afterwards)
    r = make_controller()
    r.open_sw.is_pressed = True
    r.motor.open(0.0)                    # both pressed -> the motor faults
    assert r.motor.state == MotorState.FAULT
    r.open_sw.is_pressed = False
    r.closed_sw.is_pressed = False
    r.controller.step(0.0)
    assert r.controller.fault_reason == "door_motor_switch_error"

    # 8: FAULT is sticky, and keeps the heater off
    r = make_controller()
    r.sensor.temp = 50.0
    r.controller.step(0.0)
    r.sensor.temp = 25.0                 # the problem "goes away"
    r.heater_pin.on()                    # something turns the heater on behind our back
    r.controller.step(1.0)
    assert r.controller.state == State.FAULT
    assert r.heater_pin.is_on is False

    print("Controller safety checks passed ✅")
    