"""Standalone one-page Raspberry Pi 5 oven pin test menu.

Uses the BCM assignments from All_pinnar.docx directly. No project modules or
configuration files are imported. Door motor tests are timed manual jogs.
"""
from __future__ import annotations

import time
from typing import Any

# BCM GPIO assignments from All_pinnar.docx.
DOOR_CLOSED_BCM = 16
DOOR_OPEN_BCM = 20
START_BCM = 13
OPEN_BUTTON_BCM = 19
CLOSE_BUTTON_BCM = 26
STOP_BUTTON_BCM = 21
ESTOP_BCM = 25
HEATER_BCM = 7
MOTOR_OPEN_BCM = 5
MOTOR_CLOSE_BCM = 6
TOWER_BCM = {
    "red": 17,  # Swapped with yellow relative to All_pinnar.docx (tested on the Pi)
    "yellow": 22,
    "green": 23,
    "orange": 24,  # Schematic net SIGNAL_WHITE
    "buzzer": 27,
}
TEMP_CS_BCM = 18
MAX_MOTOR_SECONDS = 10.0
RELAY_ACTIVE_LOW = True
# Red (GPIO17) is on optoisolator U56 with the heater and motor, which switch on HIGH.
TOWER_ACTIVE_HIGH = {"red"}


def main() -> int:
    try:
        from gpiozero import Button, DigitalInputDevice, DigitalOutputDevice
    except ImportError as exc:
        print(f"gpiozero is required: {exc}")
        return 1

    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    unavailable: dict[str, str] = {}
    probe: Any = None

    def add_input(name: str, pin: int, button: bool = False) -> None:
        try:
            if button:
                device = Button(pin, pull_up=True, bounce_time=0.05)
            else:
                # Door limit lines are externally biased and active low.
                device = DigitalInputDevice(pin, pull_up=None, active_state=False)
            inputs[name] = device
        except Exception as exc:
            unavailable[name] = f"BCM GPIO{pin}: {exc}"

    def add_output(name: str, pin: int, active_low: bool = False) -> None:
        try:
            outputs[name] = DigitalOutputDevice(
                pin, active_high=not active_low, initial_value=False
            )
        except Exception as exc:
            unavailable[name] = f"BCM GPIO{pin}: {exc}"

    add_input("Door closed limit", DOOR_CLOSED_BCM)
    add_input("Door open limit", DOOR_OPEN_BCM)
    add_input("Start button", START_BCM, button=True)
    add_input("Open button", OPEN_BUTTON_BCM, button=True)
    add_input("Close button", CLOSE_BUTTON_BCM, button=True)
    add_input("Stop button", STOP_BUTTON_BCM, button=True)
    add_input("E-STOP electrical input", ESTOP_BCM, button=True)

    add_output("Motor open", MOTOR_OPEN_BCM)
    add_output("Motor close", MOTOR_CLOSE_BCM)
    for name, pin in TOWER_BCM.items():
        add_output(name, pin, active_low=RELAY_ACTIVE_LOW and name not in TOWER_ACTIVE_HIGH)
    # Heater output is intentionally not claimed: the heater is disconnected,
    # and BCM GPIO7 is also SPI CE1 on the Pi header.

    def stop_motor() -> None:
        for name in ("Motor open", "Motor close"):
            if name in outputs:
                outputs[name].off()

    def show_inputs() -> None:
        print("\nInputs (buttons show gpiozero pressed state; E-STOP is not interpreted):")
        for name, device in inputs.items():
            if name.endswith("limit"):
                state = "ACTIVE" if device.is_active else "inactive"
            else:
                state = "LOW/pressed" if device.is_pressed else "HIGH/released"
            print(f"  {name}: {state}")
        for name, error in unavailable.items():
            print(f"  Unavailable {name}: {error}")

    def read_temperature() -> None:
        nonlocal probe
        try:
            if probe is None:
                import adafruit_max31865
                import board
                import busio
                import digitalio

                spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
                cs = digitalio.DigitalInOut(getattr(board, f"D{TEMP_CS_BCM}"))
                chip = adafruit_max31865.MAX31865(
                    spi, cs, rtd_nominal=100.0, ref_resistor=430.0, wires=3
                )
                probe = (chip, cs, spi)
            chip, _, _ = probe
            temperature = chip.temperature
            if any(chip.fault):
                chip.clear_faults()
                print("[SENSOR] MAX31865 reports a fault; check PT100/SPI wiring.")
            else:
                print(f"[OK] PT100: {temperature:.2f} °C")
        except Exception as exc:
            print(f"[SENSOR ERROR] {type(exc).__name__}: {exc}")

    def jog_motor(direction: str) -> None:
        output_name = "Motor open" if direction == "OPEN" else "Motor close"
        if output_name not in outputs:
            print(f"[ERROR] {output_name} output is unavailable: {unavailable.get(output_name, 'not initialized')}")
            return
        try:
            duration = float(input(f"Seconds to drive {direction.lower()} (0.1–{MAX_MOTOR_SECONDS:g}): "))
        except ValueError:
            print("Enter a number of seconds.")
            return
        if not 0.1 <= duration <= MAX_MOTOR_SECONDS:
            print(f"Choose a duration from 0.1 to {MAX_MOTOR_SECONDS:g} seconds.")
            return
        if input(f"Clear the door path, then type {direction} to start: ").strip().upper() != direction:
            print("Cancelled; motor remains off.")
            return
        stop_motor()
        started = time.monotonic()
        try:
            outputs[output_name].on()
            print("[MOVING] Manual jog ignores E-STOP, limit switches, and temperature. Ctrl+C stops early.")
            while time.monotonic() - started < duration:
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\n[STOP] Jog interrupted.")
        finally:
            stop_motor()
            print("[STOP] Motor outputs off.")

    def pulse(name: str) -> None:
        if name not in outputs:
            print(f"[ERROR] {name} output unavailable: {unavailable.get(name, 'not initialized')}")
            return
        try:
            outputs[name].on()
            print(f"[ON] {name} for 1 second.")
            time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n[STOP] Signal test interrupted.")
        finally:
            outputs[name].off()

    print("Raspberry Pi 5 oven pin test — standalone")
    while True:
        print("""
1. Show GPIO inputs
2. Read PT100 temperature
3. Open motor jog (manual override)
4. Close motor jog (manual override)
5. Stop motor outputs
6. Pulse red signal
7. Pulse yellow signal
8. Pulse green signal
9. Pulse orange signal (SIGNAL_WHITE)
10. Pulse buzzer
11. Heater status (disconnected; not driven)
0. Quit""")
        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        if choice == "1":
            show_inputs()
        elif choice == "2":
            read_temperature()
        elif choice == "3":
            jog_motor("OPEN")
        elif choice == "4":
            jog_motor("CLOSE")
        elif choice == "5":
            stop_motor()
            print("[STOP] Motor outputs off.")
        elif choice in {"6", "7", "8", "9", "10"}:
            name = {"6": "red", "7": "yellow", "8": "green", "9": "orange", "10": "buzzer"}[choice]
            pulse(name)
        elif choice == "11":
            print(f"[SKIPPED] Heater is disconnected; BCM GPIO{HEATER_BCM} was not initialized.")
        elif choice == "0":
            break
        else:
            print("Choose a listed number.")

    stop_motor()
    for device in outputs.values():
        device.off()
        device.close()
    for device in inputs.values():
        device.close()
    if probe is not None:
        _, cs, spi = probe
        cs.deinit()
        spi.deinit()
    print("GPIO outputs off; exited.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EOFError, KeyboardInterrupt):
        print("\nInterrupted.")
