# Oven Controller Simulator

A Windows-runnable PySide6 interface for the planned Raspberry Pi 5 oven controller. The current application is a UI prototype: it does not connect to GPIO, sensors, a heater, or a door switch, and its process and settings actions are not implemented.

## Run locally

```powershell
python -m pip install -r requirements.txt
python main.py
```

## Current interface

- Displays a temperature graph with a dashed goal trace and a separately scaled heater duty-cycle axis. The graph starts with a 0-120 second time range, 0-65 C temperature range, and 0-100% output range.
- Provides process controls for target temperature (30-60 C, 45 C default) and hold duration (1-120 seconds, 60 seconds default), plus editable PID gains (Kp 0.10, Ki 0.006, Kd 0.05).
- Shows Ready, Warming up, Holding, and Cooling down stages, along with elapsed time, temperature/output, and door status. Door status currently reads Unknown.
- Toolbar actions and restore-default buttons are visible, but their callbacks are placeholders. They do not start or stop a run, reset data, or import/export files.

## Hardware and control status

`main.py` contains an unfinished background-loop skeleton. Hardware startup is disabled (`HARDWARE_DRIVER_READY = False`); no temperature-reading or heater-output implementation is connected, and the GUI does not yet consume controller state. Keep Pi-only device imports behind a platform-specific hardware interface so the Windows simulator can run without Raspberry Pi packages. Keep blocking device I/O and control work off the Qt UI thread, and ensure heater output defaults to off on startup, stop, errors, and shutdown before enabling hardware operation.

## Development notes

Keep the project flat, with application modules at the repository root and tests in `tests/`. Install dependencies from `requirements.txt`. Review `TODO.md` for planned follow-up work and keep it current as work is identified or completed.
