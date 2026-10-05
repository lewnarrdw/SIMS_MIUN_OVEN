"""Simulated hardware: lets the whole system run without a Raspberry Pi."""


import math
import random


class SimulatedKiln:
    """Very simple physics model of the kiln's temperature."""

    def __init__(self, room_temp=20.0, heat_rate=0.5, loss_rate=0.005):
        self.room_temp = room_temp # T-room
        self.heat_rate = heat_rate # a
        self.loss_rate = loss_rate # b

        self.temperature = self.room_temp # T
        self.heater_percent = 0.0 # p 

    def update(self, dt):
        p_100 = self.heater_percent / 100
        a = self.heat_rate
        b = self.loss_rate
        del_temp = self.temperature - self.room_temp
        heat_in = a * p_100
        heat_loss = b * del_temp

        self.temperature += (heat_in - heat_loss) * dt

        


class SimulatedHeater:
    """Fake heater: instead of switching a pin, it changes the kiln model."""

    def __init__(self, kiln: SimulatedKiln):
        self.kiln = kiln

    def set_percent(self, percent):
        if math.isnan(percent):
            percent = 0
        result = max(0, min(100, percent))
        self.kiln.heater_percent = result


class SimulatedTemperatureProbe:
    """Fake PT100 + MAX31865: the kiln model's temperature plus noise,
    occasional spikes, and occasional failed readings."""

    def __init__(self, kiln: SimulatedKiln, noise=0.2, spike_chance=0.02, fail_chance=0.01):
        self.kiln = kiln
        self.noise = noise                  # normal measurement jitter (°C)
        self.spike_chance = spike_chance    # 2 % of readings are way off
        self.fail_chance = fail_chance      # 1 % of readings fail completely

    def read_raw(self):
        """One unfiltered reading in °C, or None if the reading failed."""
        if random.random() < self.fail_chance:
            return None
        value = self.kiln.temperature + random.gauss(0, self.noise)
        if random.random() < self.spike_chance:
            value += random.choice([15, -15])
        return value

class SimulatedSwitch:
    """A fake limit switch. Set is_pressed by hand in tests."""

    def __init__(self):
        self.is_pressed = False

class SimulatedButton:
    """A fake push button. Call press() in tests, like a finger would."""

    def __init__(self):
        self.is_pressed = False
        self.when_pressed = None     # the code under test assigns a function here

    def press(self):
        self.is_pressed = True
        if self.when_pressed is not None:
            self.when_pressed()      # like gpiozero firing its callback

    def release(self):
        self.is_pressed = False
        if self.when_released is not None:
            self.when_released()
        
class SimulatedPin:
    """A fake output pin. Stands in for gpiozero's DigitalOutputDevice."""

    def __init__(self):
        self.is_on = False

    def on(self):
        self.is_on = True

    def off(self):
        self.is_on = False

class SimulatedDoor:
    """Fake door. Moves when the motor pins say so, presses the limit switches."""

    def __init__(self, in1, in2, open_switch, closed_switch, travel_time=5.0):
        self.in1 = in1
        self.in2 = in2
        self.open_switch = open_switch
        self.closed_switch = closed_switch
        self.travel_time = travel_time
        self.position = 0.0            # 0 = closed, 1 = open
        self.stuck = False             # set True to simulate a jammed door
        self._update_switches()

    def update(self, dt):
        if not self.stuck:
            if self.in1.is_on and not self.in2.is_on:        # opening
                self.position += dt / self.travel_time
            elif self.in2.is_on and not self.in1.is_on:      # closing
                self.position -= dt / self.travel_time
            self.position = max(0.0, min(1.0, self.position))
        self._update_switches()

    def _update_switches(self):
        self.open_switch.is_pressed = self.position >= 1.0 - 1e-9
        self.closed_switch.is_pressed = self.position <= 1e-9

if __name__ == "__main__":
    kiln = SimulatedKiln()
    heater = SimulatedHeater(kiln)

    heater.set_percent(100)
    for second in range(300):
        kiln.update(1.0)
        if second % 30 == 0:
            print(f"t={second:3d}s  T={kiln.temperature:6.1f} °C  (heating)")

    heater.set_percent(0)
    for second in range(300, 600):
        kiln.update(1.0)
        if second % 30 == 0:
            print(f"t={second:3d}s  T={kiln.temperature:6.1f} °C  (cooling)")