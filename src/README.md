# Oven Controller Simulator

A Windows-runnable PySide6 simulator for the Raspberry Pi 5 oven-controller UI. It is currently simulation-only: no GPIO, sensors, heater, or door switch are connected.

## Run locally

```powershell
python -m pip install -r requirements.txt
python main.py
```

## Current behavior

- Toolbar actions: Run, Stop, Reset, Export run data, Run events, Export settings, and Import settings. Run CSV files contain versioned measurement and event rows, including PID gain changes. Completed run data prompts to save before it is cleared; stopped runs remain available without a prompt.
- Live graph: temperature, dashed target temperature, and duty-cycle traces; it scrolls through a 120-second live window, then fits the full run after cool-down.
- Graph default/range limits: 0-120 seconds on X, 0-65 C for temperature (including overshoot headroom), and 0-100% for heater output.
- Target temperature: 30-60 C (45 C default); safe cool-down: 25 C; hold duration: 1-120 seconds (60 seconds default).
- Process stages: Ready, Warming up, Holding, and Cooling down. Completed runs can be exported as CSV before Reset or a new Run clears them; stopped runs remain available for manual export.
- Door status intentionally reports `Unknown` until the hardware layer exists.
- PID safeguards: bounded integral state, back-calculation anti-windup, filtered derivative-on-measurement, and a continuous +/-0.1 C hold band.

## Handoff for future development

Keep this project flat: use root-level modules and `tests/` only. Preserve Windows simulation support while adding a Pi-specific hardware implementation behind the same interface; never import Pi-only GPIO packages in the Windows simulator. Keep GPIO/I2C/SPI/PWM work and control loops off the Qt UI thread. Heater output must default to off on startup, Stop, errors, and shutdown.

Before changing behavior, inspect `AGENTS.md`, this README, and `TODO.md` for relevant follow-up work; keep the TODO list current as work is identified or completed. State the requested UI or control change, modify the smallest relevant code path, and run `python -m py_compile main.py` (or a syntax-only validation if the workspace cannot write `__pycache__`).
