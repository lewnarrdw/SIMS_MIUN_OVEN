# TODO

- Add a compact completed-run summary (peak temperature, time of peak, and difference from target) in the process status panel, in addition to the graph marker.
- Add a graph marker showing elapsed time to reach the target temperature (warm-up time).
- Add a graph marker showing elapsed time to reach the safe temperature (cool-down time).
- Add a graph marker at the end of the hold stage showing the lowest temperature measured during the hold.
- Update `tests/test_graph_display.py` and `tests/test_settings_controls.py`: they import names that no longer exist in `main.py` (for example `DEFAULT_KD`) and fail during collection.
- Set up the Git/GitHub workflow for creating scoped commits and pull requests, including a configured remote and authenticated GitHub CLI or equivalent.
