"""Heater, door motor and signal tower output drivers."""
from enum import Enum
import math
import threading
import time
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

ALARM_STATES = ("fault", "e_stop")
RUN_STATES = ("door_moving", "heating", "holding", "cooling")
FAULT_BLINK_HZ = 1.0
DOOR_MOVING_BLINK_HZ = 4.0
RUN_BLINK_HZ = 1.0
COMPLETE_BLINK_HZ = 2.0
# Three short beeps when a run completes: (start, end) offsets in seconds.
COMPLETE_CHIRPS = ((0.0, 0.15), (0.3, 0.45), (0.6, 0.75))

def blink_on(now, hz):
    """True during the first half of each blink period."""
    return now * hz % 1.0 < 0.5

def tower_lights(state, now, door_moving=False, hot=False):
    """The lights the tower shows; the buzzer is decided separately.

    Red: steady for E-Stop, blinking for a fault. Orange: the oven is at or above
    the door-safe temperature. Yellow: blinks at 4 Hz while the door is between its
    limit switches or driven, and then only an alarm's red may show with it. Green:
    steady when ready, blinking during a run and faster once a run completes.
    """
    lit = set()
    if state == "e_stop" or (state == "fault" and blink_on(now, FAULT_BLINK_HZ)): lit.add("red")
    if door_moving:
        if blink_on(now, DOOR_MOVING_BLINK_HZ): lit.add("yellow")
        return lit
    if hot: lit.add("orange")
    if state in RUN_STATES:
        if blink_on(now, RUN_BLINK_HZ): lit.add("green")
    elif state == "complete":
        if blink_on(now, COMPLETE_BLINK_HZ): lit.add("green")
    elif state not in ALARM_STATES:
        lit.add("green")
    return lit

def light_test_steps(lights, step_s):
    """(lit lights, duration_s) steps that exercise every light; lights are top to bottom."""
    lights, fast_s = tuple(lights), step_s / 3.0
    steps = [({light}, step_s) for light in lights]  # Top to bottom, one at a time
    steps.append((set(), step_s / 2.0))
    # A ball bouncing from the bottom to the top and back, twice.
    bounce = list(reversed(lights)) + list(lights[1:-1])
    steps += [({light}, fast_s) for light in bounce * 2 + list(reversed(lights))]
    # Fill up from the bottom, then flash the full tower.
    steps += [(set(lights[index:]), step_s) for index in range(len(lights) - 1, -1, -1)]
    steps += [(lit, fast_s) for _ in range(3) for lit in (set(), set(lights))]
    # Alternate every other light, like a level crossing.
    steps += [(set(lights[start::2]), step_s / 2.0) for _ in range(4) for start in (0, 1)]
    steps.append((set(), step_s))
    return steps

class SignalTower:
    def __init__(self, outputs):
        self.outputs = outputs
        self.state = "ready"
        self.door_moving, self.hot = False, False
        self.buzzer_test_until = None
        self.light_test = []  # Remaining (lit lights, end time) steps of a light test
        self.chirp_started, self._chirp_pending = None, False
        self.now = 0.0
        self._shown = None
        self._lock = threading.RLock()
        self._refresh_stop, self._refresh_thread = None, None
    def show(self, state):
        with self._lock:
            if state == "complete" and self.state != "complete": self._chirp_pending = True
            elif state != "complete": self.chirp_started, self._chirp_pending = None, False
            self.state = state
            if state in ALARM_STATES: self.light_test = []
            self._refresh()
    def set_conditions(self, door_moving, hot):
        """Inputs the lights show besides the process state; update() applies them."""
        with self._lock:
            self.door_moving, self.hot = bool(door_moving), bool(hot)
    def test_buzzer(self, now, duration_s):
        """Sound the buzzer briefly; update() ends the test without blocking."""
        with self._lock:
            self.buzzer_test_until = now + duration_s
            self.update(now)
    def test_lights(self, now, steps):
        """Play (lit lights, duration_s) steps in place of the state lights; update() advances them."""
        with self._lock:
            if self.state in ALARM_STATES:
                raise RuntimeError("The light tower is showing an alarm")
            self.light_test, end = [], now
            for lit, duration_s in steps:
                end += duration_s
                self.light_test.append((frozenset(lit), end))
            self.update(now)
    def update(self, now):
        with self._lock:
            # The worker and the refresh thread both update; never let a stale tick
            # time step the blinks back and flash a light that just turned off.
            now = self.now = max(now, self.now)
            if self.buzzer_test_until is not None and now >= self.buzzer_test_until:
                self.buzzer_test_until = None
            while self.light_test and now >= self.light_test[0][1]:
                self.light_test.pop(0)
            if self._chirp_pending: self.chirp_started, self._chirp_pending = now, False
            self._refresh()
    def _refresh(self):
        now = self.now
        if self.light_test:
            lit = set(self.light_test[0][0])
        else:
            lit = tower_lights(self.state, now, self.door_moving, self.hot)
        chirping = self.chirp_started is not None and any(
            start <= now - self.chirp_started < end for start, end in COMPLETE_CHIRPS)
        buzzer_on = self.state == "fault" or (
            self.state != "e_stop"
            and (self.buzzer_test_until is not None or chirping)
        )
        if buzzer_on: lit.add("buzzer")
        # Blinks refresh every few milliseconds; write the pins only when the pattern changes.
        if lit == self._shown: return
        self._shown = lit
        for name, output in self.outputs.items():
            output.on() if name in lit else output.off()
    def start_refresh(self, interval_s=0.01):
        """Keep blink timing even between control-loop ticks with a background refresh."""
        if self._refresh_thread is not None: return
        self._refresh_stop = threading.Event()
        def run():
            while not self._refresh_stop.wait(interval_s):
                self.update(time.monotonic())
        self._refresh_thread = threading.Thread(target=run, name="signal-tower", daemon=True)
        self._refresh_thread.start()
    def stop_refresh(self):
        if self._refresh_thread is None: return
        self._refresh_stop.set()
        self._refresh_thread.join(timeout=1.0)
        self._refresh_thread = None
    def close(self):
        self.stop_refresh()
        for output in self.outputs.values(): output.off(); output.close()
