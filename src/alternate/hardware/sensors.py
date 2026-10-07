"""Pi sensor adapters; device libraries load only when hardware is created."""
from __future__ import annotations
import statistics
import time
from collections import deque
from enum import Enum

class DoorState(Enum):
    OPEN = "open"
    CLOSED = "closed"
    BETWEEN = "between"
    ERROR = "error"

class DoorSwitches:
    def __init__(self, open_switch, closed_switch):
        self.open_switch, self.closed_switch = open_switch, closed_switch
    def state(self):
        opened, closed = self.open_switch.is_active, self.closed_switch.is_active
        if opened and closed: return DoorState.ERROR
        if opened: return DoorState.OPEN
        if closed: return DoorState.CLOSED
        return DoorState.BETWEEN

class TemperatureSensor:
    def __init__(self, probe, window):
        self.probe, self.buffer = probe, deque(maxlen=window)
        self.started_at = time.monotonic()
        self.last_valid_time = None
    def update(self, now):
        value = self.probe.read_raw()
        if value is not None:
            self.buffer.append(value)
            self.last_valid_time = now
    def temperature(self):
        return statistics.median(self.buffer) if self.buffer else None
    def seconds_since_valid(self, now):
        return now - (self.started_at if self.last_valid_time is None else self.last_valid_time)

class Max31865Probe:
    def __init__(self, config):
        import adafruit_max31865
        import board
        import busio
        import digitalio
        self.spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
        self.cs = digitalio.DigitalInOut(getattr(board, f"D{config.TEMP_CS_PIN}"))
        self.chip = adafruit_max31865.MAX31865(self.spi, self.cs, rtd_nominal=config.RTD_NOMINAL,
            ref_resistor=config.RTD_REF_RESISTOR, wires=config.RTD_WIRES)
    def read_raw(self):
        try:
            value = self.chip.temperature
            if any(self.chip.fault):
                self.chip.clear_faults()
                return None
            return value if -50.0 <= value <= 200.0 else None
        except (OSError, RuntimeError, ValueError):
            return None
    def close(self):
        self.cs.deinit()
        self.spi.deinit()



