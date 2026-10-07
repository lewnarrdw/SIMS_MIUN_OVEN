"""Safety predicates shared by the controller."""
import math
from hardware.sensors import DoorState

class SafetyMonitor:
    def __init__(self, config): self.config = config
    def validate_setpoint(self, value):
        value = float(value)
        if not math.isfinite(value) or not self.config.MIN_SETPOINT <= value <= self.config.MAX_SETPOINT:
            raise ValueError(f"Target must be {self.config.MIN_SETPOINT:g}–{self.config.MAX_SETPOINT:g} °C")
        if value >= self.config.MAX_TEMPERATURE:
            raise ValueError("Target must remain below the over-temperature limit")
        return value
    def check(self, temperature, age_s, door, motor_fault, door_required, estop):
        if estop: return "emergency_stop"
        if age_s >= self.config.SENSOR_FAULT_TIMEOUT_S: return "sensor_timeout"
        if temperature is not None and (not math.isfinite(temperature) or temperature >= self.config.MAX_TEMPERATURE):
            return "over_temperature" if math.isfinite(temperature) else "sensor_invalid"
        if door == DoorState.ERROR: return "door_error"
        if motor_fault: return f"door_motor_{motor_fault}"
        if door_required and door != DoorState.CLOSED: return "door_opened"
        return None
    def door_may_open(self, temperature):
        return temperature is not None and math.isfinite(temperature) and temperature < self.config.DOOR_SAFE_TEMPERATURE
