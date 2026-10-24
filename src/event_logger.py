"""Small compatibility logger used by the desktop application."""

from __future__ import annotations


class ErrorHandler:
    """Track the most recent oven safety error."""

    def __init__(self, maximum_temperature=50.0):
        self.maximum_temperature = float(maximum_temperature)
        self.last_error = None

    def clear(self):
        self.last_error = None
        return None

    def check(self, temperature):
        if temperature is None:
            return None
        value = float(temperature)
        if value > self.maximum_temperature:
            self.last_error = "over_temperature"
            return self.last_error
        self.last_error = None
        return None

    def record(self, temperature):
        return self.check(temperature)

    def __call__(self, temperature):
        return self.check(temperature)

    def log(self, temperature):
        return self.check(temperature)
