# Smart Mini Kiln controller (experimental)

This branch contains the Raspberry Pi 5 kiln controller. The application code is under `src/`.

## Start

Install Raspberry Pi OS 64-bit GPIO and SPI support, enable SPI, confirm all pin numbers and relay polarity in `src/config.py`, then install `src/requirements.txt` in a virtual environment and run `python src/main.py` from the repository root. The MAX31865, PT100 wiring, limit switches, E-stop circuit, relay polarity, and motor directions must be confirmed against the actual hardware before energizing the heater.

## Runtime layout

`src/main.py` composes the unchanged interface with a thin Qt view adapter. `src/application.py` owns the worker thread, hardware lifecycle, command queue, and thread-safe sample history. The worker samples at 15 Hz, runs the control state machine off the UI thread, and updates hardware outputs. The Qt timer snapshots history and updates the graph on the UI thread. Terminal runs are saved automatically under `src/data/` and can be exported as versioned JSON records containing settings, outcome, saved progress, summary markers, and elapsed-time samples; each sample also records target, heater output, and active PID gains. Settings presets remain CSV files.

Hardware inputs are imported only when `create_hardware()` runs. The heater is driven off before the worker starts and locked off on faults, emergency stop, and shutdown. The software E-stop path is not a substitute for a correctly wired hardware interlock or independent thermal cutoff. Confirm the configuration and safety circuits with the hardware team before operating a kiln.
