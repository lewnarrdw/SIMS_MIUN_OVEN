"""PID controller for the kiln heater."""

class PID:
    """Computes heater power (0-100 %) from setpoint and measured temperature."""

    def __init__(self, kp, ki, kd):
        self.kp = kp
        self.ki = ki
        self.kd = kd

        self.integral = 0.0
        self.last_temp = None

    def update(self, targetTemp, currentTemp, dt, feedforward=0):
        """Return heater power in percent (0-100)."""
        if dt <= 0:
            raise ValueError("dt must be positive")
        
        error = targetTemp - currentTemp
        p = error * self.kp

        if self.last_temp is None:
            d = 0
        else:
            change = currentTemp - self.last_temp
            d = -self.kd * change / dt
        self.last_temp = currentTemp
        
        i = self.ki * self.integral
        tentativeIntegral = (error * dt) + self.integral 
        
        if 0 <= p + i + d + feedforward <= 100:
            self.integral = tentativeIntegral

        output = p + i + d + feedforward
        return max(0, min(output, 100))

    def set_gains(self, kp, ki, kd):
        """Change the gains while running, without a jump in heater power."""
        if kp < 0 or ki < 0 or kd < 0:
            raise ValueError("PID gains must be zero or positive")

        if self.ki > 0 and ki > 0:
            self.integral *= self.ki / ki
        else:
            self.integral = 0.0

        self.kp = kp
        self.ki = ki
        self.kd = kd

    def reset(self):
        """Forget the history call when starting an new run"""
        self.integral = 0.0
        self.last_temp = None







##################################################
##################################################
##################################################
##################################################
## TESTTTT!!!!

if __name__ == "__main__":
    from hardware.simulated import SimulatedKiln, SimulatedHeater

    def simulate(kp, ki, kd, setpoint=45.0, seconds=600):
        """Run one heat-up and return (peak, time to reach target, final temp)."""
        kiln = SimulatedKiln()
        heater = SimulatedHeater(kiln)
        pid = PID(kp, ki, kd)
        peak = kiln.temperature
        reached_at = None

        for second in range(seconds):
            power = pid.update(setpoint, kiln.temperature, 1.0)
            heater.set_percent(power)
            kiln.update(1.0)
            peak = max(peak, kiln.temperature)
            if reached_at is None and kiln.temperature >= setpoint - 0.5:
                reached_at = second

        return peak, reached_at, kiln.temperature

    runs = [
        (5.0, 0.1, 1.0),     # A  (reference)
        (10.0, 0.1, 1.0),    # E  stronger P
        (10.0, 0.05, 1.0),   # F  stronger P, weaker I
        (15.0, 0.1, 1.0),    # G  even stronger P
    ]

    for kp, ki, kd in runs:
        peak, reached, final = simulate(kp, ki, kd)
        print(f"kp={kp:4.1f} ki={ki:4.2f} kd={kd:4.1f}  "
              f"peak={peak:5.1f}  reached 44.5 at {reached}s  final={final:5.1f}")

# if __name__ == "__main__":
#     from hardware.simulated import SimulatedKiln, SimulatedHeater

#     kiln = SimulatedKiln()
#     heater = SimulatedHeater(kiln)
#     pid = PID(kp=5.0, ki=0.1, kd=1.0)
#     setpoint = 45.0

#     for second in range(600):
#         power = pid.update(setpoint, kiln.temperature, 1.0)
#         heater.set_percent(power)
#         kiln.update(1.0)
#         if second % 20 == 0:
#             print(f"t={second:3d}s  T={kiln.temperature:5.1f} °C  power={power:5.1f} %")