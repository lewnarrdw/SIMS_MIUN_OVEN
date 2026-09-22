# Oven Controller Simulator

A Windows-runnable PySide6 simulator for the Raspberry Pi 5 oven-controller UI. It is currently simulation-only: no GPIO, sensors, heater, or door switch are connected.

## Run locally

```powershell
python -m pip install -r requirements.txt
python main.py
```

## Current behavior

- Toolbar actions: Run, Stop, Reset, Import, and Export.
- Live graph: temperature, full-width dashed target temperature while running, and duty-cycle traces.
- Graph default/range limits: 0-120 seconds on X, 0-80 C for temperature, and 0-100% for heater output.
- Target temperature: 30-60 C; safe cool-down: 25 C; hold duration: 1-120 seconds.
- Connected process stages: Initialised, Warm-up, Hold, and Cool-down. Nodes turn green only after their respective goal completes.
- Door status intentionally reports `Unknown` until the hardware layer exists.
- PID safeguards: bounded integral state, back-calculation anti-windup, filtered derivative-on-measurement, and a continuous +/-0.1 C hold band.

## Handoff for future development

Keep this project flat: use root-level modules and `tests/` only. Preserve Windows simulation support while adding a Pi-specific hardware implementation behind the same interface; never import Pi-only GPIO packages in the Windows simulator. Keep GPIO/I2C/SPI/PWM work and control loops off the Qt UI thread. Heater output must default to off on startup, Stop, errors, and shutdown.

Before changing behavior, inspect `AGENTS.md` and this README. State the requested UI or control change, modify the smallest relevant code path, and run `python -m py_compile main.py` (or a syntax-only validation if the workspace cannot write `__pycache__`).
