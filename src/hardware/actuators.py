"""Actuators: things the controller commands (heater, door motor, signal tower)."""

import config
from enum import Enum
import math
from hardware.sensors import DoorState


class Heater:
    """Time-proportional heater: switches a pin on and off within a window."""

    def __init__(self, pin, window=config.HEATER_WINDOW_S):
        self.pin = pin
        self.window = window
        self.percent = 0.0
        self.locked = False

    def set_percent(self, percent):
        """Set the requested power, 0-100. Ignored while the heater is locked."""
        if self.locked:
            return
        if math.isnan(percent):
            percent = 0
        self.percent = max(0, min(100, percent))

    def update(self, now):
        """Call often. Switches the pin according to where we are in the window."""
        if self.locked:
            self.pin.off()
            return
        position = now % self.window
        on_time = self.percent / 100 * self.window
        if position < on_time:
            self.pin.on()
        else:
            self.pin.off()

    def off(self):
        """Emergency stop for the heater: pin off right now, power request to 0."""
        self.percent = 0
        self.pin.off()

    def emergency_off(self):
        """Cut the heater and keep it off until unlock() is called."""
        self.locked = True
        self.percent = 0
        self.pin.off()

    def unlock(self):
        """Allow the heater to be switched on again (after a reset)."""
        self.locked = False

class MotorState(Enum):
    IDLE = "idle"
    OPENING = "opening"
    CLOSING = "closing"
    FAULT = "fault"


class DoorMotor:
    """Drives the door until a limit switch is hit, with a timeout."""

    def __init__(self, in1, in2, sleep, switches, timeout=config.DOOR_TIMEOUT_S):
        self.in1 = in1
        self.in2 = in2
        self.sleep = sleep
        self.switches = switches          # a DoorSwitches object
        self.timeout = timeout
        self.state = MotorState.IDLE
        self.started_at = None
        self.fault_reason = None

    def _drive(self, opening):
        self.in1.off()                    # both off first: never both on at once
        self.in2.off()
        if opening:
            self.in1.on()
        else:
            self.in2.on()
        self.sleep.on()

    def stop(self):
        self.in1.off()
        self.in2.off()
        self.sleep.off()
        if self.state in (MotorState.OPENING, MotorState.CLOSING):
            self.state = MotorState.IDLE

    def _fail(self, reason):
        self.stop()
        self.state = MotorState.FAULT
        self.fault_reason = reason

    def clear_fault(self):
        self.stop()
        self.state = MotorState.IDLE
        self.fault_reason = None

    def open(self, now):
        if self.state == MotorState.FAULT:
                    return                        # a faulted motor refuses to move
        door = self.switches.state()
        if door == DoorState.ERROR:
            self._fail("switch_error")
            return
        if door == DoorState.OPEN:
            return                        # already open, nothing to do
        self._drive(opening=True)
        self.state = MotorState.OPENING
        self.started_at = now

    def close(self, now):
        if self.state == MotorState.FAULT:
            return                        # a faulted motor refuses to move
        door = self.switches.state()
        if door == DoorState.ERROR:
            self._fail("switch_error")
            return        
        if door == DoorState.CLOSED:
            return
        self._drive(opening=False)
        self.state = MotorState.CLOSING
        self.started_at = now

    def update(self, now):
        if self.state not in (MotorState.OPENING, MotorState.CLOSING):
            return

        door = self.switches.state()

        if door == DoorState.ERROR:
            self._fail("switch_error")
            return
        if self.state == MotorState.OPENING and door == DoorState.OPEN:
            self.stop()
            return
        if self.state == MotorState.CLOSING and door == DoorState.CLOSED:
            self.stop()
            return

        if now - self.started_at >= self.timeout:
            self._fail("timeout")
class NullTower:
    """Placeholder until the real signal tower class exists."""

    def show(self, state):
        pass

