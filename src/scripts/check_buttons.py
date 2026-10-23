"""Check the four front-panel buttons (start, stop, open, close) on the Pi.

Run on the Raspberry Pi:  python scripts/check_buttons.py
Press each button; the script prints press/release events and exits once all
four have been seen, or on Ctrl+C. Pins come from config.py and are wired the
same way as hardware/factory.py (internal pull-up, active low, 50 ms debounce).
"""
from __future__ import annotations

import sys
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from gpiozero import Button  # noqa: E402

BUTTON_PINS = {
    "start": config.BUTTON_START_PIN,
    "stop": config.BUTTON_STOP_PIN,
    "open": config.BUTTON_OPEN_PIN,
    "close": config.BUTTON_CLOSE_PIN,
}


def main() -> int:
    buttons: dict[str, Button] = {}
    seen: set[str] = set()
    all_seen = Event()

    def on_press(name: str) -> None:
        seen.add(name)
        remaining = sorted(set(BUTTON_PINS) - seen)
        print(f"{name.upper():6} pressed   (GPIO{BUTTON_PINS[name]})"
              f"  remaining: {', '.join(remaining) or 'none'}")
        if not remaining:
            print("All four buttons work.")
            all_seen.set()

    def on_release(name: str) -> None:
        print(f"{name.upper():6} released")

    try:
        for name, pin in BUTTON_PINS.items():
            button = Button(pin, pull_up=True, bounce_time=0.05)
            button.when_pressed = lambda n=name: on_press(n)
            button.when_released = lambda n=name: on_release(n)
            buttons[name] = button
            state = "PRESSED (check wiring?)" if button.is_pressed else "released"
            print(f"{name:6} on GPIO{pin}: {state}")

        print("\nPress each button. Ctrl+C to quit.\n")
        while not all_seen.wait(0.5):
            pass
    except KeyboardInterrupt:
        missing = sorted(set(BUTTON_PINS) - seen)
        print(f"\nNot pressed: {', '.join(missing)}" if missing else "\nAll pressed.")
        return 1 if missing else 0
    finally:
        for button in buttons.values():
            button.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
