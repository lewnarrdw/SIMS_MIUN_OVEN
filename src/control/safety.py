"""Safety rules: decide when the kiln must shut down."""

import math

import config
from hardware.sensors import DoorState

class SafetyMonitor:
    """Checks the kiln's state and reports the first fault it finds."""

    def __init__(self, max_temperature=config.MAX_TEMPERATURE, sensor_timeout=config.SENSOR_FAULT_TIMEOUT_S,
                 min_setpoint=config.MIN_SETPOINT, max_setpoint=config.MAX_SETPOINT):
        self.maxTemp = max_temperature
        self.sensorTimeout = sensor_timeout
        self.minSetpoint = min_setpoint
        self.maxSetpoint = max_setpoint

    def door_may_open(self, temperature):
        """True only when we KNOW the kiln is cool enough to open the door."""
        if temperature is None:
            return False
        return temperature < config.DOOR_SAFE_TEMPERATURE



    def check(self, temperature, seconds_since_valid, door_state, motor_fault):
        """Return None if everything is safe, otherwise a short fault name."""

        if self.sensorTimeout <= seconds_since_valid:
            return "sensor_timeout"
        
        if temperature is not None:
            if math.isnan(temperature):
                return "sensor_invalid"
            if temperature >= self.maxTemp:
                return "over_temperature"
        
        if door_state == DoorState.ERROR:
            return "door_error"
        
        if motor_fault is not None:
            return "door_motor_" + motor_fault
        
        return None


    def validate_setpoint(self, setpoint):

        """Raise ValueError if the setpoint is outside the allowed range."""
        if not (self.minSetpoint <=  setpoint <= self.maxSetpoint):
            raise ValueError(
                f"Setpoint {setpoint} °C is outside the allowed range "
                f"{self.minSetpoint}-{self.maxSetpoint} °C"
            )

if __name__ == "__main__":
    safety = SafetyMonitor()

    # --- normal cases ---
    assert safety.check(30.0, 0.1, DoorState.CLOSED, None) is None
    assert safety.check(51.0, 0.1, DoorState.CLOSED, None) == "over_temperature"
    assert safety.check(None, 3.0, DoorState.CLOSED, None) == "sensor_timeout"
    assert safety.check(30.0, 0.1, DoorState.ERROR, None) == "door_error"
    assert safety.check(51.0, 3.0, DoorState.ERROR, None) == "sensor_timeout"   # order check

    # --- boundaries: reaching a limit is a fault ---
    assert safety.check(49.9, 0.1, DoorState.CLOSED, None) is None
    assert safety.check(50.0, 0.1, DoorState.CLOSED, None) == "over_temperature"
    assert safety.check(30.0, 1.99, DoorState.CLOSED, None) is None
    assert safety.check(30.0, 2.0, DoorState.CLOSED, None) == "sensor_timeout"

    # --- startup and bad readings ---
    assert safety.check(None, 0.1, DoorState.CLOSED, None) is None              # buffer still filling
    assert safety.check(None, float("inf"), DoorState.CLOSED, None) == "sensor_timeout"
    assert safety.check(float("nan"), 0.1, DoorState.CLOSED, None) == "sensor_invalid"

    # --- door may open? ---
    assert safety.door_may_open(25.0) is True
    assert safety.door_may_open(29.9) is True
    assert safety.door_may_open(30.0) is False
    assert safety.door_may_open(45.0) is False
    assert safety.door_may_open(None) is False
    assert safety.door_may_open(float("nan")) is False
    
    # --- new check
    assert safety.check(30.0, 0.1, DoorState.CLOSED, "timeout") == "door_motor_timeout"
    assert safety.check(51.0, 0.1, DoorState.CLOSED, "timeout") == "over_temperature"   # order

    # --- setpoints ---
    safety.validate_setpoint(20.0)
    safety.validate_setpoint(40.0)
    safety.validate_setpoint(45.0)

    def expect_rejected(setpoint):
        try:
            safety.validate_setpoint(setpoint)
        except ValueError:
            return
        assert False, f"{setpoint} should have been rejected"

    for bad in [19.9, 45.1, 60.0, float("nan")]:
        expect_rejected(bad)

    print("All safety checks passed ✅")