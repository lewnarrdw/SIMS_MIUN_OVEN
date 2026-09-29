# Repository Guidelines

## Project Structure & Module Organization

This is a small Python project. Keep `main.py` as the application entry point and place supporting modules directly at the repository root when they are needed, for example `controller.py` or `config.py`. Avoid adding packages or subfolders unless the project genuinely outgrows this flat layout.

Review `TODO.md` when planning related work, and update it when follow-up work is identified or completed so it remains a useful project-level backlog.

Put automated tests in `tests/`; use matching names such as `tests/test_controller.py`. Add `assets/` only for files the application loads at runtime. Keep local settings and secrets in ignored files such as `.env`.

## Build, Test, and Development Commands

Install the declared desktop dependencies, then run the application:

```powershell
python -m pip install -r requirements.txt
python main.py
```

When dependencies are introduced, record them in `requirements.txt` (or the selected Python project configuration). Use a virtual environment: `python -m venv .venv`, then `.venv\Scripts\Activate.ps1`. If pytest is adopted, run the suite with `python -m pytest`.

## Coding Style & Naming Conventions

Use four-space indentation and modern Python. Name modules, functions, variables, and test files in `snake_case`; classes in `PascalCase`; and constants in `UPPER_SNAKE_CASE`. Add type hints to public functions and choose descriptive names, for example `target_temperature_c` rather than abbreviations.

Use a formatter and linter consistently once selected (Ruff is recommended). Do not commit `__pycache__/`, virtual environments, generated output, or secrets.

## Testing Guidelines

Add tests for every behavior change. Name tests `test_<behavior>()`; cover normal behavior, edge cases, and invalid input. Keep hardware or UI integration separate from core logic so it remains testable.

## Raspberry Pi 5 Compatibility

All generated application code must run on 64-bit Raspberry Pi OS on a Raspberry Pi 5 and remain usable on Windows through the simulator. Use ARM/Linux-compatible dependencies and avoid Windows-only APIs, x86-only binaries, and desktop-only paths.

Keep GPIO, I2C, SPI, PWM, and device imports behind a hardware interface selected at runtime. The Windows simulator must never import Pi-only packages; the Pi implementation must provide the same interface. Keep sensor reads, control loops, and blocking I/O off the Qt UI thread. Treat heater output as safety-critical: default to off on startup, stop, errors, and shutdown.

For graph behavior, annotate the maximum measured temperature only after a run completes; keep completion summaries concise and do not show a peak marker during an active or aborted run.

## Commit & Pull Request Guidelines

No Git history is available in this root to establish a convention. Use concise, imperative subjects such as `Add temperature controller`, and keep each commit focused. Pull requests should summarize the change, describe validation, link relevant issues, and include screenshots for UI changes.
