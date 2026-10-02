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
            return "sensor timeout"
        
        # TODO 1: sensor timeout -> return "sensor_timeout"

        if temperature is not None and temperature > self.maxTemp:
            return "over_temprature"
        # TODO 2: temperature is not None and too high -> return "over_temperature"

        e

        # TODO 3: door_state is "error" -> return "door_error"
        # TODO 4: everything fine -> return None
        ...

    def validate_setpoint(self, setpoint):

        """Raise ValueError if the setpoint is outside the allowed range."""
        # TODO: if setpoint is below min or above max, raise ValueError
        ...