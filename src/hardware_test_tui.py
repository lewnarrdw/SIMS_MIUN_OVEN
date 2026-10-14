"""Standalone, numbered Raspberry Pi GPIO diagnostic menu for the oven.

Run with ``python hardware_test_tui.py`` on the Pi. GPIO and SPI packages are
imported only when the relevant hardware is initialized. The heater is never
energized by this diagnostic tool.
"""
from __future__ import annotations

import time
from typing import Any

import config

MANUAL_OVERRIDE_MAX_S = 10.0


def line(message: str = "") -> None:
    print(message, flush=True)


def pause() -> None:
    input("\nPress Enter to continue...")


class PiDiagnostics:
    """Owns GPIO devices and provides bounded, safety-checked test actions."""

    def __init__(self) -> None:
        try:
            from gpiozero import Button, DigitalInputDevice, DigitalOutputDevice
        except ImportError as exc:
            raise RuntimeError(
                "gpiozero is unavailable. Run this on Raspberry Pi OS and install gpiozero."
            ) from exc

        self.devices: list[Any] = []
        self.inputs: dict[str, Any] = {}
        self.outputs: dict[str, Any] = {}
        self.probe: Any = None
        self.sensor_error: str | None = None

        def button(name: str, pin: int) -> Any:
            try:
                device = Button(pin, pull_up=True, bounce_time=0.05)
            except Exception as exc:
                raise RuntimeError(f"Cannot claim {name} on BCM GPIO{pin}: {exc}") from exc
            self.devices.append(device)
            self.inputs[name] = device
            return device

        def limit(name: str, pin: int) -> Any:
            # Active-low limit input: pull the idle line high and debounce the
            # switch. Omitting the pull-up can leave an open input floating.
            try:
                device = Button(pin, pull_up=True, bounce_time=0.05)
            except Exception as exc:
                raise RuntimeError(f"Cannot claim {name} on BCM GPIO{pin}: {exc}") from exc
            self.devices.append(device)
            self.inputs[name] = device
            return device

        def output(name: str, pin: int, active_high: bool = True) -> Any:
            try:
                device = DigitalOutputDevice(pin, active_high=active_high, initial_value=False)
            except Exception as exc:
                raise RuntimeError(f"Cannot claim {name} on BCM GPIO{pin}: {exc}") from exc
            self.devices.append(device)
            self.outputs[name] = device
            return device

        try:
            limit("Door open limit", config.DOOR_LIMIT_OPEN_PIN)
            limit("Door closed limit", config.DOOR_LIMIT_CLOSED_PIN)
            for name, pin in {
                "Start button": config.BUTTON_START_PIN,
                "Open button": config.BUTTON_OPEN_PIN,
                "Close button": config.BUTTON_CLOSE_PIN,
                "Stop button": config.BUTTON_STOP_PIN,
            }.items():
                button(name, pin)
            # Keep the E-STOP path free of Button's software debounce delay.
            try:
                estop_input = DigitalInputDevice(config.ESTOP_SENSE_PIN, pull_up=True)
            except Exception as exc:
                raise RuntimeError(
                    f"Cannot claim E-STOP on BCM GPIO{config.ESTOP_SENSE_PIN}: {exc}"
                ) from exc
            self.devices.append(estop_input)
            self.inputs["E-STOP circuit"] = estop_input

            active_high = not config.RELAY_ACTIVE_LOW
            output("Motor IN1 (open)", config.MOTOR_IN1_PIN)
            output("Motor IN2 (close)", config.MOTOR_IN2_PIN)
            if config.MOTOR_ENABLE_PIN is not None:
                output("Motor enable", config.MOTOR_ENABLE_PIN)
            for name, pin in config.TOWER_PINS.items():
                output(f"Tower {name}", pin, active_high)
        except Exception:
            self.close()
            raise

    def estop_active(self) -> bool:
        # DigitalInputDevice.is_active is LOW with its pull-up enabled.
        low = self.inputs["E-STOP circuit"].is_active
        return not low if config.ESTOP_NORMALLY_CLOSED else low

    def door_state(self) -> str:
        opened = self.inputs["Door open limit"].is_active
        closed = self.inputs["Door closed limit"].is_active
        if opened and closed:
            return "ERROR (both limit switches active)"
        if opened:
            return "OPEN"
        if closed:
            return "CLOSED"
        return "BETWEEN limits"

    def read_temperature(self) -> float | None:
        if self.probe is None and self.sensor_error is None:
            try:
                from hardware.sensors import Max31865Probe

                self.probe = Max31865Probe(config)
            except Exception as exc:
                self.sensor_error = f"{type(exc).__name__}: {exc}"
        if self.probe is None:
            line(f"[SENSOR ERROR] Could not initialize MAX31865: {self.sensor_error}")
            return None
        value = self.probe.read_raw()
        if value is None:
            line("[SENSOR ERROR] No valid PT100 reading. Check RTD wiring, SPI, and MAX31865 faults.")
            return None
        line(f"[OK] PT100 temperature: {value:.2f} °C")
        return value

    def input_report(self) -> None:
        line("GPIO input status (electrical state may differ from mechanical state):")
        for name, device in self.inputs.items():
            if name == "E-STOP circuit":
                level = "LOW" if device.is_active else "HIGH"
                value = f"GPIO {level} (BCM GPIO{config.ESTOP_SENSE_PIN})"
            elif name.endswith("limit"):
                value = "ACTIVE" if device.is_active else "inactive"
            else:
                value = "pressed/closed" if device.is_pressed else "released/open"
            line(f"  {name}: {value}")
        expected_healthy_level = "LOW" if config.ESTOP_NORMALLY_CLOSED else "HIGH"
        line(f"  E-STOP polarity configured as {'normally closed' if config.ESTOP_NORMALLY_CLOSED else 'normally open'}; healthy circuit is expected GPIO {expected_healthy_level}")
        line(f"  Interpreted E-STOP: {'ACTIVE — outputs must stop' if self.estop_active() else 'released'}")
        line(f"  Door position: {self.door_state()}")

    def stop_motor(self) -> None:
        self.outputs["Motor IN1 (open)"].off()
        self.outputs["Motor IN2 (close)"].off()
        if "Motor enable" in self.outputs:
            self.outputs["Motor enable"].off()

    def move_door(self, opening: bool, ignore_temperature: bool = False) -> None:
        label = "OPEN" if opening else "CLOSE"
        if self.estop_active():
            line("[BLOCKED] E-STOP circuit is active. Release/reset it before moving the door.")
            return
        state = self.door_state()
        if state.startswith("ERROR"):
            line("[BLOCKED] Both door limit switches are active. Inspect switches and wiring.")
            return
        goal = "OPEN" if opening else "CLOSED"
        if state == goal:
            line(f"[OK] Door already reports {goal}; motor remains off.")
            return
        if opening and not ignore_temperature:
            temperature = self.read_temperature()
            if temperature is None or temperature >= config.DOOR_SAFE_TEMPERATURE:
                line(f"[SAFETY] Opening normally requires a valid reading below {config.DOOR_SAFE_TEMPERATURE:g} °C.")
                ack = input("If you independently verified the oven is cold, type COLD to continue: ").strip()
                if ack != "COLD":
                    line("[BLOCKED] No cold-door acknowledgment. Motor remains off.")
                    return
        elif opening:
            line("[OVERRIDE] Temperature reading and temperature interlock are bypassed for this motor test.")
            line("E-STOP, door limits, and movement timeout remain active.")
        line(f"[ACTION] Driving door {label.lower()} (max {config.DOOR_TIMEOUT_S:g} s). Press Ctrl+C to stop.")
        self.stop_motor()
        selected = self.outputs["Motor IN1 (open)" if opening else "Motor IN2 (close)"]
        started = time.monotonic()
        try:
            selected.on()
            if "Motor enable" in self.outputs:
                self.outputs["Motor enable"].on()
            while time.monotonic() - started < config.DOOR_TIMEOUT_S:
                if self.estop_active():
                    line("[E-STOP] Circuit activated during movement; stopping motor immediately.")
                    return
                state = self.door_state()
                if state.startswith("ERROR"):
                    line("[FAULT] Both door limits active; stopping motor.")
                    return
                if state == goal:
                    line(f"[OK] Door reached {goal} limit in {time.monotonic() - started:.1f} s.")
                    return
                line(f"  Moving... door={state}; elapsed={time.monotonic() - started:.1f}s",)
                time.sleep(0.01)
            line(f"[TIMEOUT] Door did not reach {goal}; check motor, limit switch, and wiring.")
        except KeyboardInterrupt:
            line("[STOP] User interrupted door motion.")
        finally:
            self.stop_motor()
            line("[SAFE] Motor outputs are off.")

    def manual_override_move(self, opening: bool) -> None:
        """Run a timed manual motor test without reading any input interlocks."""
        direction = "OPEN" if opening else "CLOSE"
        line(f"[MANUAL OVERRIDE] {direction}: E-STOP, temperature, and limit inputs are ignored.")
        line(f"Motor output will stop after at most {MANUAL_OVERRIDE_MAX_S:g} seconds.")
        expected = f"BYPASS {direction}"
        if input(f"Clear the door path, then type {expected} to move: ").strip().upper() != expected:
            line("[CANCELLED] Confirmation did not match; motor remains off.")
            return
        selected = self.outputs["Motor IN1 (open)" if opening else "Motor IN2 (close)"]
        self.stop_motor()
        started = time.monotonic()
        try:
            selected.on()
            if "Motor enable" in self.outputs:
                self.outputs["Motor enable"].on()
            line("[MOVING] Watch the door continuously. Press Ctrl+C to stop early.")
            while time.monotonic() - started < MANUAL_OVERRIDE_MAX_S:
                time.sleep(0.1)
            line("[STOP] Manual override time limit reached.")
        except KeyboardInterrupt:
            line("[STOP] Manual override interrupted by operator.")
        finally:
            self.stop_motor()
            line("[SAFE] Motor outputs are off.")

    def test_signal(self, name: str) -> None:
        output = self.outputs[f"Tower {name}"]
        line(f"[ACTION] Pulsing {name} signal for 1 second. Confirm the indicator/buzzer physically.")
        try:
            output.on()
            time.sleep(1.0)
        except KeyboardInterrupt:
            line("[STOP] Signal test interrupted.")
        finally:
            output.off()
            line(f"[SAFE] {name} signal off.")

    def heater_off_check(self) -> None:
        line("[SKIPPED] Heater is not connected, so BCM GPIO7 is not initialized or tested.")
        line("The heater output is never energized by this diagnostic tool.")

    def close(self) -> None:
        for name in ("Motor IN1 (open)", "Motor IN2 (close)", "Motor enable"):
            device = self.outputs.get(name)
            if device is not None:
                try:
                    device.off()
                except Exception:
                    pass
        for device in reversed(self.devices):
            try:
                device.close()
            except Exception:
                pass
        if self.probe is not None:
            try:
                self.probe.close()
            except Exception:
                pass


