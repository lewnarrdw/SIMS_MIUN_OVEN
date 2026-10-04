#Real sensor classes for the kiln

import threading 
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
    """Reports the door position from two limit switches."""

    def __init__(self, open_switch, closed_switch):
        self.upSensor = open_switch
        self.downSensor = closed_switch

    def state(self):
        """Return a DoorState."""
        open_pressed = self.upSensor.is_pressed
        closed_pressed = self.downSensor.is_pressed

        if open_pressed and closed_pressed: return DoorState.ERROR
        if open_pressed:                    return DoorState.OPEN
        if closed_pressed:                  return DoorState.CLOSED
        return DoorState.BETWEEN

class Buttons:
    """Remembers button presses until the controller collects them."""

    def __init__(self, buttons):
        # buttons is a dict like {"start": btn1, "stop": btn2, ...}
        self._events = {name: threading.Event() for name in buttons}
        for name, button in buttons.items():
            button.when_pressed = self._events[name].set

    def pressed(self, name):
        """True once per press: True if the button was pressed since the last call."""
        event = self._events[name]
        if event.is_set():
            event.clear()
            return True
        return False



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
    from hardware.simulated import SimulatedKiln, SimulatedHeater, SimulatedTemperatureProbe, SimulatedSwitch, SimulatedButton





    # --- Button tests ---
    start_btn = SimulatedButton()
    stop_btn = SimulatedButton()
    open_btn = SimulatedButton()
    close_btn = SimulatedButton()
    buttons = Buttons({
        "start": start_btn,
        "stop": stop_btn,
        "open": open_btn,
        "close": close_btn,
    })

    # 1. nothing pressed yet
    assert buttons.pressed("start") is False

    # 2. one press is seen
    start_btn.press()
    assert buttons.pressed("start") is True

    # 3. ...and only once
    assert buttons.pressed("start") is False

    # 4. two presses between two checks count as one
    start_btn.press()
    start_btn.press()
    assert buttons.pressed("start") is True
    assert buttons.pressed("start") is False

    # 5. pressing start does not trigger stop
    start_btn.press()
    assert buttons.pressed("stop") is False

    print("Button checks passed ✅")
    # kiln = SimulatedKiln()
    # heater = SimulatedHeater(kiln)
    # sensor = TemperatureSensor(SimulatedTemperatureProbe(kiln))
    #     # --- Door switch tests ---
    # open_sw = SimulatedSwitch()
    # closed_sw = SimulatedSwitch()
    # door = DoorSwitches(open_sw, closed_sw)

    # # row 1: only open pressed
    # open_sw.is_pressed = True
    # closed_sw.is_pressed = False
    # assert door.state() == DoorState.OPEN

    # open_sw.is_pressed = True
    # closed_sw.is_pressed = True
    # assert door.state() == DoorState.ERROR

    # open_sw.is_pressed = False
    # closed_sw.is_pressed = True
    # assert door.state() == DoorState.CLOSED

    # open_sw.is_pressed = False
    # closed_sw.is_pressed = False
    # assert door.state() == DoorState.BETWEEN

    # print("Door switch checks passed ✅")

    # heater.set_percent(100)
    # for step in range(150):              # 150 steps of 1/15 s = 10 seconds :)
    #     kiln.update(1/15)
    #     sensor.update()
    #     if step != 0 and step % 15 == 0 :
    #         print(f"true={kiln.temperature:5.1f}  filtered={sensor.temperature()}  "
    #               f"window={len(sensor.buffer)}")