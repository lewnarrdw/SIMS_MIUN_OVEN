"""Bounded PID controller with filtered derivative, integral zone and anti-windup."""
import math


OUTPUT_MIN = 0.0
OUTPUT_MAX = 100.0
DERIVATIVE_FILTER_TAU_S = 0.9
INTEGRAL_OUTPUT_LIMIT = 100.0


class PID:
    __slots__ = ("kp", "ki", "kd", "integral", "last_temp", "filtered_derivative", "integral_zone")

    def __init__(self, kp, ki, kd, integral_zone=math.inf):
        self.integral = 0.0
        self.last_temp = None
        self.filtered_derivative = 0.0
        self.integral_zone = float(integral_zone)
        self.set_gains(kp, ki, kd)

    def set_gains(self, kp, ki, kd):
        values = tuple(float(value) for value in (kp, ki, kd))
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("PID gains must be finite and non-negative")
        self.kp, self.ki, self.kd = values

    def reset(self):
        self.integral = 0.0
        self.last_temp = None
        self.filtered_derivative = 0.0

    def update(self, target, measured, dt, feedforward=0.0):
        if dt <= 0 or not math.isfinite(dt):
            return 0.0
        error = target - measured
        measured_rate = 0.0 if self.last_temp is None else (measured - self.last_temp) / dt
        filter_alpha = 1.0 - math.exp(-dt / DERIVATIVE_FILTER_TAU_S)
        self.filtered_derivative += (measured_rate - self.filtered_derivative) * filter_alpha
        derivative = -self.kd * self.filtered_derivative
        self.last_temp = measured

        base_output = self.kp * error + derivative + feedforward
        # Far from the target, feed-forward and P/D drive the approach; integrating there
        # stores heat-up error that later carries the oven past the target.
        integral = self.integral
        if abs(error) <= self.integral_zone:
            integral += self.ki * error * dt
        # Conditional integration: while the output is pinned, integrate only up to the
        # limit and never backwards. Back-calculation instead drove the integral far
        # negative during a full-power heat-up, starving the heater near the target.
        if error > 0 and base_output + integral > OUTPUT_MAX:
            integral = max(self.integral, min(integral, OUTPUT_MAX - base_output))
        elif error < 0 and base_output + integral < OUTPUT_MIN:
            integral = min(self.integral, max(integral, OUTPUT_MIN - base_output))
        if math.isfinite(integral):
            self.integral = max(-INTEGRAL_OUTPUT_LIMIT, min(INTEGRAL_OUTPUT_LIMIT, integral))

        corrected = base_output + self.integral
        if not math.isfinite(corrected):
            return OUTPUT_MIN
        return max(OUTPUT_MIN, min(OUTPUT_MAX, corrected))
