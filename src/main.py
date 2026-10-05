"""Builds the kiln, connects the parts, and runs the control loops."""

import time
from types import SimpleNamespace

import config
from control.controller import Controller
from control.pid import PID
from control.safety import SafetyMonitor
from hardware.actuators import DoorMotor, Heater, NullTower
from hardware.sensors import Buttons, DoorSwitches, EmergencyStop, TemperatureSensor

WARMUP_S = config.SENSOR_FAULT_TIMEOUT_S     # give the sensor time to deliver readings first


class Kiln:
    """All the parts of the kiln, connected. Real hardware or simulation, chosen by config.SIMULATION."""

    def __init__(self, simulation=config.SIMULATION):
        self.simulation = simulation
        if simulation:
            self.devices = self._build_simulated()
        else:
            self.devices = self._build_real()
        self._connect()
        self._running = False
        self._started = None
        self._last_tick = None
        self._last_step = None

    def _build_simulated(self):
        from hardware.simulated import (SimulatedButton, SimulatedDoor, SimulatedKiln,
                                        SimulatedPin, SimulatedSwitch, SimulatedTemperatureProbe)
        d = SimpleNamespace()
        d.kiln = SimulatedKiln()
        d.probe = SimulatedTemperatureProbe(d.kiln)
        d.heater_pin = SimulatedPin()
        d.motor_in1, d.motor_in2, d.motor_sleep = SimulatedPin(), SimulatedPin(), SimulatedPin()
        d.open_switch, d.closed_switch = SimulatedSwitch(), SimulatedSwitch()
        d.door = SimulatedDoor(d.motor_in1, d.motor_in2, d.open_switch, d.closed_switch)
        d.buttons = {"start": SimulatedButton(), "stop": SimulatedButton()}
        d.estop_switch = SimulatedButton()
        d.estop_switch.is_pressed = True            # NC contact closed = healthy
        return d

    def _build_real(self):
        from gpiozero import Button, DigitalOutputDevice     # only available on the Pi
        from hardware.sensors import Max31865Probe
        d = SimpleNamespace()
        d.probe = Max31865Probe(config.TEMP_CS_PIN, config.RTD_NOMINAL,
                                config.RTD_REF_RESISTOR, config.RTD_WIRES)
        d.heater_pin = DigitalOutputDevice(config.HEATER_PIN)
        d.motor_in1 = DigitalOutputDevice(config.MOTOR_IN1_PIN)
        d.motor_in2 = DigitalOutputDevice(config.MOTOR_IN2_PIN)
        d.motor_sleep = DigitalOutputDevice(config.MOTOR_SLEEP_PIN)
        d.open_switch = Button(config.DOOR_LIMIT_OPEN_PIN, bounce_time=0.05)
        d.closed_switch = Button(config.DOOR_LIMIT_CLOSED_PIN, bounce_time=0.05)
        d.buttons = {
            "start": Button(config.BUTTON_START_PIN, bounce_time=0.05),
            "stop": Button(config.BUTTON_STOP_PIN, bounce_time=0.05),
        }
        d.estop_switch = Button(config.ESTOP_SENSE_PIN, bounce_time=0.05)
        return d

    def _connect(self):
        d = self.devices
        self.sensor = TemperatureSensor(d.probe, window=config.FILTER_WINDOW)
        self.heater = Heater(d.heater_pin)
        self.switches = DoorSwitches(d.open_switch, d.closed_switch)
        self.motor = DoorMotor(d.motor_in1, d.motor_in2, d.motor_sleep, self.switches)
        self.buttons = Buttons(d.buttons)
        self.estop = EmergencyStop(d.estop_switch, normally_closed=config.ESTOP_NORMALLY_CLOSED)
        self.pid = PID(config.PID_KP, config.PID_KI, config.PID_KD)
        self.controller = Controller(self.sensor, self.switches, self.motor, self.heater,
                                     SafetyMonitor(), NullTower(), self.buttons, self.pid,
                                     self.estop)
        self.buttons.on_press("stop", self.controller.emergency_cut)
        self.estop.on_trigger(self.controller.emergency_cut)

    def _simulate(self, now):
        dt = 0.0 if self._last_tick is None else now - self._last_tick
        self._last_tick = now
        d = self.devices
        d.kiln.heater_percent = 100.0 if d.heater_pin.is_on else 0.0
        d.kiln.update(dt)
        d.door.update(dt)

    def tick(self, now):
        """One fast tick, about 15 times per second. Runs the controller once per second."""
        if self._started is None:
            self._started = now
        if self.simulation:
            self._simulate(now)
        self.sensor.update()
        self.heater.update(now)
        self.motor.update(now)
        if now - self._started < WARMUP_S:
            return
        if self._last_step is None or now - self._last_step >= config.LOOP_PERIOD_S:
            self._last_step = now
            self.controller.step(now)

    def run_forever(self):
        """Run until stop() is called or something crashes. The heater and motor are always cut at the end."""
        self._running = True
        next_tick = time.monotonic()
        try:
            while self._running:
                self.tick(time.monotonic())
                next_tick += config.SENSOR_PERIOD_S
                time.sleep(max(0.0, next_tick - time.monotonic()))
        finally:
            self.controller.emergency_cut()

    def stop(self):
        self._running = False


def demo(kiln, seconds=900):
    """Run the simulated kiln through a heat-up, as fast as the computer can, and print a table."""
    kiln.controller.set_setpoint(40.0)
    print(f"{'time':>6}  {'state':<12} {'temp C':>7}  {'heater %':>8}")
    tick_s = config.SENSOR_PERIOD_S
    ticks_per_row = round(30 / tick_s)
    started = False
    for i in range(round(seconds / tick_s)):
        now = i * tick_s
        kiln.tick(now)
        if now >= 3 and not started:
            kiln.devices.buttons["start"].press()
            started = True
        if i % ticks_per_row == 0:
            print(f"{now:5.0f}s  {kiln.controller.state.value:<12} "
                  f"{kiln.devices.kiln.temperature:7.2f}  {kiln.heater.percent:8.1f}")


if __name__ == "__main__":
    kiln = Kiln()
    if kiln.simulation:
        demo(kiln)
    else:
        try:
            kiln.run_forever()
        except KeyboardInterrupt:
            pass

