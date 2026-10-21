"""Heater, door motor and signal tower output drivers."""
from enum import Enum
import math
from .sensors import DoorState

class Heater:
    def __init__(self, output, window_s):
        self.output, self.window_s = output, window_s
        self.percent, self.locked = 0.0, False
        self.output.off()
    def set_percent(self, percent):
        value = float(percent)
        if not self.locked:
            self.percent = max(0.0, min(100.0, value)) if math.isfinite(value) else 0.0
    def update(self, now):
        active = not self.locked and now % self.window_s < self.percent * self.window_s / 100.0
        self.output.on() if active else self.output.off()
    def off(self):
        self.percent = 0.0
        self.output.off()
    def emergency_off(self):
        self.locked = True
        self.off()
    def unlock(self): self.locked = False
    def close(self):
        self.emergency_off()
        self.output.close()

class MotorState(Enum):
    IDLE = "idle"
    OPENING = "opening"
    CLOSING = "closing"
    FAULT = "fault"

class DoorMotor:
    def __init__(self, in1, in2, enable, switches, timeout_s):
        self.in1, self.in2, self.enable = in1, in2, enable
        self.switches, self.timeout_s = switches, timeout_s
        self.state, self.started_at, self.fault_reason = MotorState.IDLE, None, None
        self.goal = None
    def _drive(self, opening, now):
        self.stop()
        (self.in1 if opening else self.in2).on()
        if self.enable is not None: self.enable.on()
        self.state = MotorState.OPENING if opening else MotorState.CLOSING
        self.started_at = now
    def open(self, now): self._move(DoorState.OPEN, now)
    def close_door(self, now): self._move(DoorState.CLOSED, now)
    def _move(self, goal, now):
        if self.state == MotorState.FAULT: return
        state = self.switches.state()
        if state == DoorState.ERROR:
            self.goal = goal
            self._fail("switch_error")
        elif state != goal:
            self._drive(goal == DoorState.OPEN, now)
            self.goal = goal
        else:
            self.goal = None
    def stop(self):
        self.in1.off(); self.in2.off()
        if self.enable is not None: self.enable.off()
        if self.state in (MotorState.OPENING, MotorState.CLOSING): self.state = MotorState.IDLE
    def _fail(self, reason):
        self.stop()
        self.state, self.fault_reason = MotorState.FAULT, reason
    def update(self, now):
        if self.state not in (MotorState.OPENING, MotorState.CLOSING): return
        goal = DoorState.OPEN if self.state == MotorState.OPENING else DoorState.CLOSED
        state = self.switches.state()
        if state == DoorState.ERROR: self._fail("switch_error")
        elif state == goal:
            self.goal = None
            self.stop()
        elif now - self.started_at >= self.timeout_s: self._fail("timeout")
    def clear_fault(self):
        self.stop()
        self.state, self.fault_reason = MotorState.IDLE, None
        self.goal = None
    def close(self):
        self.stop()
        self.in1.close(); self.in2.close()
        if self.enable is not None: self.enable.close()

class SignalTower:
    def __init__(self, outputs): self.outputs = outputs
    def show(self, state):
        color = "red" if state in ("fault", "e_stop") else "yellow" if state in ("door_moving", "heating", "holding", "cooling") else "green"
        for name, output in self.outputs.items():
            output.on() if name == color or (name == "buzzer" and state in ("fault", "e_stop")) else output.off()
    def close(self):
        for output in self.outputs.values(): output.off(); output.close()
