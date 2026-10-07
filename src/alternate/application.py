"""Qt-safe application service and bounded-latency hardware worker."""
from __future__ import annotations
from dataclasses import dataclass
from queue import Empty, SimpleQueue
import threading
import time

from control.controller import Controller, State
from hardware.factory import create_hardware

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
    error: str = ""

class Application:
    """Owns hardware, control thread and immutable UI-facing snapshots."""
    def __init__(self, config):
        self.config = config
        self.hw = create_hardware(config)
        self.controller = Controller(self.hw, config)
        self.commands = SimpleQueue()
        self.samples = []
        self._lock = threading.Lock()
        self._error = ""
        self._view = ViewState(False,0,0.0,"","Ready to run",0.0,config.MIN_SETPOINT,0.0,"unknown","ready",None,None,None,None,False)
        self._closing = threading.Event()
        self._closed = False
        self.hw.buttons["start"].when_pressed = lambda: self.command("start", self.defaults())
        self.hw.buttons["stop"].when_pressed = lambda: self.command("stop")
        self.hw.buttons["open"].when_pressed = lambda: self.command("open")
        self.hw.buttons["close"].when_pressed = lambda: self.command("close")
        callback = lambda: self._emergency_cut("emergency_stop")
        if config.ESTOP_NORMALLY_CLOSED: self.hw.estop_device.when_released = callback
        else: self.hw.estop_device.when_pressed = callback
        self._thread = threading.Thread(target=self._run, name="kiln-control", daemon=False)
        self._thread.start()
    def defaults(self):
        return dict(target_temperature_c=45.0, hold_duration_s=60.0,
            kp=self.config.PID_KP, ki=self.config.PID_KI, kd=self.config.PID_KD)
    def command(self, name, values=None):
        self.commands.put((name, values))
    def _emergency_cut(self, reason):
        self.hw.heater.emergency_off()
        self.hw.motor.stop()
        self.command("emergency", reason)
    def snapshot(self):
        with self._lock:
            view = self._view
            return view, tuple(self.samples)
    def _publish(self, error=""):
        fields = self.controller.snapshot_fields()
        with self._lock:
            self._error = error or self._error
            self._view = ViewState(*fields, error=self._error)
    def _apply_commands(self, now, temperature, age):
        while True:
            try: name, value = self.commands.get_nowait()
            except Empty: return
            try:
                if name == "start":
                    self.controller.start(now, value["target_temperature_c"], value["hold_duration_s"],
                        (value["kp"],value["ki"],value["kd"]), temperature)
                    self.hw.heater.unlock()
                    with self._lock: self.samples.clear(); self._error = ""
                elif name == "stop": self.controller.stop()
                elif name == "emergency": self.controller.emergency_stop(value)
                elif name == "reset":
                    self.controller.reset(now, temperature, age, self.hw.estop())
                    with self._lock:
                        self.samples.clear()
                        self._error = ""
                elif name == "tune":
                    gains = (value["kp"],value["ki"],value["kd"])
                    self.controller.pid.set_gains(*gains); self.controller.gains = gains
                elif name == "open":
                    if self.controller.state in (State.READY, State.COMPLETE) and self.controller.safety.door_may_open(temperature): self.hw.motor.open(now)
                elif name == "close":
                    if self.controller.state in (State.READY, State.COMPLETE): self.hw.motor.close_door(now)
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
                self.hw.motor.update(now)
                self._apply_commands(now, temperature, age)
                if self.controller.active and temperature is not None:
                    elapsed = max(0.0, now - self.controller.run_started)
                    sample = {"elapsed_s": elapsed, "temperature_c": temperature,
                        "target_temperature_c": self.controller.target, "heater_output_percent": self.hw.heater.percent}
                    with self._lock: self.samples.append(sample)
                control_due = now >= next_control
                if control_due: next_control = now + self.config.LOOP_PERIOD_S
                self.controller.step(now, temperature, age, self.hw.estop(), control_due)
                self.hw.heater.update(now)
                self._publish()
                next_tick += period
                self._closing.wait(max(0.0, next_tick-time.monotonic()))
                if next_tick < time.monotonic()-period: next_tick = time.monotonic()
        except Exception as exc:
            self.controller.emergency_stop(f"worker_error: {exc}")
            self._publish(str(exc))
        finally:
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