def show_page_one(hw: PiDiagnostics) -> str:
    while True:
        line("\n=== OVEN HARDWARE TEST — PAGE 1: INPUTS / SENSOR ===")
        line("1. Show door switches, buttons, and E-STOP status")
        line("2. Read PT100 temperature once")
        line("3. Monitor E-STOP (Ctrl+C to stop)")
        line("4. Go to outputs / door page")
        line("0. Quit")
        choice = input("> ").strip()
        if choice == "1":
            hw.input_report()
            pause()
        elif choice == "2":
            hw.read_temperature()
            pause()
        elif choice == "3":
            line("Monitoring E-STOP. Press Ctrl+C to return.")
            try:
                previous_state: bool | None = None
                while True:
                    active = hw.estop_active()
                    if active != previous_state:
                        line(f"  {'ACTIVE' if active else 'released'}")
                        previous_state = active
                    time.sleep(0.001)
            except KeyboardInterrupt:
                line("[STOP] E-STOP monitor ended.")
        elif choice == "4":
            return "outputs"
        elif choice == "0":
            return "quit"
        else:
            line("Choose one of the listed numbers.")


def show_page_two(hw: PiDiagnostics) -> str:
    while True:
        line("\n=== OVEN HARDWARE TEST — PAGE 2: OUTPUTS / DOOR ===")
        line("1. Open door (checks E-STOP, limits, and cold condition)")
        line("2. Close door (checks E-STOP and limits)")
        line("3. Stop door motor now")
        line("4. Pulse red signal")
        line("5. Pulse yellow signal")
        line("6. Pulse green signal")
        line("7. Pulse white signal")
        line("8. Pulse buzzer")
        line("9. Heater test status (skipped; heater is not connected)")
        line("10. Go to inputs / sensor page")
        line("11. Test door motor without a temperature sensor (E-STOP/limits/timeout remain active)")
        line("12. Manual door override (bypasses E-STOP, temperature, and limit inputs)")
        line("0. Quit")
        choice = input("> ").strip()
        if choice == "1":
            hw.move_door(opening=True)
            pause()
        elif choice == "2":
            hw.move_door(opening=False)
            pause()
        elif choice == "3":
            hw.stop_motor()
            line("[SAFE] Both motor directions and enable set OFF.")
            pause()
        elif choice in {"4", "5", "6", "7", "8"}:
            signal = {"4": "red", "5": "yellow", "6": "green", "7": "white", "8": "buzzer"}[choice]
            hw.test_signal(signal)
            pause()
        elif choice == "9":
            hw.heater_off_check()
            pause()
        elif choice == "10":
            return "inputs"
        elif choice == "11":
            line("This test skips the temperature check. Keep clear of the moving door.")
            direction = input("Type OPEN or CLOSE to run that motor direction: ").strip().upper()
            if direction in {"OPEN", "CLOSE"}:
                hw.move_door(opening=direction == "OPEN", ignore_temperature=True)
            else:
                line("[CANCELLED] Type OPEN or CLOSE exactly; motor remains off.")
            pause()
        elif choice == "12":
            direction = input("Type OPEN or CLOSE for manual override: ").strip().upper()
            if direction in {"OPEN", "CLOSE"}:
                hw.manual_override_move(opening=direction == "OPEN")
            else:
                line("[CANCELLED] Type OPEN or CLOSE exactly; motor remains off.")
            pause()
        elif choice == "0":
            return "quit"
        else:
            line("Choose one of the listed numbers.")


def main() -> int:
    line("Raspberry Pi 5 oven hardware diagnostic")
    line("DB25 / BCM mapping is from All_pinnar.docx and config.py.")
    line("Heater tests are OFF-only. Door motion can be stopped with Ctrl+C.")
    try:
        hardware = PiDiagnostics()
    except Exception as exc:
        line(f"[STARTUP ERROR] {type(exc).__name__}: {exc}")
        return 1
    try:
        page = "inputs"
        while page != "quit":
            page = show_page_one(hardware) if page == "inputs" else show_page_two(hardware)
    except (EOFError, KeyboardInterrupt):
        line("\n[STOP] Exiting diagnostic application.")
    finally:
        hardware.close()
        line("[SAFE] Heater and motor outputs are off; GPIO resources released.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
