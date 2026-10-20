"""Compact process state machine. All methods run on the hardware worker."""
from datetime import datetime
from enum import Enum
from .pid import PID
from .safety import SafetyMonitor
from hardware.sensors import DoorState
from peak_tracking import PeakTracker

class State(Enum):
    READY = "ready"
    DOOR_MOVING = "door_moving"
    HEATING = "heating"
    HOLDING = "holding"
    COOLING = "cooling"
    COMPLETE = "complete"
    ABORTED = "aborted"
    FAULT = "fault"
    E_STOP = "e_stop"

class Controller:
    def __init__(self, hardware, config):
        self.hw, self.config = hardware, config
        self.safety, self.pid = SafetyMonitor(config), PID(config.PID_KP, config.PID_KI, config.PID_KD)
        self.state, self.fault_reason = State.READY, ""
        self.target, self.hold_s, self.gains = config.MIN_SETPOINT, 60.0, (config.PID_KP, config.PID_KI, config.PID_KD)
        self.run_started = self.stable_since = self.hold_started = None
        self.heating_start_temperature = self.cooling_start_temperature = None
        self.run_started_at = None
        self.run_settings = {}
        self.hold_reached_s = self.cooling_started = self.cooldown_s = None
        self.heating_start_temperature = self.cooling_start_temperature = None
        self.hold_elapsed_s = 0.0
        self.stop_state = self.stop_elapsed_s = self.stop_stage = None
        self.stop_fraction = None
        self.stop_detail = None
        self.door_goal = None
        self.status_text = "Ready to run"
        self.progress_fraction, self.progress_detail = 0.0, ""
        self.last_step = None
        self.last_pid_time = None
        self.run_ended = None
        self.peak_temp = self.peak_time = None
        self.hold_min = self.hold_min_time = None
        self.hold_completed = False
        self.peak_tracker = PeakTracker()
    @property
    def active(self): return self.state in (State.DOOR_MOVING, State.HEATING, State.HOLDING, State.COOLING)
    def start(self, now, target, hold_s, gains, temperature):
        if self.active: return
        if self.state not in (State.READY, State.COMPLETE, State.ABORTED): raise RuntimeError("Reset the safety fault before starting another run")
        if temperature is None: raise RuntimeError("Waiting for a valid temperature reading")
        self.target = self.safety.validate_setpoint(target)
        self.hold_s = min(self.config.HOLD_DURATION_MAX_S, max(self.config.HOLD_DURATION_MIN_S, float(hold_s)))
        self.pid.set_gains(*gains); self.gains = tuple(gains); self.pid.reset()
        self.run_started, self.stable_since, self.hold_started = now, None, None
        self.run_started_at = datetime.now().astimezone().isoformat(timespec="microseconds")
        self.run_settings = {"target_temperature_c": self.target, "hold_duration_s": self.hold_s,
            "kp": self.gains[0], "ki": self.gains[1], "kd": self.gains[2]}
        self.hold_reached_s = self.cooling_started = self.cooldown_s = None
        self.hold_elapsed_s = 0.0
        self.stop_state = self.stop_elapsed_s = self.stop_stage = None
        self.stop_fraction = self.stop_detail = None
        self.last_pid_time = None
        self.run_ended = None
        self.peak_temp = self.peak_time = self.hold_min = self.hold_min_time = None
        self.hold_completed = False
        self.peak_tracker = PeakTracker()
        self.completion_allowed = True
        self.fault_reason = ""
        if self.hw.switches.state() == DoorState.CLOSED:
            self.heating_start_temperature = temperature
            self._set_state(State.HEATING, "Warming up")
        else:
            self.hw.motor.close_door(now); self.door_goal = DoorState.CLOSED
            self._set_state(State.DOOR_MOVING, "Closing door")
    def _set_state(self, state, text=None):
        self.state = state
        if state in (State.COMPLETE, State.ABORTED, State.FAULT, State.E_STOP) and self.run_ended is None:
            self.run_ended = self.last_step
        self.status_text = text or state.value.replace("_", " ").title()
        self.hw.tower.show(state.value)
        if state in (State.HEATING, State.READY): self.pid.reset()
    def stop(self, now, reason="Stopped by operator"):
        if self.state in (State.READY, State.COMPLETE, State.ABORTED, State.FAULT, State.E_STOP):
            return
        stages = {State.DOOR_MOVING: 1, State.HEATING: 1, State.HOLDING: 2, State.COOLING: 3}
        if self.state == State.HOLDING and self.hold_started is not None:
            self.hold_elapsed_s += max(0.0, now - self.hold_started)
            self.hold_started = None
        self.stop_state = self.state.value
        self.stop_elapsed_s = max(0.0, now - self.run_started)
        self.stop_stage = stages[self.state]
        self.stop_fraction = self.progress_fraction
        self.stop_detail = reason
        self.last_step = now
        self.hw.heater.off()
        self.hw.motor.stop()
        self.stable_since = None
        self.last_pid_time = None
        self._set_state(State.ABORTED, f"{reason}; start a new run to continue")
    def emergency_stop(self, reason="emergency_stop"):
        # The physical E-Stop removes power; only end the run and leave outputs idle.
        if self.state == State.E_STOP: return
        self.hw.heater.off(); self.hw.motor.stop()
        self.fault_reason = reason
        self._set_state(State.E_STOP, "Emergency stop")
    def reset(self, now, temperature, age_s, estop):
        if self.active: raise RuntimeError("Stop or finish the active run before resetting")
        fault = self.safety.check(temperature, age_s, self.hw.switches.state(), self.hw.motor.fault_reason, False, estop)
        if estop or fault in ("sensor_timeout", "sensor_invalid", "over_temperature", "door_error"):
            raise RuntimeError(f"Cannot reset while safety condition remains: {fault or 'emergency stop'}")
        self.hw.motor.clear_fault(); self.hw.heater.unlock(); self.hw.heater.off()
        self.fault_reason = ""; self.run_started = None; self.run_ended = None; self.hold_completed = False; self.completion_allowed = True
        self._set_state(State.READY, "Ready to run")
    def fault(self, reason):
        self.hw.heater.emergency_off(); self.hw.motor.stop()
        self.fault_reason = reason; self._set_state(State.FAULT, f"Fault: {reason.replace('_',' ')}")
    def step(self, now, temperature, age_s, estop, control_due=True):
        door = self.hw.switches.state()
        fault = self.safety.check(temperature, age_s, door, self.hw.motor.fault_reason,
            False, estop)
        if fault:
            if fault == "emergency_stop": self.emergency_stop()
            elif self.state not in (State.FAULT, State.E_STOP): self.fault(fault)
            return
        if self.state in (State.HEATING, State.HOLDING) and door != DoorState.CLOSED:
            self.stop(now, "Door opened; run stopped")
            return
        if self.state in (State.FAULT, State.E_STOP, State.COMPLETE, State.ABORTED): return
        if temperature is not None and self.run_started is not None:
            elapsed = now - self.run_started
            self.peak_tracker.update(temperature, elapsed)
            self.peak_temp = self.peak_tracker.peak_temperature_c
            self.peak_time = self.peak_tracker.peak_time_s
        self.last_step = now
        if self.state == State.DOOR_MOVING:
            if self.hw.motor.state.value == "fault": self.fault(f"door_motor_{self.hw.motor.fault_reason}")
            elif self.hw.motor.state.value == "idle":
                if self.hw.switches.state() != self.door_goal: self.fault("door_not_in_position")
                else:
                    self.heating_start_temperature = temperature
                    self._set_state(State.HEATING, "Warming up")
        elif self.state in (State.HEATING, State.HOLDING) and temperature is not None:
            if control_due:
                dt = self.config.LOOP_PERIOD_S if self.last_pid_time is None else max(0.0, now-self.last_pid_time)
                self.last_pid_time = now
                feedforward = max(0.0, self.config.FF_PERCENT_PER_DEGREE*(self.target-self.config.ROOM_TEMPERATURE))
                self.hw.heater.set_percent(self.pid.update(self.target, temperature, dt, feedforward))
            if self.state == State.HEATING:
                start_temperature = self.heating_start_temperature
                if start_temperature is not None and abs(self.target - start_temperature) > self.config.STABLE_TOLERANCE:
                    self.progress_fraction = min(1.0, max(0.0,
                        (temperature - start_temperature) / (self.target - start_temperature)))
                else:
                    self.progress_fraction = 1.0 if abs(temperature - self.target) <= self.config.STABLE_TOLERANCE else 0.0
                if abs(temperature-self.target) <= self.config.STABLE_TOLERANCE:
                    self.stable_since = now if self.stable_since is None else self.stable_since
                    held = now-self.stable_since
                    self.progress_detail = f"Stable {held:.0f}/{self.config.STABLE_TIME_S:.0f} s"
                    if held >= self.config.STABLE_TIME_S:
                        self.hold_started = now
                        self.hold_elapsed_s = 0.0
                        self.hold_reached_s = now-self.run_started
                        self.hold_min, self.hold_min_time = temperature, now-self.run_started
                        self._set_state(State.HOLDING, "Holding target")
                else: self.stable_since, self.progress_detail = None, ""
            else:
                elapsed = self.hold_elapsed_s + (now-self.hold_started)
                if self.hold_min is None or temperature < self.hold_min:
                    self.hold_min, self.hold_min_time = temperature, now-self.run_started
                self.progress_fraction = min(1.0, elapsed/self.hold_s)
                self.progress_detail = f"{max(0.0,self.hold_s-elapsed):.0f} s remaining"
                if elapsed >= self.hold_s:
                    self.hold_elapsed_s = elapsed
                    self.hold_completed = True
                    self.cooling_started = now
                    self.cooling_start_temperature = temperature
                    self.progress_fraction = 0.0
                    self.hw.heater.off(); self._set_state(State.COOLING, "Cooling down")
        elif self.state == State.COOLING:
            self.hw.heater.off()
            start_temperature = self.cooling_start_temperature
            safe_temperature = self.config.DOOR_SAFE_TEMPERATURE
            if start_temperature is not None and start_temperature > safe_temperature:
                self.progress_fraction = min(1.0, max(0.0,
                    (start_temperature - temperature) / (start_temperature - safe_temperature)))
            elif temperature <= safe_temperature:
                self.progress_fraction = 1.0
            if self.safety.door_may_open(temperature):
                if door != DoorState.OPEN and self.hw.motor.state.value == "idle":
                    self.hw.motor.open(now)
                elif door == DoorState.OPEN and self.hw.motor.state.value == "idle":
                    self.cooldown_s = max(0.0, now - self.cooling_started) if self.cooling_started is not None else None
                    if self.completion_allowed:
                        self._set_state(State.COMPLETE, "Run complete")
                    else:
                        stopped_during = self.stop_state.replace("_", " ").title() if self.stop_state else "run"
                        self._set_state(State.ABORTED, f"Stopped during {stopped_during}; cooldown complete")
        self.hw.tower.show(self.state.value)
    def snapshot_fields(self):
        stage = {State.READY:0,State.COMPLETE:4,State.ABORTED:(self.stop_stage or 0),State.FAULT:0,State.E_STOP:0,
            State.DOOR_MOVING:1,State.HEATING:1,State.HOLDING:2,State.COOLING:3}[self.state]
        elapsed = 0.0 if self.run_started is None else max(0.0, (self.last_step if self.run_ended is None else self.run_ended)-self.run_started)
        stopped = self.state == State.ABORTED
        fraction = self.stop_fraction if stopped and self.stop_fraction is not None else self.progress_fraction
        detail = self.stop_detail if stopped and self.stop_detail is not None else self.progress_detail
        if self.state in (State.FAULT,State.E_STOP): detail = self.fault_reason
        held_s = self.hold_elapsed_s
        if self.state == State.HOLDING and self.hold_started is not None:
            held_s += max(0.0, (self.last_step or self.hold_started) - self.hold_started)
        return (self.active, stage, fraction, detail, self.status_text, elapsed,
            self.target, self.hw.heater.percent, self.hw.switches.state().value, self.state.value,
            self.peak_time, self.peak_temp, self.hold_min_time, self.hold_min, self.hold_completed,
            self.run_started_at, self.run_settings, self.hold_reached_s, self.cooldown_s,
            self.stop_state, self.stop_elapsed_s, self.stop_stage, self.stop_fraction, self.stop_detail,
            held_s)
