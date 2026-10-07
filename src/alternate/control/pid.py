"""Small bounded PID controller with derivative on measurement."""
import math


class PID:
    __slots__ = ("kp", "ki", "kd", "integral", "last_temp")

    def __init__(self, kp, ki, kd):
        self.integral, self.last_temp = 0.0, None
        self.set_gains(kp, ki, kd)

    def set_gains(self, kp, ki, kd):
        values = tuple(float(value) for value in (kp, ki, kd))
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("PID gains must be finite and non-negative")
        old_ki = getattr(self, "ki", 0.0)
        self.integral = self.integral * old_ki / values[1] if old_ki and values[1] else 0.0
        self.kp, self.ki, self.kd = values

    def reset(self):
        self.integral, self.last_temp = 0.0, None

    def update(self, target, measured, dt, feedforward=0.0):
        if dt <= 0 or not math.isfinite(dt):
            return 0.0
        error = target - measured
        derivative = 0.0 if self.last_temp is None else -self.kd * (measured - self.last_temp) / dt
        self.last_temp = measured
        candidate = self.integral + error * dt
        raw = self.kp * error + self.ki * candidate + derivative + feedforward
        if 0.0 < raw < 100.0:
            self.integral = candidate
        return max(0.0, min(100.0, raw if math.isfinite(raw) else 0.0))

