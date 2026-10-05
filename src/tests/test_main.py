"""Integration tests: the whole kiln, built by main.py, in simulation."""

import random
import threading
import time

import main
from control.controller import State
from main import Kiln

TICK = 1 / 15


def run_for(kiln, seconds, start=0.0):
    """Advance simulated time. Returns the time reached."""
    now = start
    for _ in range(round(seconds / TICK)):
        kiln.tick(now)
        now += TICK
    return now


def new_kiln(seed=1):
    random.seed(seed)
    k = Kiln()
    k.devices.probe.noise = 0.05          # a guess for a PT100 board; the real value comes from the Pi
    return k


# 1: the kiln builds, warms up, and sits in IDLE without faults
k = new_kiln()
run_for(k, 5)
assert k.controller.state == State.IDLE
assert k.controller.fault_reason is None

# 2: the sensor needs time at the start: a failed very first reading is not a fault
k = new_kiln()
k.devices.probe.fail_chance = 1.0
k.tick(0.0)
k.devices.probe.fail_chance = 0.0
run_for(k, 5, start=TICK)
assert k.controller.state == State.IDLE

# 3: a dead sensor -> FAULT once the warm-up is over
k = new_kiln()
k.devices.probe.fail_chance = 1.0
run_for(k, 5)
assert k.controller.state == State.FAULT
assert k.controller.fault_reason == "sensor_timeout"

# 4: Start -> heats up -> HOLDING at the setpoint (with the simulated sensor noise)
k = new_kiln()
k.controller.set_setpoint(40.0)
t = run_for(k, 3)
k.devices.buttons["start"].press()
t = run_for(k, 900, start=t)
assert k.controller.state == State.HOLDING
assert abs(k.devices.kiln.temperature - 40.0) < 0.2

# 5: Stop button: the heater is cut at once, before the controller's next loop
k = new_kiln()
k.controller.set_setpoint(40.0)
t = run_for(k, 3)
k.devices.buttons["start"].press()
t = run_for(k, 60, start=t)
assert k.controller.state == State.HEATING
assert k.heater.percent > 0
k.devices.buttons["stop"].press()
assert k.devices.heater_pin.is_on is False
assert k.heater.locked is True
t = run_for(k, 2, start=t)
assert k.controller.state == State.E_STOP

# 6: the mushroom E-stop does the same
k = new_kiln()
k.controller.set_setpoint(40.0)
t = run_for(k, 3)
k.devices.buttons["start"].press()
t = run_for(k, 60, start=t)
k.devices.estop_switch.release()              # the NC contact opens
assert k.devices.heater_pin.is_on is False
assert k.heater.locked is True                # cut by the callback, not by the next loop
t = run_for(k, 2, start=t)
assert k.controller.state == State.E_STOP

# 7: after the E-stop: still stopped when released, reset() brings it back, and it heats again
k.devices.estop_switch.is_pressed = True      # released again
t = run_for(k, 3, start=t)
assert k.controller.state == State.E_STOP
k.controller.reset()
t = run_for(k, 2, start=t)
assert k.controller.state == State.IDLE
assert k.heater.locked is False
k.devices.buttons["start"].press()
t = run_for(k, 30, start=t)
assert k.controller.state == State.HEATING
assert k.heater.percent > 0

# 8: a whole run: heat, hold, finish, cool, the door opens, IDLE
k = new_kiln()
k.controller.set_setpoint(40.0)
t = run_for(k, 3)
k.devices.buttons["start"].press()
t = run_for(k, 900, start=t)
assert k.controller.state == State.HOLDING
k.controller.finish()
t = run_for(k, 600, start=t)
assert k.controller.state == State.IDLE
assert k.devices.kiln.temperature < 30.0
assert k.switches.state().value == "open"
assert k.heater.percent == 0

# 9: run_forever() cuts the heater when it ends
main.WARMUP_S = 0.0
k = new_kiln()
thread = threading.Thread(target=k.run_forever)
thread.start()
time.sleep(0.4)
k.stop()
thread.join(timeout=3)
assert not thread.is_alive()
assert k.heater.locked is True
assert k.devices.heater_pin.is_on is False

# 10: a crash inside the loop still cuts the heater
main.WARMUP_S = 0.0
k = new_kiln()
k.controller.set_setpoint(40.0)
k.devices.buttons["start"].press()


def boom(now):
    raise RuntimeError("simulated crash")


k.controller.step = boom
try:
    k.run_forever()
except RuntimeError:
    pass
assert k.heater.locked is True
assert k.devices.heater_pin.is_on is False

print("Kiln integration checks passed ✅")