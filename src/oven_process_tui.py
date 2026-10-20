"""Standalone Raspberry Pi oven process runner based on diagram.png.

The heater stays inactive until an operator confirms START. The application
then closes the door, runs the configured PID controller, holds the target,
turns the heater off, cools below 26 C, and opens the door.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import config
from control.controller import Controller, State
from control.safety import SafetyMonitor
from hardware.factory import create_hardware

DOOR_CLOSED_STABLE_S = 0.25


def ask_float(prompt: str) -> float:
    while True:
        try:
            return float(input(prompt).strip())
        except ValueError:
            print("Enter a number.", flush=True)


def choose_process() -> tuple[float, float] | None:
    print("Oven process setup")
    print(f"Target range: {config.MIN_SETPOINT:g} to {config.MAX_SETPOINT:g} C")
    print(
        f"Configured hardware: heater BCM GPIO{config.HEATER_PIN}, "
        f"MAX31865 CS BCM GPIO{config.TEMP_CS_PIN}."
    )
    target = ask_float("Target temperature (C): ")
    hold_s = ask_float("Hold time (seconds): ")
    if not config.HOLD_DURATION_MIN_S <= hold_s <= config.HOLD_DURATION_MAX_S:
        print(
            f"Hold time must be {config.HOLD_DURATION_MIN_S:g} to "
            f"{config.HOLD_DURATION_MAX_S:g} seconds."
        )
        return None
    try:
        SafetyMonitor(config).validate_setpoint(target)
    except ValueError as exc:
        print(f"Invalid target: {exc}")
        return None
    print(f"Ready: heat to {target:g} C and hold for {hold_s:g} seconds.")
    print("Confirm the oven area is clear and the door can close safely.")
    if input("Type START to begin the process: ").strip() != "START":
        print("Cancelled. No GPIO hardware was initialized.")
        return None
    return target, hold_s


def run_process(target: float, hold_s: float) -> int:
    hardware = None
    controller = None
    try:
        hardware = create_hardware(config)
        # Explicitly lock the output off while the sensor and start conditions
        # are checked. Only the confirmed run below can unlock it.
        hardware.heater.emergency_off()

        process_settings = {
            name: value for name, value in vars(config).items()
            if not name.startswith("__")
        }
        process_settings["DOOR_SAFE_TEMPERATURE"] = 26.0
        process_config = SimpleNamespace(**process_settings)
        controller = Controller(hardware, process_config)
        print("Checking sensor and safety inputs; heater remains off.", flush=True)

        start_deadline = time.monotonic() + config.SENSOR_FAULT_TIMEOUT_S
        while True:
            now = time.monotonic()
            hardware.sensor.update(now)
            temperature = hardware.sensor.temperature()
            age_s = hardware.sensor.seconds_since_valid(now)
            if temperature is not None:
                break
            if now >= start_deadline:
                print("[BLOCKED] No valid temperature reading; heater remains off.")
                return 1
            time.sleep(config.SENSOR_PERIOD_S)

        if hardware.estop():
            print("[BLOCKED] E-STOP is active; heater remains off.")
            return 1
        if temperature >= config.MAX_TEMPERATURE:
            print("[BLOCKED] Temperature is at or above the safety limit; heater remains off.")
            return 1
        if hardware.switches.state().value == "error":
            print("[BLOCKED] Both door limits are active; heater remains off.")
            return 1
        if hardware.buttons["stop"].is_pressed:
            print("[BLOCKED] Stop input reads pressed before door movement; heater remains off.")
            return 1

        print("[DOOR] Closing door; heater remains locked off.", flush=True)
        now = time.monotonic()
        close_deadline = now + config.DOOR_TIMEOUT_S
        closed_since = None
        if hardware.switches.state().value != "closed":
            hardware.motor.close_door(now)

        while True:
            now = time.monotonic()
            hardware.sensor.update(now)
            temperature = hardware.sensor.temperature()
            age_s = hardware.sensor.seconds_since_valid(now)
            hardware.motor.update(now)
            door_state = hardware.switches.state().value

            if hardware.estop():
                hardware.motor.stop()
                print("[BLOCKED] E-STOP activated while closing; heater remains off.")
                return 1
            if age_s >= config.SENSOR_FAULT_TIMEOUT_S or temperature is None:
                hardware.motor.stop()
                print("[BLOCKED] Temperature sensor failed while closing; heater remains off.")
                return 1
            if temperature >= config.MAX_TEMPERATURE:
                hardware.motor.stop()
                print("[BLOCKED] Over-temperature while closing; heater remains off.")
                return 1
            if door_state == "error":
                hardware.motor.stop()
                print("[BLOCKED] Both door limits are active; heater remains off.")
                return 1
            if hardware.buttons["stop"].is_pressed:
                hardware.motor.stop()
                print("[STOP] Stop button pressed while closing; heater remains off.")
                return 1
            if hardware.motor.fault_reason:
                print(
                    f"[DOOR ERROR] Motor fault: {hardware.motor.fault_reason}; "
                    "heater remains off.",
                    flush=True,
                )
                return 1

            if door_state == "closed":
                closed_since = now if closed_since is None else closed_since
                if now - closed_since >= DOOR_CLOSED_STABLE_S:
                    hardware.motor.stop()
                    print("[DOOR] Closed sensor confirmed; door is stable.", flush=True)
                    break
            else:
                closed_since = None
                if now >= close_deadline:
                    hardware.motor.stop()
                    print("[DOOR ERROR] Closed sensor was not reached before timeout; heater remains off.")
                    return 1
                if hardware.motor.state.value == "idle":
                    hardware.motor.close_door(now)

            time.sleep(min(config.SENSOR_PERIOD_S, 0.05))

        now = time.monotonic()
        controller.start(
            now,
            target,
            hold_s,
            (config.PID_KP, config.PID_KI, config.PID_KD),
            temperature,
        )
        # This is the only normal path that unlocks the heater: START was
        # confirmed, inputs are safe, and the closed-door sensor is stable.
        hardware.heater.unlock()
        print("[STARTED] Door confirmed closed; PID heating may begin.", flush=True)

        next_sample = now
        next_control = now
        last_report = 0.0
        period = config.SENSOR_PERIOD_S
        while controller.state not in (
            State.COMPLETE, State.FAULT, State.E_STOP, State.ABORTED
        ):
            now = time.monotonic()
            hardware.sensor.update(now)
            temperature = hardware.sensor.temperature()
            age_s = hardware.sensor.seconds_since_valid(now)
            hardware.motor.update(now)

            if hardware.buttons["stop"].is_pressed:
                controller.stop(now)
                print("[STOP] Stop button pressed; start a new run to continue.", flush=True)

            before = controller.state
            control_due = now >= next_control
            controller.step(now, temperature, age_s, hardware.estop(), control_due)
            if control_due:
                next_control = now + config.LOOP_PERIOD_S

            if controller.state in (State.HEATING, State.HOLDING) and not hardware.heater.locked:
                hardware.heater.update(now)
            else:
                hardware.heater.off()

            if temperature is not None and now - last_report >= 1.0:
                print(
                    f"[{controller.state.value.upper()}] {temperature:.2f} C | "
                    f"target {target:.2f} C | heater {hardware.heater.percent:.1f}% | "
                    f"{controller.status_text}",
                    flush=True,
                )
                last_report = now

            if controller.state in (State.FAULT, State.E_STOP):
                print(f"[FAULT] {controller.status_text}; heater disabled.", flush=True)
                return 1
            if controller.state == State.COMPLETE:
                print("[COMPLETE] Below 26 C; door opened and heater is off.", flush=True)
                return 0
            if controller.state == State.ABORTED:
                print("[STOPPED] Run interrupted; start a new run to continue.", flush=True)
                return 1
            if before != controller.state:
                print(f"[STAGE] {controller.status_text}", flush=True)
            next_sample += period
            time.sleep(max(0.0, next_sample - time.monotonic()))
            if next_sample < time.monotonic() - period:
                next_sample = time.monotonic()
        return 0
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted; heater disabled. Keep the door closed until cool.", flush=True)
        return 130
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}; heater disabled.", flush=True)
        return 1
    finally:
        if hardware is not None:
            hardware.heater.emergency_off()
            hardware.motor.stop()
            hardware.close()
            print("[SAFE] Heater and motor outputs off; GPIO resources released.", flush=True)


def main() -> int:
    try:
        settings = choose_process()
        if settings is None:
            return 0
        return run_process(*settings)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled before start; heater was not initialized.", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
