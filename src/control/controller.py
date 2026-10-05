"""The state machine: decides what the kiln does, once per loop."""

from enum import Enum
import config

from hardware.actuators import MotorState
from hardware.sensors import DoorState


class State(Enum):
    IDLE = "idle"
    DOOR_MOVING = "door_moving"
    HEATING = "heating"
    HOLDING = "holding"
    COOLING = "cooling"
    FAULT = "fault"
    E_STOP = "e_stop"


class Controller:
    """Runs the kiln. Call step(now) once per loop."""

    def __init__(self, sensor, switches, motor, heater, safety, tower, buttons, pid, estop):
        self.sensor = sensor
        self.switches = switches
        self.motor = motor
        self.heater = heater
        self.safety = safety
        self.tower = tower
        self.buttons = buttons
        self.pid = pid
        self.estop = estop
        self.state = State.IDLE
        self.fault_reason = None
        self.setpoint = None
        self._door_goal = None
        self._after_door = None
        self._pid_time = None
        self._stable_since = None
        self._finish_requested = False
        self._reset_requested = False
        self._cut_requested = False

    def emergency_cut(self):
        """Cut the heater and stop the motor right now. Meant to be called from the button callbacks."""
        self.heater.emergency_off()
        self.motor.stop()
        self._cut_requested = True

    def reset(self):
        """Ask the controller to leave FAULT or E_STOP. Only works when the cause is gone."""
        self._reset_requested = True

    def finish(self):
        """Ask the controller to end the run and cool down. Safe to call from another thread."""
        self._finish_requested = True

    def set_setpoint(self, value):
        """Set the target temperature. Raises ValueError if it is not allowed."""
        self.safety.validate_setpoint(value)
        self.setpoint = value

    def _enter_fault(self, reason):
        self.heater.off()
        self.motor.stop()
        self.state = State.FAULT
        self.fault_reason = reason

    def _enter_e_stop(self):
        self.heater.off()
        self.motor.stop()
        self.state = State.E_STOP

    def _try_reset(self, temperature, since_valid, door):
        fault = self.safety.check(temperature, since_valid, door, None, False)
        if fault is not None:
            return
        self.motor.clear_fault()
        self.heater.unlock()
        self.heater.off()
        self.fault_reason = None
        self._enter_state(State.IDLE)

    def _enter_state(self, new_state):
        if new_state == State.HEATING:
            self.pid.reset()
            self._pid_time = None
            self._stable_since = None
        self.state = new_state

    def _run_pid(self, now, temperature):
        if self._pid_time is None:
            dt = config.LOOP_PERIOD_S
        else:
            dt = now - self._pid_time
        if dt <= 0:
            return
        self._pid_time = now

        feedforward = max(0.0, config.FF_PERCENT_PER_DEGREE
                          * (self.setpoint - config.ROOM_TEMPERATURE))
        power = self.pid.update(self.setpoint, temperature, dt, feedforward)
        self.heater.set_percent(power)

    def _step_heating(self, now, temperature):
        if temperature is None:
            self.heater.set_percent(0)
            self._stable_since = None
            return

        self._run_pid(now, temperature)

        if abs(temperature - self.setpoint) <= config.STABLE_TOLERANCE:
            if self._stable_since is None:
                self._stable_since = now
            elif now - self._stable_since >= config.STABLE_TIME_S:
                self._enter_state(State.HOLDING)
        else:
            self._stable_since = None

    def _step_holding(self, now, temperature):
        if temperature is None:
            self.heater.set_percent(0)
            return

        self._run_pid(now, temperature)

    def _step_cooling(self, now, temperature):
        self.heater.off()
        if self.safety.door_may_open(temperature):
            self._begin_door_move(DoorState.OPEN, State.IDLE, now)

    def _start(self, now, temperature, door):
        if self.setpoint is None or temperature is None:
            return
        if door == DoorState.CLOSED:
            self._enter_state(State.HEATING)
        else:
            self._begin_door_move(DoorState.CLOSED, State.HEATING, now)

    def _begin_door_move(self, goal, next_state, now):
        if goal == DoorState.CLOSED:
            self.motor.close(now)
        else:
            self.motor.open(now)
        self._door_goal = goal
        self._after_door = next_state
        self.state = State.DOOR_MOVING

    def _step_door_moving(self, door):
        if self.motor.state != MotorState.IDLE:
            return
        if door != self._door_goal:
            self._enter_fault("door_not_in_position")
            return
        self._enter_state(self._after_door)

    def _door_must_be_closed(self, temperature):
        if self.state in (State.HEATING, State.HOLDING):
            return True
        if self.state == State.COOLING:
            return not self.safety.door_may_open(temperature)
        return False

    def step(self, now):
        start_pressed = self.buttons.pressed("start")
        stop_pressed = self.buttons.pressed("stop")
        finish_requested = self._finish_requested
        self._finish_requested = False
        reset_requested = self._reset_requested
        self._reset_requested = False
        cut_requested = self._cut_requested
        self._cut_requested = False

        temperature = self.sensor.temperature()
        since_valid = self.sensor.seconds_since_valid()
        door = self.switches.state()
        estop_active = self.estop.is_active()

        if self.state in (State.FAULT, State.E_STOP):
            self.heater.off()
            if reset_requested and not estop_active:
                self._try_reset(temperature, since_valid, door)
            return

        if estop_active or cut_requested:
            self._enter_e_stop()
            return

        fault = self.safety.check(temperature, since_valid, door,
                                  self.motor.fault_reason,
                                  self._door_must_be_closed(temperature))
        if fault is not None:
            self._enter_fault(fault)
            return

        if stop_pressed:
            self._enter_e_stop()
            return

        if finish_requested and self.state in (State.HEATING, State.HOLDING):
            self._enter_state(State.COOLING)

        if self.state == State.IDLE and start_pressed:
            self._start(now, temperature, door)
        elif self.state == State.DOOR_MOVING:
            self._step_door_moving(door)
        elif self.state == State.HEATING:
            self._step_heating(now, temperature)
        elif self.state == State.HOLDING:
            self._step_holding(now, temperature)
        elif self.state == State.COOLING:
            self._step_cooling(now, temperature)

