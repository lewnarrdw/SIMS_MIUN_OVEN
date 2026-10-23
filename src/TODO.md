# TODO

- Tune the PID on the real oven with `pid_tuning_monitor.py`: after each run, read the latest report in `data/pid_tuning_log.txt`, import `data/pid_suggested_settings.csv` (File > Import settings), and repeat. Remove the module, its application hook, and its tests once a report says the suggestion has settled. If a report suggests a new `FF_PERCENT_PER_DEGREE`, try it in `config.py`. The PID integral was fixed on 2026-10-22 (it previously applied only about a third of Ki at a 1 s loop), so gains tuned before then behave differently.

- Add a compact completed-run summary (peak temperature, time of peak, and difference from target) in the process status panel, in addition to the graph marker.
- Add a graph marker showing elapsed time to reach the target temperature (warm-up time).
- Add a graph marker showing elapsed time to reach the safe temperature (cool-down time).
- Add a graph marker at the end of the hold stage showing the lowest temperature measured during the hold.
- Update `tests/test_graph_display.py` and `tests/test_settings_controls.py`: they import names that no longer exist in `main.py` (for example `DEFAULT_KD`) and fail during collection.
- Set up the Git/GitHub workflow for creating scoped commits and pull requests, including a configured remote and authenticated GitHub CLI or equivalent.
- Review the temperature sampling rate, rolling median window, controller update rate, and graph/log sampling rate together; tune them for the MAX31865 and oven response.
- Confirm the warm-up heating check thresholds (`HEATING_CHECK_*` in `config.py`) on the real oven: note the delay before the temperature starts rising at full output and keep the window above it.
- Decide on a heater stuck-on check (temperature rising while the commanded output is 0 %, after a grace period), including whether it applies during cool-down.
- On the Pi, confirm the minimise button appears only when started from the desktop (taskbar present) and that the window can be restored; set `SHOW_MINIMIZE_BUTTON` in `config.py` if auto-detection is wrong.
- Fix two failing tests in `tests/test_emergency_stop.py`: the E-Stop lock expects the hold input to be disabled, and the fault-cause text expects "Fix it, then press Reset." for `sensor_timeout`.
