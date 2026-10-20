"""Qt-safe application service and bounded-latency hardware worker."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from queue import Empty, SimpleQueue
import threading
import time

from control.controller import Controller, State
from hardware.factory import create_hardware
from data_manager import DataManager
from pid_tuning_monitor import append_recommendation

@dataclass(frozen=True, slots=True)
class ViewState:
    running: bool
    stage: int
    fraction: float
    detail: str
    status: str
    elapsed_s: float
    target_c: float
    output_percent: float
    door: str
    state: str
    peak_time_s: float | None
    peak_c: float | None
    hold_min_time_s: float | None
    hold_min_c: float | None
    hold_completed: bool
    run_started_at: str | None = None
    run_settings: dict | None = None
    hold_reached_s: float | None = None
    cooldown_s: float | None = None
    stop_state: str | None = None
    stop_elapsed_s: float | None = None
    stop_stage: int | None = None
    stop_fraction: float | None = None
    stop_detail: str | None = None
    hold_elapsed_s: float = 0.0
    error: str = ""
    events: tuple = ()
    current_temperature_c: float | None = None
    can_start: bool = False
    ready_reason: str = "Waiting for a valid temperature reading"
    estop_active: bool = False

# Physical buttons and their on-screen equivalents queue these same commands.
OPERATOR_BUTTON_COMMANDS = ("start", "stop", "open", "close")

FAULT_DESCRIPTIONS = {
    "sensor_timeout": "Temperature sensor is not responding",
    "sensor_invalid": "Temperature reading is invalid",
    "over_temperature": "Temperature reached the safety limit",
    "door_error": "Both door limit switches report pressed",
    "door_motor_timeout": "Door did not reach its limit switch in time",
    "door_motor_switch_error": "Door limit switches conflicted while the door moved",
    "door_not_in_position": "Door stopped away from its expected position",
}

def describe_fault(reason: str) -> str:
    """Return an operator-facing explanation of a controller fault reason."""
    if reason.startswith("worker_error: "):
        return "Control error: " + reason.removeprefix("worker_error: ")
    return FAULT_DESCRIPTIONS.get(reason, reason.replace("_", " ").capitalize())

class Application:
    """Owns hardware, control thread and immutable UI-facing snapshots."""
    def __init__(self, config):
        self.config = config
        self._start_settings = dict(target_temperature_c=45.0, hold_duration_s=60.0,
            kp=config.PID_KP, ki=config.PID_KI, kd=config.PID_KD)
        self.hw = create_hardware(config)
        self.controller = Controller(self.hw, config)
        self.commands = SimpleQueue()
        self.samples = []
        self._lock = threading.Lock()
        self._error = ""
        self._last_saved_run_started_at = None
        self._last_run_record = None
        self._events = []
        self._run_started_monotonic = None
        self._event_log_closed = True
        self._pending_pid_event = None
        self._last_door_state = self.hw.switches.state().value
        self._save_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="run-storage")
        self._view = ViewState(False,0,0.0,"","Ready to run",0.0,config.MIN_SETPOINT,0.0,"unknown","ready",None,None,None,None,False)
        self._current_temperature_c = None
        self._sensor_age_s = float("inf")
        self._estop_active = False
        self._manual_override_active = False
        self._closing = threading.Event()
        self._closed = False
        for name in OPERATOR_BUTTON_COMMANDS:
            self.hw.buttons[name].when_pressed = lambda name=name: self.command(name)
        # The E-Stop circuit cuts power itself; the app only listens so it can end the run.
        callback = lambda: self.command("emergency", "emergency_stop")
        if config.ESTOP_NORMALLY_CLOSED: self.hw.estop_device.when_deactivated = callback
        else: self.hw.estop_device.when_activated = callback
        self._thread = threading.Thread(target=self._run, name="kiln-control", daemon=False)
        self._thread.start()
    def command(self, name, values=None):
        self.commands.put((name, values))
    def _enter_emergency_stop(self, now, reason):
        if self.controller.state == State.E_STOP:
            return
        self._manual_override_active = False
        self.controller.emergency_stop(reason)
        self._append_event("emergency_stop", "Emergency stop activated", now, reason=str(reason))
    def snapshot(self):
        with self._lock:
            view = self._view
            return view, tuple(self.samples)
    @property
    def last_run_record(self):
        with self._lock:
            return self._last_run_record
    def _event_elapsed(self, now=None):
        if self._run_started_monotonic is None:
            return 0.0
        return max(0.0, (time.monotonic() if now is None else now) - self._run_started_monotonic)

    def _append_event(self, event_type, description, now=None, **details):
        if self._run_started_monotonic is None or self._event_log_closed:
            return
        event = {"elapsed_s": round(self._event_elapsed(now), 3), "type": event_type,
                 "description": description}
        event.update(details)
        self._events.append(event)
        self._events.sort(key=lambda item: item["elapsed_s"])

    def _flush_pid_event(self):
        pending = self._pending_pid_event
        self._pending_pid_event = None
        if pending is None or pending["before"] == pending["after"]:
            return
        changed = {key: {"from": before, "to": after}
                   for key, before, after in zip(("kp", "ki", "kd"), pending["before"], pending["after"])
                   if before != after}
        self._append_event("pid_gains_changed", "PID gains changed", pending["elapsed"],
                           changes=changed)

    def _record_state_transition(self, before, after, now):
        if before == after:
            return
        labels = {
            (State.HEATING, State.HOLDING): ("stage_completed", "Warm up completed"),
            (State.HOLDING, State.COOLING): ("stage_completed", "Hold completed"),
            (State.COOLING, State.COMPLETE): ("stage_completed", "Cool down completed"),
        }
        stage_event = labels.get((before, after))
        if stage_event:
            self._append_event(stage_event[0], stage_event[1], now)
            if after == State.HOLDING:
                self._append_event("stage_started", "Hold started", now)
            elif after == State.COOLING:
                self._append_event("stage_started", "Cool down started", now)
        elif before == State.DOOR_MOVING and after == State.HEATING:
            self._append_event("stage_started", "Warm up started", now)
        if after == State.ABORTED:
            if before == State.COOLING:
                self._append_event("cooldown_completed", "Cool down completed after stop", now)
            self._append_event("run_aborted", self.controller.status_text, now,
                               stopped_during=self.controller.stop_state)
        elif after == State.COMPLETE:
            self._append_event("run_completed", "Run completed", now)
        elif after in (State.FAULT, State.E_STOP):
            self._append_event("run_faulted" if after == State.FAULT else "emergency_stop",
                               self.controller.status_text, now, reason=self.controller.fault_reason)

    def _publish(self, error=""):
        fields = self.controller.snapshot_fields()
        state = self.controller.state
        allowed_state = state in (State.READY, State.COMPLETE, State.ABORTED)
        reason = ""
        if not allowed_state:
            reason = "Reset the safety fault before starting" if state in (State.FAULT, State.E_STOP) else "A run is already in progress"
        else:
            reason = self.controller.safety.check(
                self._current_temperature_c, self._sensor_age_s, self.hw.switches.state(),
                self.hw.motor.fault_reason, False, self._estop_active,
            ) or ""
            if not reason and self.hw.motor.state.value != "idle":
                reason = "Wait for the door motor to stop"
            if not reason:
                try:
                    self.controller.safety.validate_setpoint(self._start_settings["target_temperature_c"])
                except (ValueError, KeyError) as exc:
                    reason = str(exc)
            if not reason and self._current_temperature_c is None:
                reason = "Waiting for a valid temperature reading"
        ready_text = {
            "sensor_timeout": "Temperature sensor is not responding",
            "sensor_invalid": "Temperature reading is invalid",
            "over_temperature": "Temperature is above the safe start limit",
            "door_error": "Door sensor fault",
            "emergency_stop": "Emergency stop is active",
        }.get(reason, reason)
        with self._lock:
            self._error = error or self._error
            view = ViewState(*fields, error=self._error, events=tuple(dict(event) for event in self._events),
                current_temperature_c=self._current_temperature_c,
                can_start=not reason and allowed_state, ready_reason=ready_text,
                estop_active=self._estop_active)
            self._view = view
            samples = tuple(self.samples)
        terminal = view.state in ("complete", "aborted", "fault", "e_stop")
        if terminal and view.run_started_at and view.run_started_at != self._last_saved_run_started_at:
            record = DataManager.build_run_record(view, samples)
            with self._lock:
                self._last_saved_run_started_at = view.run_started_at
                self._last_run_record = record
            try:
                future = self._save_executor.submit(
                    self._save_run_and_analyze, record,
                )
                future.add_done_callback(self._save_run_done)
            except RuntimeError as exc:
                self._save_run_error(exc)
    def _save_run_and_analyze(self, record):
        saved_path = DataManager(self.config.DATA_DIRECTORY).export_run(record)
        try:
            append_recommendation(record, self.config.DATA_DIRECTORY)
        except Exception as exc:
            # Run persistence remains successful even if this temporary observer fails.
            with self._lock:
                self._error = f"PID tuning log failed: {exc}"
                self._view = replace(self._view, error=self._error)
        return saved_path
    def _save_run_done(self, future):
        try:
            future.result()
        except Exception as exc:
            self._save_run_error(exc)

    def _save_run_error(self, exc):
        with self._lock:
            self._error = f"Run auto-save failed: {exc}"
            self._view = replace(self._view, error=self._error)

    def _apply_commands(self, now, temperature, age):
        while True:
            try: name, value = self.commands.get_nowait()
            except Empty: return
            try:
                if ((self.controller.state == State.E_STOP or self._estop_active)
                        and name in ("start", "open", "close", "heater_on")):
                    raise RuntimeError("Emergency stop is active; release it and press Reset before using the heater or door")
                if (self.controller.state == State.FAULT
                        and name in ("start", "open", "close", "heater_on")):
                    raise RuntimeError("Safety fault is latched; reset it before using the heater or door")
                if name == "start":
                    if self.controller.active:
                        if self._manual_override_active:
                            self._manual_override_active = False
                            self.hw.heater.off()
                        continue
                    self._manual_override_active = False
                    self.hw.heater.off()
                    settings = self._start_settings
                    self.controller.start(now, settings["target_temperature_c"], settings["hold_duration_s"],
                        (settings["kp"],settings["ki"],settings["kd"]), temperature)
                    self.hw.heater.unlock()
                    self._last_saved_run_started_at = None
                    with self._lock:
                        self.samples.clear()
                        self._events.clear()
                        self._error = ""
                        self._last_run_record = None
                    self._run_started_monotonic = now
                    self._event_log_closed = False
                    self._pending_pid_event = None
                    self._append_event("run_started", "Run started", now,
                        settings=dict(self.controller.run_settings))
                    initial_door = self.hw.switches.state().value
                    if initial_door in ("open", "closed"):
                        self._append_event(
                            "door_opened" if initial_door == "open" else "door_closed",
                            f"Door was {initial_door} when run started", now)
                    if self.controller.state == State.DOOR_MOVING:
                        self._append_event("stage_started", "Closing door", now)
                    else:
                        self._append_event("stage_started", "Warm up started", now)
                elif name == "stop":
                    self._manual_override_active = False
                    self.hw.heater.off()
                    old_state = self.controller.state
                    self.controller.stop(now)
                    if old_state != self.controller.state:
                        self._append_event("run_stopped", "Run stopped by user; heater off", now,
                                           stopped_during=old_state.value)
                elif name == "emergency":
                    self._enter_emergency_stop(now, value)
                elif name == "reset":
                    self._manual_override_active = False
                    self.controller.reset(now, temperature, age, self.hw.estop())
                    self._run_started_monotonic = None
                    self._event_log_closed = True
                    self._pending_pid_event = None
                    with self._lock:
                        self.samples.clear()
                        self._events.clear()
                        self._error = ""
                elif name == "defaults":
                    if self.controller.active: raise RuntimeError("Settings cannot be imported during an active run")
                    self._start_settings = dict(value)
                    self.controller.target = float(value["target_temperature_c"])
                    self.controller.hold_s = float(value["hold_duration_s"])
                    self.controller.gains = (float(value["kp"]), float(value["ki"]), float(value["kd"]))
                    self.controller.pid.set_gains(*self.controller.gains)
                elif name == "settings":
                    self._start_settings = dict(value)
                elif name == "tune":
                    gains = (value["kp"],value["ki"],value["kd"])
                    previous = tuple(self.controller.gains)
                    self.controller.pid.set_gains(*gains); self.controller.gains = gains
                    if self.controller.active and previous != gains:
                        pending = self._pending_pid_event
                        self._pending_pid_event = {
                            "before": pending["before"] if pending else previous,
                            "after": gains, "elapsed": now,
                            "last_change": now,
                        }
                elif name == "open":
                    if self.controller.active:
                        raise RuntimeError("Manual door controls are unavailable during a run")
                    self._manual_override_active = True
                    self.hw.motor.open(now)
                elif name == "close":
                    if self.controller.active:
                        raise RuntimeError("Manual door controls are unavailable during a run")
                    self._manual_override_active = True
                    self.hw.motor.close_door(now)
                elif name == "read_temperature":
                    self.hw.sensor.update(now)
                    self._current_temperature_c = self.hw.sensor.temperature()
                    self._sensor_age_s = self.hw.sensor.seconds_since_valid(now)
                elif name == "heater_on":
                    if self.controller.active:
                        raise RuntimeError("Manual heating is unavailable during a run")
                    self._manual_override_active = True
                    self.hw.heater.unlock()
                    self.hw.heater.set_percent(100.0)
                elif name == "heater_off":
                    if self.controller.active:
                        self.controller.stop(now)
                    self._manual_override_active = True
                    self.hw.heater.off()
            except (ValueError, RuntimeError, KeyError) as exc:
                with self._lock: self._error = str(exc)
    def _run(self):
        period = self.config.SENSOR_PERIOD_S
        next_tick = time.monotonic()
        next_control = next_tick
        try:
            self.hw.heater.off()
            while not self._closing.is_set():
                now = time.monotonic()
                self.hw.sensor.update(now)
                temperature = self.hw.sensor.temperature()
                age = self.hw.sensor.seconds_since_valid(now)
                self._current_temperature_c = temperature
                self._sensor_age_s = age
                self._estop_active = self.hw.estop()
                if self._estop_active:
                    self._enter_emergency_stop(now, "emergency_stop")
                elif self.controller.state == State.E_STOP:
                    # The physical switch is the E-Stop latch. Recover on release
                    # without clearing the just-finished run's samples or events.
                    try:
                        self.controller.reset(now, temperature, age, False)
                    except RuntimeError:
                        # Other active safety faults still require operator reset.
                        pass
                    else:
                        self._run_started_monotonic = None
                        self._event_log_closed = True
                self.hw.motor.update(now)
                self._apply_commands(now, temperature, age)
                if self.controller.active and temperature is not None:
                    elapsed = max(0.0, now - self.controller.run_started)
                    sample = {"elapsed_s": elapsed, "temperature_c": temperature,
                        "target_temperature_c": self.controller.target, "heater_output_percent": self.hw.heater.percent,
                        "kp": self.controller.gains[0], "ki": self.controller.gains[1], "kd": self.controller.gains[2]}
                    with self._lock: self.samples.append(sample)
                control_due = now >= next_control
                if control_due: next_control = now + self.config.LOOP_PERIOD_S
                before_state = self.controller.state
                if not self._manual_override_active:
                    self.controller.step(now, temperature, age, self._estop_active, control_due)
                door_state = self.hw.switches.state().value
                if door_state != self._last_door_state and self._run_started_monotonic is not None:
                    event_type = "door_opened" if door_state == "open" else "door_closed"
                    self._append_event(event_type, f"Door {door_state}", now)
                self._last_door_state = door_state
                self._record_state_transition(before_state, self.controller.state, now)
                if self._pending_pid_event and now - self._pending_pid_event["last_change"] >= 0.6:
                    self._flush_pid_event()
                if self.controller.state in (State.COMPLETE, State.ABORTED, State.FAULT, State.E_STOP):
                    self._flush_pid_event()
                    self._event_log_closed = True
                self.hw.heater.update(now)
                self._publish()
                next_tick += period
                self._closing.wait(max(0.0, next_tick-time.monotonic()))
                if next_tick < time.monotonic()-period: next_tick = time.monotonic()
        except Exception as exc:
            self.controller.fault(f"worker_error: {exc}")
            self._publish(str(exc))
        finally:
            if self.controller.active:
                self.controller.stop(time.monotonic(), "Application closed; run stopped")
                self._publish()
            self.hw.heater.emergency_off()
            self.hw.motor.stop()
            self.hw.close()
    def close(self):
        if self._closed: return
        self._closed = True
        self._closing.set()
        self._thread.join(timeout=3.0)
        if self._thread.is_alive():
            self.hw.heater.emergency_off()
            raise RuntimeError("Control worker did not stop cleanly")
        self._save_executor.shutdown(wait=True)




