# Oven Controller

A PySide6 application for a kiln/oven controller, with a Windows simulator and a Raspberry Pi 5 hardware backend.

## Run locally

```powershell
python -m pip install -r requirements.txt
python main.py
```

## Interface and controls

- Displays a temperature graph with a dashed goal trace and a separately scaled heater duty-cycle axis. The graph starts with a 0-120 second time range, 0-65 C temperature range, and 0-100% output range.
- Provides process controls for target temperature (30-60 C, 45 C default) and hold duration (1-120 seconds, 60 seconds default), plus editable PID gains (Kp 0.10, Ki 0.006, Kd 0.05).
- Shows run stages, elapsed time, temperature/output, and door status. A stopped or interrupted run must be started over; safety faults clear when their underlying condition is resolved.
- Supports importing and exporting run records and process settings, viewing run events, and restoring process or PID defaults.
- Completed runs are saved as CSV in the configured data directory. Export run saves CSV by default or JSON when chosen, and Import run reads either.
- A run CSV has one row per sample, with the graph data in the leftmost columns (`elapsed_s`, `temperature_c`, `target_temperature_c`, `heater_output_percent`, `kp`, `ki`, `kd`). The run summary, event log and remaining run details follow in column groups to the right, each separated by an empty column and reading down from the first row.

## Hardware and control status

The control worker owns sensor reads, the controller, and actuator updates, keeping blocking hardware work off the Qt UI thread. The application always opens the Raspberry Pi hardware backend, so it must run on the Pi. Heater output is turned off on startup, stop, errors, and shutdown.

The physical E-Stop circuit removes heater and motor power on its own; the application only listens for it. When the E-Stop is pressed, the run ends with its outputs left idle, the window turns red with an emergency-stop banner, and every control is locked. Releasing the E-Stop clears emergency mode. Other safety faults clear when their underlying condition is resolved. On-screen Run, Stop, Open door and Close door send the same commands as the matching physical buttons.

## Development notes

Keep the project flat, with application modules at the repository root and tests in `tests/`. Install dependencies from `requirements.txt`. Review `TODO.md` for planned follow-up work and keep it current as work is identified or completed.