if __name__ == "__main__":
    from types import SimpleNamespace
    from hardware.simulated import SimulatedPin, SimulatedSwitch, SimulatedButton, SimulatedDoor, SimulatedKiln
    from control.pid import PID
    from hardware.sensors import DoorSwitches, Buttons, EmergencyStop
    from hardware.actuators import DoorMotor, Heater, NullTower
    from control.safety import SafetyMonitor

    class FakeSensor:
        """Stands in for TemperatureSensor: tests set temp and age by hand."""
        def __init__(self):
            self.temp = 25.0
            self.age = 0.1
        def temperature(self):
            return self.temp
        def seconds_since_valid(self):
            return self.age

    def make_controller():
        r = SimpleNamespace()
        r.sensor = FakeSensor()
        r.open_sw, r.closed_sw = SimulatedSwitch(), SimulatedSwitch()
        r.closed_sw.is_pressed = True                  # door starts closed
        r.switches = DoorSwitches(r.open_sw, r.closed_sw)
        r.in1, r.in2, r.sleep = SimulatedPin(), SimulatedPin(), SimulatedPin()
        r.motor = DoorMotor(r.in1, r.in2, r.sleep, r.switches)
        r.sim = SimulatedDoor(r.in1, r.in2, r.open_sw, r.closed_sw, travel_time=5.0)
        r.now = 0.0
        r.heater_pin = SimulatedPin()
        r.heater = Heater(r.heater_pin)
        r.start_btn, r.stop_btn = SimulatedButton(), SimulatedButton()
        r.buttons = Buttons({"start": r.start_btn, "stop": r.stop_btn})
        r.kiln = SimulatedKiln()
        r.pid = PID(config.PID_KP, config.PID_KI, config.PID_KD)
        r.estop_sw = SimulatedButton()
        r.estop_sw.is_pressed = True                   # NC contact closed = healthy
        r.estop = EmergencyStop(r.estop_sw)
        r.controller = Controller(r.sensor, r.switches, r.motor, r.heater,
                                  SafetyMonitor(), NullTower(), r.buttons, r.pid, r.estop)
        return r

    # ---------- safety checks (unchanged) ----------
    # 1: healthy
    r = make_controller()
    r.controller.step(0.0)
    assert r.controller.state == State.IDLE
    assert r.controller.fault_reason is None

    # 2: over temperature
    r = make_controller()
    r.sensor.temp = 50.0
    r.controller.step(0.0)
    assert r.controller.state == State.FAULT
    assert r.controller.fault_reason == "over_temperature"

    # 3: the heater is cut immediately
    r = make_controller()
    r.heater.set_percent(100)
    r.heater.update(0.0)
    assert r.heater_pin.is_on is True
    r.sensor.temp = 50.0
    r.controller.step(0.0)
    assert r.heater_pin.is_on is False
    assert r.heater.percent == 0

    # 4: sensor timeout
    r = make_controller()
    r.sensor.age = 2.0
    r.controller.step(0.0)
    assert r.controller.fault_reason == "sensor_timeout"

    # 5: unknown temperature with a fresh timestamp is not a fault (startup)
    r = make_controller()
    r.sensor.temp = None
    r.controller.step(0.0)
    assert r.controller.state == State.IDLE

    # 6: door error from the switches
    r = make_controller()
    r.open_sw.is_pressed = True          # closed_sw is already pressed -> both pressed
    r.controller.step(0.0)
    assert r.controller.fault_reason == "door_error"

    # 7: door motor fault (switches look normal afterwards)
    r = make_controller()
    r.open_sw.is_pressed = True
    r.motor.open(0.0)                    # both pressed -> the motor faults
    assert r.motor.state == MotorState.FAULT
    r.open_sw.is_pressed = False
    r.closed_sw.is_pressed = False
    r.controller.step(0.0)
    assert r.controller.fault_reason == "door_motor_switch_error"

    # 8: FAULT is sticky, and keeps the heater off
    r = make_controller()
    r.sensor.temp = 50.0
    r.controller.step(0.0)
    r.sensor.temp = 25.0                 # the problem "goes away"
    r.heater_pin.on()                    # something turns the heater on behind our back
    r.controller.step(1.0)
    assert r.controller.state == State.FAULT
    assert r.heater_pin.is_on is False

    print("Controller safety checks passed ✅")

    # ---------- setpoint ----------
    # 9: a good setpoint is stored
    r = make_controller()
    r.controller.set_setpoint(40.0)
    assert r.controller.setpoint == 40.0

    # 10: bad setpoints are rejected and nothing is stored
    r = make_controller()
    for bad in [19.9, 45.1, float("nan")]:
        try:
            r.controller.set_setpoint(bad)
            assert False, f"{bad} should have been rejected"
        except ValueError:
            pass
    assert r.controller.setpoint is None

    # ---------- Start ----------
    # 11: Start with a setpoint and a closed door -> HEATING
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.HEATING

    # 12: Start without a setpoint does nothing
    r = make_controller()
    r.start_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.IDLE

    # 13: Start while the temperature is unknown does nothing
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.sensor.temp = None
    r.start_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.IDLE

    # 14: Start with the door open -> the door starts closing
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.closed_sw.is_pressed = False
    r.open_sw.is_pressed = True
    r.start_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.DOOR_MOVING
    assert r.motor.state == MotorState.CLOSING
    assert r.in2.is_on and not r.in1.is_on

    # 15: Start with the door halfway -> also closes
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.closed_sw.is_pressed = False       # neither switch pressed
    r.start_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.DOOR_MOVING
    assert r.motor.state == MotorState.CLOSING

    # ---------- Stop ----------
    # 16: Stop in IDLE -> E_STOP
    r = make_controller()
    r.stop_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.E_STOP

    # 17: Stop while heating cuts the heater at once
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.HEATING
    r.heater.set_percent(100)
    r.heater.update(0.0)
    assert r.heater_pin.is_on is True
    r.stop_btn.press()
    r.controller.step(1.0)
    assert r.controller.state == State.E_STOP
    assert r.heater_pin.is_on is False
    assert r.heater.percent == 0

    # 18: Stop while the door is moving: the motor stops, nothing moves by itself
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.closed_sw.is_pressed = False
    r.open_sw.is_pressed = True
    r.start_btn.press()
    r.controller.step(0.0)
    assert r.motor.state == MotorState.CLOSING
    r.stop_btn.press()
    r.controller.step(1.0)
    assert r.controller.state == State.E_STOP
    assert not r.in1.is_on and not r.in2.is_on and not r.sleep.is_on
    assert r.motor.state == MotorState.IDLE

    # 19: a fault beats Stop when both happen in the same loop
    r = make_controller()
    r.sensor.temp = 50.0
    r.stop_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.FAULT

    # 20: button presses made while stopped are used up, not left waiting
    r = make_controller()
    r.stop_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.E_STOP
    r.start_btn.press()
    r.controller.step(1.0)
    assert r.controller.state == State.E_STOP
    assert r.buttons.pressed("start") is False

    print("Controller start/stop checks passed ✅")

        # ---------- DOOR_MOVING ----------
    def set_door(r, position):
        """Put the fake door at a position: 0 = closed, 1 = open."""
        r.sim.position = position
        r.sim.update(0.0)

    def run_hw(r, seconds, dt=0.1):
        """What main.py will do: the fake door and the motor are updated often."""
        for _ in range(round(seconds / dt)):
            r.now += dt
            r.sim.update(dt)
            r.motor.update(r.now)

    # 21: open door -> closes -> HEATING
    r = make_controller()
    set_door(r, 1.0)
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    assert r.controller.state == State.DOOR_MOVING
    run_hw(r, 6)
    r.controller.step(r.now)
    assert r.controller.state == State.HEATING
    assert r.switches.state() == DoorState.CLOSED

    # 22: while the door is still moving, the controller keeps waiting
    r = make_controller()
    set_door(r, 1.0)
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    run_hw(r, 2)
    r.controller.step(r.now)
    assert r.controller.state == State.DOOR_MOVING
    assert r.motor.state == MotorState.CLOSING

    # 23: door starting halfway also ends in HEATING
    r = make_controller()
    set_door(r, 0.5)
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    assert r.controller.state == State.DOOR_MOVING
    run_hw(r, 3)
    r.controller.step(r.now)
    assert r.controller.state == State.HEATING

    # 24: a stuck door times out -> FAULT
    r = make_controller()
    set_door(r, 1.0)
    r.sim.stuck = True
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    run_hw(r, 11)
    r.controller.step(r.now)
    assert r.controller.state == State.FAULT
    assert r.controller.fault_reason == "door_motor_timeout"

    # 25: motor stopped by something else, door not closed -> never heat
    r = make_controller()
    set_door(r, 1.0)
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    run_hw(r, 1)
    r.motor.stop()
    r.controller.step(r.now)
    assert r.controller.state == State.FAULT
    assert r.controller.fault_reason == "door_not_in_position"
    assert r.heater_pin.is_on is False

    # 26: Stop while DOOR_MOVING still works and the door stays where it is
    r = make_controller()
    set_door(r, 1.0)
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    run_hw(r, 1)
    r.stop_btn.press()
    r.controller.step(r.now)
    assert r.controller.state == State.E_STOP
    position_at_stop = r.sim.position
    run_hw(r, 3)
    assert r.sim.position == position_at_stop

    print("Controller door checks passed ✅")
        # ---------- door must stay closed while heating / holding ----------
    # 27: door halfway open while heating -> FAULT, heater cut
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    assert r.controller.state == State.HEATING
    r.heater.set_percent(100)
    r.heater.update(0.0)
    assert r.heater_pin.is_on is True
    set_door(r, 0.5)
    r.controller.step(r.now)
    assert r.controller.state == State.FAULT
    assert r.controller.fault_reason == "door_opened"
    assert r.heater_pin.is_on is False

    # 28: door fully open while heating -> FAULT
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    set_door(r, 1.0)
    r.controller.step(r.now)
    assert r.controller.state == State.FAULT
    assert r.controller.fault_reason == "door_opened"

    # 29: the same rule while holding (HOLDING logic comes later, so set the state by hand)
    r = make_controller()
    r.controller.state = State.HOLDING
    set_door(r, 0.5)
    r.controller.step(r.now)
    assert r.controller.state == State.FAULT
    assert r.controller.fault_reason == "door_opened"

    # 30: a closed door while heating is fine
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    r.controller.step(r.now + 1.0)
    assert r.controller.state == State.HEATING

    # 31: an open door in IDLE is not a fault
    r = make_controller()
    set_door(r, 1.0)
    r.controller.step(r.now)
    assert r.controller.state == State.IDLE

    # 32: the door fault beats Stop in the same loop
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    set_door(r, 0.5)
    r.stop_btn.press()
    r.controller.step(r.now)
    assert r.controller.state == State.FAULT

    print("Controller door-closed checks passed ✅")

        # ---------- HEATING: the PID drives the heater ----------
    # 33: entering HEATING resets the PID
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.pid.integral = 99.0
    r.pid.last_temp = 12.3
    r.start_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.HEATING
    assert r.pid.integral == 0.0
    assert r.pid.last_temp is None

    # 34: far below the setpoint -> full power
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.sensor.temp = 20.0
    r.start_btn.press()
    r.controller.step(0.0)
    r.controller.step(1.0)
    assert r.heater.percent == 100

    # 35: exactly at the setpoint -> only the feedforward (1 % per degree above 20 = 20 %)
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.sensor.temp = 40.0
    r.start_btn.press()
    r.controller.step(0.0)
    r.controller.step(1.0)
    assert abs(r.heater.percent - 20.0) < 1e-9

    # 36: above the setpoint -> heater off, never negative
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.sensor.temp = 44.0
    r.start_btn.press()
    r.controller.step(0.0)
    r.controller.step(1.0)
    assert r.heater.percent == 0

    # 37: unknown temperature while heating -> heater off
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    r.heater.set_percent(80)
    r.sensor.temp = None
    r.controller.step(1.0)
    assert r.heater.percent == 0
    assert r.controller.state == State.HEATING

    # 38: the same time twice, or time going backwards, must not crash the PID
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    r.controller.step(1.0)
    r.controller.step(1.0)
    r.controller.step(0.5)
    assert r.controller.state == State.HEATING

    # 39: closed loop with the simulated kiln, 20 C -> 40 C
    def run_loop(r, seconds, dt=0.1):
        """Heater and kiln every 0.1 s, controller once per second. Returns the peak."""
        peak = r.kiln.temperature
        for i in range(round(seconds / dt)):
            r.now += dt
            r.sim.update(dt)
            r.motor.update(r.now)
            r.heater.update(r.now)
            r.kiln.heater_percent = 100.0 if r.heater_pin.is_on else 0.0
            r.kiln.update(dt)
            r.sensor.temp = r.kiln.temperature
            peak = max(peak, r.kiln.temperature)
            if (i + 1) % 10 == 0:
                r.controller.step(r.now)
                assert 0 <= r.heater.percent <= 100
        return peak

    r = make_controller()
    r.sensor.temp = r.kiln.temperature            # the kiln starts at room temperature
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    assert r.controller.state == State.HEATING
    peak = run_loop(r, 90)
    assert r.kiln.temperature >= 39.5             # reached the target within 90 s
    peak = max(peak, run_loop(r, 210))
    assert peak < 42.0                            # overshoot stays small
    assert abs(r.kiln.temperature - 40.0) < 1.0   # and it settles near the target
    assert r.controller.state == State.HEATING    # no fault on the way

    print("Controller heating checks passed ✅")


        # ---------- HOLDING: stable for 30 s, then keep controlling ----------
    def reach_holding(r):
        """Drive a controller sitting exactly at its setpoint into HOLDING. Returns the time."""
        r.controller.set_setpoint(40.0)
        r.sensor.temp = 40.0
        r.start_btn.press()
        r.controller.step(0.0)
        r.controller.step(1.0)             # first sample inside the band
        r.controller.step(31.0)            # 30 s later
        return 31.0

    # 40: stable for 30 s -> HOLDING (29 s is not enough)
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.sensor.temp = 40.0
    r.start_btn.press()
    r.controller.step(0.0)
    r.controller.step(1.0)
    r.controller.step(30.0)
    assert r.controller.state == State.HEATING
    r.controller.step(31.0)
    assert r.controller.state == State.HOLDING

    # 41: leaving the band restarts the 30 s timer
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.sensor.temp = 40.0
    r.start_btn.press()
    r.controller.step(0.0)
    r.controller.step(1.0)
    r.sensor.temp = 40.5
    r.controller.step(20.0)
    r.sensor.temp = 40.0
    r.controller.step(21.0)
    r.controller.step(50.0)
    assert r.controller.state == State.HEATING
    r.controller.step(51.0)
    assert r.controller.state == State.HOLDING

    # 42: just inside the band counts, just outside does not
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    r.sensor.temp = 40.09
    r.controller.step(1.0)
    r.controller.step(31.0)
    assert r.controller.state == State.HOLDING
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    r.sensor.temp = 40.11
    r.controller.step(1.0)
    r.controller.step(31.0)
    assert r.controller.state == State.HEATING

    # 43: an unknown temperature restarts the timer too
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.sensor.temp = 40.0
    r.start_btn.press()
    r.controller.step(0.0)
    r.controller.step(1.0)
    r.sensor.temp = None
    r.controller.step(20.0)
    assert r.heater.percent == 0
    r.sensor.temp = 40.0
    r.controller.step(21.0)
    r.controller.step(50.0)
    assert r.controller.state == State.HEATING
    r.controller.step(51.0)
    assert r.controller.state == State.HOLDING

    # 44: in HOLDING the controller keeps controlling, even far off target (it stays HOLDING)
    r = make_controller()
    t = reach_holding(r)
    r.sensor.temp = 20.0
    r.controller.step(t + 1.0)
    assert r.controller.state == State.HOLDING
    assert r.heater.percent == 100
    r.sensor.temp = 45.0
    r.controller.step(t + 2.0)
    assert r.controller.state == State.HOLDING
    assert r.heater.percent == 0

    # 45: entering HOLDING does not reset the PID
    r = make_controller()
    reach_holding(r)
    assert r.controller.state == State.HOLDING
    assert r.pid.last_temp == 40.0

    # 46: Stop in HOLDING -> E_STOP, heater off
    r = make_controller()
    t = reach_holding(r)
    r.heater.set_percent(50)
    r.heater.update(0.0)
    r.stop_btn.press()
    r.controller.step(t + 1.0)
    assert r.controller.state == State.E_STOP
    assert r.heater.percent == 0

    # 47: the door opening in HOLDING -> FAULT (reached the normal way this time)
    r = make_controller()
    t = reach_holding(r)
    set_door(r, 0.5)
    r.controller.step(t + 1.0)
    assert r.controller.state == State.FAULT
    assert r.controller.fault_reason == "door_opened"

    # 48: closed loop: the simulated kiln ends up HOLDING at 40 C
    r = make_controller()
    r.sensor.temp = r.kiln.temperature
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    run_loop(r, 700)
    assert r.controller.state == State.HOLDING
    assert abs(r.kiln.temperature - 40.0) < 0.2

    print("Controller holding checks passed ✅")

        # ---------- COOLING: finish() ends the run ----------
    def reach_cooling(r, temp):
        """HOLDING at 40 C, then finish(), then the temperature is set to temp. Returns the time."""
        t = reach_holding(r)
        r.controller.finish()
        r.sensor.temp = temp
        r.controller.step(t + 1.0)
        return t + 1.0

    # 49: finish() in HOLDING -> COOLING, heater off at once
    r = make_controller()
    t = reach_holding(r)
    r.heater.set_percent(50)
    r.heater.update(0.0)
    r.controller.finish()
    r.controller.step(t + 1.0)
    assert r.controller.state == State.COOLING
    assert r.heater.percent == 0
    assert r.heater_pin.is_on is False

    # 50: finish() while still heating also cools down
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.HEATING
    r.sensor.temp = 35.0
    r.controller.finish()
    r.controller.step(1.0)
    assert r.controller.state == State.COOLING

    # 51: finish() in IDLE does nothing, and the request is used up (not left waiting)
    r = make_controller()
    r.controller.finish()
    r.controller.step(0.0)
    assert r.controller.state == State.IDLE
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(1.0)
    assert r.controller.state == State.HEATING
    r.controller.step(2.0)
    assert r.controller.state == State.HEATING

    # 52: warm -> the door stays closed and nothing moves
    r = make_controller()
    t = reach_cooling(r, 35.0)
    r.controller.step(t + 1.0)
    assert r.controller.state == State.COOLING
    assert r.motor.state == MotorState.IDLE
    assert not r.in1.is_on and not r.in2.is_on

    # 53: unknown temperature -> the door stays closed
    r = make_controller()
    t = reach_cooling(r, 35.0)
    r.sensor.temp = None
    r.controller.step(t + 1.0)
    assert r.controller.state == State.COOLING
    assert r.motor.state == MotorState.IDLE

    # 54: exactly 30.0 is not cool enough
    r = make_controller()
    t = reach_cooling(r, 30.0)
    assert r.controller.state == State.COOLING
    assert r.motor.state == MotorState.IDLE

    # 55: just below 30 -> the door starts opening
    r = make_controller()
    t = reach_cooling(r, 29.9)
    assert r.controller.state == State.DOOR_MOVING
    assert r.motor.state == MotorState.OPENING
    assert r.in1.is_on and not r.in2.is_on

    # 56: cool -> door opens -> IDLE, heater still off
    r = make_controller()
    t = reach_cooling(r, 29.0)
    r.now = t
    run_hw(r, 6)
    r.controller.step(r.now)
    assert r.controller.state == State.IDLE
    assert r.switches.state() == DoorState.OPEN
    assert r.heater.percent == 0

    # 57: a new run after cooling: the door closes again, then HEATING (setpoint is kept)
    r.start_btn.press()
    r.controller.step(r.now + 1.0)
    assert r.controller.state == State.DOOR_MOVING
    run_hw(r, 6)
    r.controller.step(r.now)
    assert r.controller.state == State.HEATING
    assert r.controller.setpoint == 40.0

    # 58: Stop in COOLING -> E_STOP
    r = make_controller()
    t = reach_cooling(r, 35.0)
    r.stop_btn.press()
    r.controller.step(t + 1.0)
    assert r.controller.state == State.E_STOP

    # 59: someone opens the door by hand while it is still warm -> FAULT
    r = make_controller()
    t = reach_cooling(r, 35.0)
    set_door(r, 0.5)
    r.controller.step(t + 1.0)
    assert r.controller.state == State.FAULT
    assert r.controller.fault_reason == "door_opened"

    # 60: finish() while already cooling changes nothing
    r = make_controller()
    t = reach_cooling(r, 35.0)
    r.controller.finish()
    r.controller.step(t + 1.0)
    assert r.controller.state == State.COOLING

    # 61: the whole cycle with the simulated kiln: heat, hold, finish, cool, door opens, IDLE
    r = make_controller()
    r.sensor.temp = r.kiln.temperature
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    run_loop(r, 700)
    assert r.controller.state == State.HOLDING
    r.controller.finish()
    run_loop(r, 400)
    assert r.controller.state == State.IDLE
    assert r.switches.state() == DoorState.OPEN
    assert r.kiln.temperature < 30.0
    assert r.heater.percent == 0

    # 62: finish() when the kiln is already cool: COOLING goes straight on to open the door
    r = make_controller()                 # the fake sensor reads 25 C
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.HEATING
    r.controller.finish()
    r.controller.step(1.0)
    assert r.controller.state == State.DOOR_MOVING
    assert r.motor.state == MotorState.OPENING

    print("Controller cooling checks passed ✅")

        # ---------- COOLING: a door opened by hand ----------
    # 63: opened by hand while cool enough -> no fault, the controller carries on opening
    r = make_controller()
    t = reach_cooling(r, 35.0)
    r.sensor.temp = 29.0
    set_door(r, 0.5)
    r.controller.step(t + 1.0)
    assert r.controller.state == State.DOOR_MOVING
    assert r.motor.state == MotorState.OPENING

    # 64: opened by hand while the temperature is unknown -> FAULT (unknown counts as warm)
    r = make_controller()
    t = reach_cooling(r, 35.0)
    r.sensor.temp = None
    set_door(r, 0.5)
    r.controller.step(t + 1.0)
    assert r.controller.state == State.FAULT
    assert r.controller.fault_reason == "door_opened"

    # 65: opened by hand at exactly 30.0 (not cool enough) -> FAULT
    r = make_controller()
    t = reach_cooling(r, 35.0)
    r.sensor.temp = 30.0
    set_door(r, 1.0)
    r.controller.step(t + 1.0)
    assert r.controller.state == State.FAULT

    # 66: the heater stays off after that fault
    assert r.heater.percent == 0
    assert r.heater_pin.is_on is False

    print("Controller cooling door checks passed ✅")

        # ---------- the mushroom E-stop ----------
    # 75: E-stop pressed (or its wire broken) in IDLE -> E_STOP
    r = make_controller()
    r.estop_sw.is_pressed = False                 # the NC contact opens
    r.controller.step(0.0)
    assert r.controller.state == State.E_STOP

    # 76: E-stop while heating cuts the heater at once
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    r.heater.set_percent(100)
    r.heater.update(0.0)
    assert r.heater_pin.is_on is True
    r.estop_sw.is_pressed = False
    r.controller.step(1.0)
    assert r.controller.state == State.E_STOP
    assert r.heater_pin.is_on is False
    assert r.heater.percent == 0

    # 77: E-stop while the door is moving: the motor stops and nothing moves by itself
    r = make_controller()
    set_door(r, 1.0)
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    run_hw(r, 1)
    r.estop_sw.is_pressed = False
    r.controller.step(r.now)
    assert r.controller.state == State.E_STOP
    position_at_stop = r.sim.position
    run_hw(r, 3)
    assert r.sim.position == position_at_stop
    assert not r.in1.is_on and not r.in2.is_on

    # 78: the E-stop wins over a fault that shows up in the same loop
    r = make_controller()
    r.sensor.temp = 50.0
    r.estop_sw.is_pressed = False
    r.controller.step(0.0)
    assert r.controller.state == State.E_STOP

    # 79: but if the controller is already in FAULT, it stays FAULT and keeps its reason
    r = make_controller()
    r.sensor.temp = 50.0
    r.controller.step(0.0)
    assert r.controller.fault_reason == "over_temperature"
    r.estop_sw.is_pressed = False
    r.controller.step(1.0)
    assert r.controller.state == State.FAULT
    assert r.controller.fault_reason == "over_temperature"

    # 80: releasing the E-stop does NOT leave E_STOP by itself
    r = make_controller()
    r.estop_sw.is_pressed = False
    r.controller.step(0.0)
    r.estop_sw.is_pressed = True                  # released again
    r.controller.step(1.0)
    assert r.controller.state == State.E_STOP

    # 81: reset() is refused while the E-stop is still pressed, accepted after release
    r = make_controller()
    r.estop_sw.is_pressed = False
    r.controller.step(0.0)
    r.controller.reset()
    r.controller.step(1.0)
    assert r.controller.state == State.E_STOP
    r.estop_sw.is_pressed = True
    r.controller.reset()
    r.controller.step(2.0)
    assert r.controller.state == State.IDLE

    # 82: the same from FAULT: the fault is gone, but the E-stop is pressed -> refused
    r = make_controller()
    r.sensor.temp = 50.0
    r.controller.step(0.0)
    r.sensor.temp = 25.0
    r.estop_sw.is_pressed = False
    r.controller.reset()
    r.controller.step(1.0)
    assert r.controller.state == State.FAULT
    r.estop_sw.is_pressed = True
    r.controller.reset()
    r.controller.step(2.0)
    assert r.controller.state == State.IDLE

    print("Controller E-stop checks passed ✅")

        # ---------- reset() leaves FAULT and E_STOP ----------
    # 67: FAULT, the cause is gone, reset() -> IDLE
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.sensor.temp = 50.0
    r.controller.step(0.0)
    assert r.controller.state == State.FAULT
    r.sensor.temp = 25.0
    r.controller.reset()
    r.controller.step(1.0)
    assert r.controller.state == State.IDLE
    assert r.controller.fault_reason is None
    assert r.heater.percent == 0
    assert r.controller.setpoint == 40.0          # the setpoint is kept

    # 68: the cause is still there -> reset() is refused
    r = make_controller()
    r.sensor.temp = 50.0
    r.controller.step(0.0)
    r.controller.reset()
    r.controller.step(1.0)
    assert r.controller.state == State.FAULT
    assert r.controller.fault_reason == "over_temperature"

    # 69: E_STOP (Stop button) -> reset() -> IDLE
    r = make_controller()
    r.stop_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.E_STOP
    r.controller.reset()
    r.controller.step(1.0)
    assert r.controller.state == State.IDLE

    # 70: a refused reset is used up: it does not wait for the cause to go away
    r = make_controller()
    r.sensor.temp = 50.0
    r.controller.step(0.0)
    r.controller.reset()
    r.controller.step(1.0)
    assert r.controller.state == State.FAULT
    r.sensor.temp = 25.0
    r.controller.step(2.0)                        # nobody called reset() again
    assert r.controller.state == State.FAULT

    # 71: reset() while everything is fine does nothing
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    assert r.controller.state == State.HEATING
    r.controller.reset()
    r.controller.step(1.0)
    assert r.controller.state == State.HEATING

    # 72: an old reset() cannot clear a fresh fault in the same loop
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    r.controller.reset()
    r.sensor.temp = 50.0
    r.controller.step(1.0)
    assert r.controller.state == State.FAULT

    # 73: after a door motor fault, reset() makes the motor usable again
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.open_sw.is_pressed = True
    r.motor.open(0.0)                             # both switches pressed -> the motor faults
    assert r.motor.state == MotorState.FAULT
    r.open_sw.is_pressed = False
    r.closed_sw.is_pressed = False                # the door now reads "halfway"
    r.controller.step(0.0)
    assert r.controller.fault_reason == "door_motor_switch_error"
    r.controller.reset()
    r.controller.step(1.0)
    assert r.controller.state == State.IDLE
    assert r.motor.state == MotorState.IDLE
    assert r.motor.fault_reason is None
    r.start_btn.press()
    r.controller.step(2.0)
    assert r.controller.state == State.DOOR_MOVING
    assert r.motor.state == MotorState.CLOSING

    # 74: after a reset a whole new run works
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.stop_btn.press()
    r.controller.step(0.0)
    r.controller.reset()
    r.controller.step(1.0)
    assert r.controller.state == State.IDLE
    r.start_btn.press()
    r.controller.step(2.0)
    assert r.controller.state == State.HEATING

    print("Controller reset checks passed ✅")

        # ---------- the faster Stop ----------
    # 83: emergency_cut() cuts the heater at once, without waiting for a loop
    r = make_controller()
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    r.heater.set_percent(100)
    r.heater.update(0.0)
    assert r.heater_pin.is_on is True
    r.controller.emergency_cut()
    assert r.heater_pin.is_on is False
    assert r.heater.percent == 0

    # 84: after a cut, the heater cannot be switched back on, even by a loop that was already running
    r.heater.set_percent(100)
    r.heater.update(0.0)
    assert r.heater_pin.is_on is False

    # 85: emergency_cut() also stops the door motor
    r = make_controller()
    set_door(r, 1.0)
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(r.now)
    assert r.motor.state == MotorState.CLOSING
    r.controller.emergency_cut()
    assert not r.in1.is_on and not r.in2.is_on and not r.sleep.is_on
    assert r.motor.state == MotorState.IDLE

    # 86: the next loop turns a cut into E_STOP, even if nothing is pressed any more
    r = make_controller()
    r.controller.emergency_cut()
    r.controller.step(0.0)
    assert r.controller.state == State.E_STOP

    # 87: the Stop button wired to the cut: the heater is off BEFORE any loop runs
    r = make_controller()
    r.buttons.on_press("stop", r.controller.emergency_cut)
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    r.heater.set_percent(100)
    r.heater.update(0.0)
    assert r.heater_pin.is_on is True
    r.stop_btn.press()
    assert r.heater_pin.is_on is False            # no step() has run yet
    r.controller.step(1.0)
    assert r.controller.state == State.E_STOP

    # 88: the same with the mushroom E-stop
    r = make_controller()
    r.estop.on_trigger(r.controller.emergency_cut)
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    r.heater.set_percent(100)
    r.heater.update(0.0)
    assert r.heater_pin.is_on is True
    r.estop_sw.release()                          # the NC contact opens
    assert r.heater_pin.is_on is False            # again before any step()
    r.controller.step(1.0)
    assert r.controller.state == State.E_STOP

    # 89: reset() unlocks the heater, and the next run heats again
    r = make_controller()
    r.buttons.on_press("stop", r.controller.emergency_cut)
    r.controller.set_setpoint(40.0)
    r.start_btn.press()
    r.controller.step(0.0)
    r.stop_btn.press()
    r.controller.step(1.0)
    assert r.controller.state == State.E_STOP
    assert r.heater.locked is True
    r.controller.reset()
    r.controller.step(2.0)
    assert r.controller.state == State.IDLE
    assert r.heater.locked is False
    r.sensor.temp = 20.0
    r.start_btn.press()
    r.controller.step(3.0)
    assert r.controller.state == State.HEATING
    r.controller.step(4.0)
    assert r.heater.percent == 100

    # 90: a cut while already in FAULT keeps the FAULT and its reason; reset unlocks later
    r = make_controller()
    r.sensor.temp = 50.0
    r.controller.step(0.0)
    r.controller.emergency_cut()
    r.controller.step(1.0)
    assert r.controller.state == State.FAULT
    assert r.controller.fault_reason == "over_temperature"
    r.sensor.temp = 25.0
    r.controller.reset()
    r.controller.step(2.0)
    assert r.controller.state == State.IDLE
    assert r.heater.locked is False

    # 91: reset() is refused while the E-stop is held in, and the heater stays locked
    r = make_controller()
    r.estop.on_trigger(r.controller.emergency_cut)
    r.estop_sw.release()
    r.controller.step(0.0)
    r.controller.reset()
    r.controller.step(1.0)
    assert r.controller.state == State.E_STOP
    assert r.heater.locked is True

    print("Controller faster-Stop checks passed ✅")