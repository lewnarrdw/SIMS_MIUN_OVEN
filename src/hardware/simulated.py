"""Simulated hardware: lets the whole system run without a Raspberry Pi."""


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