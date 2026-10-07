"""Bounded PID controller with filtered derivative and anti-windup."""
import math


OUTPUT_MIN = 0.0
OUTPUT_MAX = 100.0
DERIVATIVE_FILTER_TAU_S = 0.9
ANTI_WINDUP_GAIN_PER_S = 2.0
INTEGRAL_OUTPUT_LIMIT = 100.0


class PID:
    __slots__ = ("kp", "ki", "kd", "integral", "last_temp", "filtered_derivative")

    def __init__(self, kp, ki, kd):
        self.integral = 0.0
        self.last_temp = None
        self.filtered_derivative = 0.0
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

        proportional = self.kp * error
        base_output = proportional + derivative + feedforward
        unsaturated = base_output + self.integral
        if math.isfinite(unsaturated):
            output = max(OUTPUT_MIN, min(OUTPUT_MAX, unsaturated))
        else:
            output = OUTPUT_MIN

        # Back-calculation unwinds the integral when output reaches either limit.
        tracking_step = ANTI_WINDUP_GAIN_PER_S * dt
        self.integral = (
            self.integral + self.ki * error * dt + tracking_step * (output - base_output)
        ) / (1.0 + tracking_step)
        self.integral = max(-INTEGRAL_OUTPUT_LIMIT, min(INTEGRAL_OUTPUT_LIMIT, self.integral))

        corrected = base_output + self.integral
        if not math.isfinite(corrected):
            return OUTPUT_MIN
        return max(OUTPUT_MIN, min(OUTPUT_MAX, corrected))