if __name__ == "__main__":
    from hardware.simulated import SimulatedPin

    def count_on(heater, pin):
        """Run 100 steps of 0.01 s and count how many steps the pin was on."""
        count = 0
        for i in range(100):
            heater.update(i * 0.01)
            if pin.is_on:
                count += 1
        return count

    pin = SimulatedPin()
    heater = Heater(pin)

    heater.set_percent(30)
    assert abs(count_on(heater, pin) - 30) <= 1

    heater.set_percent(0)
    assert count_on(heater, pin) == 0

    heater.set_percent(100)
    assert count_on(heater, pin) == 100

    heater.set_percent(float("nan"))
    heater.update(0.0)
    assert pin.is_on is False

    heater.set_percent(150)
    assert heater.percent == 100
    heater.set_percent(-5)
    assert heater.percent == 0

    heater.set_percent(100)
    heater.update(0.0)
    assert pin.is_on is True
    heater.off()
    assert pin.is_on is False

    print("Heater checks passed ✅")


    from types import SimpleNamespace
    from hardware.simulated import SimulatedSwitch, SimulatedDoor
    from hardware.sensors import DoorSwitches

    def make_rig(stuck=False):
        """A fresh motor + fake door + switches, door starting closed."""
        r = SimpleNamespace()
        r.in1, r.in2, r.sleep = SimulatedPin(), SimulatedPin(), SimulatedPin()
        r.open_sw, r.closed_sw = SimulatedSwitch(), SimulatedSwitch()
        r.switches = DoorSwitches(r.open_sw, r.closed_sw)
        r.sim = SimulatedDoor(r.in1, r.in2, r.open_sw, r.closed_sw, travel_time=5.0)
        r.sim.stuck = stuck
        r.motor = DoorMotor(r.in1, r.in2, r.sleep, r.switches)
        r.now = 0.0
        return r

    def run(r, seconds, dt=0.1):
        """Advance simulated time. Also checks that both pins are never on together."""
        for _ in range(round(seconds / dt)):
            r.now += dt
            r.sim.update(dt)
            r.motor.update(r.now)
            assert not (r.in1.is_on and r.in2.is_on)

        # 1-3: open the door
    r = make_rig()
    r.motor.open(r.now)
    assert r.motor.state == MotorState.OPENING
    assert r.in1.is_on and not r.in2.is_on
    run(r, 3)
    assert r.motor.state == MotorState.OPENING
    run(r, 3)
    assert r.switches.state() == DoorState.OPEN
    assert r.motor.state == MotorState.IDLE
    assert not r.in1.is_on and not r.in2.is_on

    # 4: open again when already open
    r.motor.open(r.now)
    assert r.motor.state == MotorState.IDLE
    assert not r.in1.is_on

    # 5: close the door
    r.motor.close(r.now)
    assert r.in2.is_on and not r.in1.is_on
    run(r, 6)
    assert r.switches.state() == DoorState.CLOSED
    assert r.motor.state == MotorState.IDLE

    # 5b: close again when already closed (must not start the motor)
    r.motor.close(r.now)
    assert r.motor.state == MotorState.IDLE
    assert not r.in1.is_on and not r.in2.is_on

    # 6-7: stuck door -> timeout
    r = make_rig(stuck=True)
    r.motor.open(r.now)
    run(r, 9)
    assert r.motor.state == MotorState.OPENING
    run(r, 2)
    assert r.motor.state == MotorState.FAULT
    assert r.motor.fault_reason == "timeout"
    assert not r.in1.is_on and not r.in2.is_on and not r.sleep.is_on

    # 8-9: a faulted motor refuses to move until the fault is cleared
    r.motor.close(r.now)
    assert r.motor.state == MotorState.FAULT
    r.motor.clear_fault()
    r.sim.stuck = False
    r.motor.open(r.now)
    assert r.motor.state == MotorState.OPENING

    # 10: both switches pressed before starting
    r = make_rig()
    r.open_sw.is_pressed = True
    r.closed_sw.is_pressed = True
    r.motor.open(r.now)
    assert r.motor.state == MotorState.FAULT
    assert r.motor.fault_reason == "switch_error"
    assert not r.in1.is_on

    # 11: stop halfway
    r = make_rig()
    r.motor.open(r.now)
    run(r, 1)
    r.motor.stop()
    assert r.motor.state == MotorState.IDLE
    assert not r.in1.is_on

    print("Door motor checks passed ✅")
        # --- emergency_off / unlock ---
    pin2 = SimulatedPin()
    h2 = Heater(pin2)
    h2.set_percent(100)
    h2.update(0.0)
    assert pin2.is_on is True
    h2.emergency_off()
    assert pin2.is_on is False and h2.percent == 0
    h2.set_percent(100)                      # ignored while locked
    assert h2.percent == 0
    h2.percent = 80                          # even if a value sneaks in...
    h2.update(0.0)
    assert pin2.is_on is False               # ...the pin stays off
    h2.unlock()
    h2.set_percent(100)
    h2.update(0.0)
    assert pin2.is_on is True
    print("Heater lockout checks passed ✅")