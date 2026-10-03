"""Safety rules: decide when the kiln must shut down."""

import config


class SafetyMonitor:
    """Checks the kiln's state and reports the first fault it finds."""

    def __init__(self, max_temperature=config.MAX_TEMPERATURE, sensor_timeout=config.SENSOR_FAULT_TIMEOUT_S,
                 min_setpoint=config.MIN_SETPOINT, max_setpoint=config.MAX_SETPOINT):
        self.maxTemp = max_temperature
        self.sensorTimeout = sensor_timeout
        self.minSetpoint = min_setpoint
        self.maxSetpoint = max_setpoint


    def check(self, temperature, seconds_since_valid, door_state):
        """Return None if everything is safe, otherwise a short fault name."""

        if self.sensorTimeout <= seconds_since_valid:
            return "sensor_timeout"
        
        if temperature is not None and temperature >= self.maxTemp:
            return "over_temperature"
        
        if door_state == "error":
            return "door_error"

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

    assert safety.check(30.0, 0.1, "closed") is None
    assert safety.check(51.0, 0.1, "closed") == "over_temperature"
    assert safety.check(None, 3.0, "closed") == "sensor_timeout"
    assert safety.check(30.0, 0.1, "error") == "door_error"
    assert safety.check(51.0, 3.0, "error") == "sensor_timeout"   # order check

    safety.validate_setpoint(40.0)                                # fine, no error
    try:
        safety.validate_setpoint(60.0)
        assert False, "60 °C should have been rejected"
    except ValueError:
        pass                                                      # expected

    print("All safety checks passed ✅")