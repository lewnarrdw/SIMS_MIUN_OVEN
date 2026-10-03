#Real sensor classes for the kiln

import statistics
import time
from collections import deque


class TemperatureSensor:
    """Turns noisy raw readings from any probe into a clean temperature.

    Works with any probe object that has a read_raw() method returning
    a float in °C, or None if the reading failed.
    """

    def __init__(self, probe, window=15):
        self.probe = probe
        self.buffer = deque(maxlen = window)
        self.last_valid_time = None


    def update(self):
        #Take one raw reading. Call this about 10 times per second.
        reading = self.probe.read_raw()
        if reading is None:
            return
        self.buffer.append(reading)
        self.last_valid_time = time.monotonic()
        

    def temperature(self):
        #Filtered temperature in °C, or None if there are no readings yet.
        if len(self.buffer) < self.buffer.maxlen:  
            return None
        else:
            return statistics.median(self.buffer)


    def seconds_since_valid(self):
        #How long ago the last valid reading arrived.
        if self.last_valid_time is None:
            return float("inf")
        return time.monotonic() - self.last_valid_time
    
class Max31865Probe:
    """Real PT100 probe, read through the MAX31865 board over SPI.

    read_raw() returns one reading in °C, or None if the chip reports
    a fault or the value is physically impossible.
    """

    def __init__(self, cs_pin, rtd_nominal=100.0, ref_resistor=430.0, wires=3):
        import board
        import busio
        import digitalio
        import adafruit_max31865

        spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
        cs = digitalio.DigitalInOut(getattr(board, f"D{cs_pin}"))
        self.chip = adafruit_max31865.MAX31865(
            spi,
            cs,
            rtd_nominal=rtd_nominal,
            ref_resistor=ref_resistor,
            wires=wires,
        )

    def read_raw(self):
        """One unfiltered reading in °C, or None on any problem."""
        try:
            value = self.chip.temperature
        except (OSError, RuntimeError):
            return None

        if any(self.chip.fault):
            self.chip.clear_faults()
            return None

        if not -50.0 <= value <= 200.0:
            return None

        return value


    
if __name__ == "__main__":
    from hardware.simulated import SimulatedKiln, SimulatedHeater, SimulatedTemperatureProbe

    kiln = SimulatedKiln()
    heater = SimulatedHeater(kiln)
    sensor = TemperatureSensor(SimulatedTemperatureProbe(kiln))

    heater.set_percent(100)
    for step in range(150):              # 150 steps of 1/15 s = 10 seconds :)
        kiln.update(1/15)
        sensor.update()
        if step != 0 and step % 15 == 0 :
            print(f"true={kiln.temperature:5.1f}  filtered={sensor.temperature()}  "
                  f"window={len(sensor.buffer)}")