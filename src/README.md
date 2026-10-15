# Raspberry Pi Oven Controller

A PySide6 application for a kiln/oven controller running on Raspberry Pi 5.

## Run on Raspberry Pi

```sh
python -m pip install -r requirements.txt
python src/main.py
```

## Interface and controls

- Displays a temperature graph with a dashed goal trace and a separately scaled heater duty-cycle axis. The graph starts with a 0-120 second time range, 0-65 C temperature range, and 0-100% output range.
- Provides process controls for target temperature (30-60 C, 45 C default) and hold duration (1-120 seconds, 60 seconds default), plus editable PID gains (Kp 0.10, Ki 0.006, Kd 0.05).
- Shows run stages, elapsed time, temperature/output, and door status. Runs can be paused and resumed; safety faults require a reset before another run.
- Supports importing and exporting run records and process settings, viewing run events, and restoring process or PID defaults.
- Completed runs are saved as JSON in the configured data directory.

## Hardware and control status

The control worker owns sensor reads, the controller, and actuator updates, keeping blocking hardware work off the Qt UI thread. The hardware factory selects the Raspberry Pi backend. Heater output is turned off on startup, pause/stop, errors, and shutdown.

## Development notes

Keep the project flat, with application modules at the repository root and tests in `tests/`. Install dependencies from `requirements.txt`. Review `TODO.md` for planned follow-up work and keep it current as work is identified or completed.
