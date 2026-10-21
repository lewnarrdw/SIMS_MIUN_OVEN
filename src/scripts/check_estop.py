"""Show the raw E-Stop sense input on the Pi with each internal pull resistor.

Run on the Raspberry Pi (with the kiln app closed):  python scripts/check_estop.py
For each pull direction, engage and release the E-Stop a few times and note
which level each position gives. Press Enter to move on to the next test.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from gpiozero import DigitalInputDevice  # noqa: E402

PIN = config.ESTOP_SENSE_PIN


def watch(pull_up: bool) -> None:
    name = "pull-up (to 3.3 V)" if pull_up else "pull-down (to GND)"
    device = DigitalInputDevice(PIN, pull_up=pull_up)
    stop = threading.Event()

    def report() -> None:
        last = None
        while not stop.wait(0.05):
            # is_active is inverted by gpiozero for pull-up inputs; report the pin level.
            level = "LOW" if device.is_active == pull_up else "HIGH"
            if level != last:
                print(f"  GPIO{PIN} = {level}")
                last = level

    print(f"\nInternal {name}: engage and release the E-Stop, then press Enter.")
    thread = threading.Thread(target=report, daemon=True)
    thread.start()
    try:
        input()
    finally:
        stop.set()
        thread.join()
        device.close()


def main() -> int:
    print(f"Config: ESTOP_SENSE_PIN={PIN}, ESTOP_ACTIVE_LOW={config.ESTOP_ACTIVE_LOW}")
    try:
        watch(pull_up=True)
        watch(pull_up=False)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
