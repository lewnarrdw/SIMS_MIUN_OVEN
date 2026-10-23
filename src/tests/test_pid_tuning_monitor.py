"""Tests for the temporary PID tuning assistant and the PID integral."""

import csv
import json
import math
import random
import tempfile
import time
import unittest
from pathlib import Path

import config
import pid_tuning_monitor as monitor
from control.pid import PID
from pid_tuning_monitor import SimulatedOven, ThermalModel

TRUE_OVEN = ThermalModel(gain_c_per_percent=0.6, time_constant_s=600.0, heater_lag_s=60.0,
                         dead_time_s=8.0, ambient_c=21.0)


def synthetic_run(oven=TRUE_OVEN, gains=None, target=40.0, hold_s=60.0, noise_c=0.02, seed=1):
    """A run record from the real PID and stage rules on a known oven, sampled at 5 Hz."""
    gains = gains or {"kp": 10.0, "ki": 0.1, "kd": 1.0}
    rng = random.Random(seed)
    plant = SimulatedOven(oven, oven.ambient_c)
    pid = PID(gains["kp"], gains["ki"], gains["kd"])
    feedforward = config.FF_PERCENT_PER_DEGREE * (target - config.ROOM_TEMPERATURE)
    samples, events = [], [{"type": "stage_started", "description": "Warm up started", "elapsed_s": 0.0}]
    temperature = measured = oven.ambient_c
    output, stable_since, hold_started, cooling_started, door_opened = 0.0, None, None, None, None
    peak = None
    for second in range(0, 3000):
        if cooling_started is None:
            output = pid.update(target, measured, 1.0, feedforward)
        else:
            output = 0.0
        for tick in range(5):
            elapsed = second + tick / 5
            samples.append({"elapsed_s": elapsed, "temperature_c": round(measured, 3),
                            "target_temperature_c": target, "heater_output_percent": output, **gains})
        temperature = plant.step(output)
        measured = temperature + rng.gauss(0.0, noise_c)
        if cooling_started is None and temperature >= target:
            peak = temperature if peak is None else max(peak, temperature)
        if hold_started is None:
            if abs(measured - target) <= config.STABLE_TOLERANCE:
                stable_since = second if stable_since is None else stable_since
                if second - stable_since >= config.STABLE_TIME_S:
                    hold_started = float(second)
                    events.append({"type": "stage_started", "description": "Hold started", "elapsed_s": hold_started})
            else:
                stable_since = None
        elif cooling_started is None and second - hold_started >= hold_s:
            cooling_started = float(second)
            events.append({"type": "stage_started", "description": "Cool down started", "elapsed_s": cooling_started})
        elif cooling_started is not None and temperature <= config.DOOR_SAFE_TEMPERATURE:
            door_opened = float(second)
            events.append({"type": "door_opened", "description": "Door open", "elapsed_s": door_opened})
            break
    return {
        "schema_version": 1, "started_at": "2026-10-22T10:00:00+02:00",
        "settings": {"target_temperature_c": target, "hold_duration_s": hold_s, **gains},
        "result": {"state": "complete" if door_opened else "aborted", "status": "", "duration_s": samples[-1]["elapsed_s"],
                   "hold_reached_s": hold_started, "hold_elapsed_s": hold_s if cooling_started else 0.0},
        "progress": {"stage": 4, "fraction": 1.0, "detail": ""}, "events": events,
        "metrics": {"peak": None if peak is None else {"temperature_c": peak, "elapsed_s": 0.0}, "hold_minimum": None},
        "samples": samples,
    }


class PidIntegralTests(unittest.TestCase):
    def test_unsaturated_integral_accumulates_ki_error_dt(self) -> None:
        pid = PID(0.0, 0.5, 0.0)
        pid.update(40.0, 38.0, 1.0)
        self.assertAlmostEqual(pid.integral, 1.0)
        pid.update(40.0, 38.0, 1.0)
        self.assertAlmostEqual(pid.integral, 2.0)

    def test_saturation_does_not_wind_up(self) -> None:
        pid = PID(0.0, 5.0, 0.0)
        for _ in range(100):
            output = pid.update(45.0, 20.0, 1.0, feedforward=50.0)
        self.assertEqual(output, 100.0)
        self.assertAlmostEqual(pid.integral, 50.0)
        # The first sample above target pulls the output off the limit at once.
        self.assertLess(pid.update(45.0, 46.0, 1.0, feedforward=50.0), 100.0)

    def test_output_stays_within_limits(self) -> None:
        pid = PID(10.0, 1.0, 1.0)
        for measured in (20.0, 60.0, 20.0, -5.0):
            self.assertTrue(0.0 <= pid.update(40.0, measured, 1.0) <= 100.0)


class MeasureRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.record = synthetic_run()

    def test_measures_warm_up_overshoot_and_hold(self) -> None:
        metrics = monitor.measure_run(self.record)
        self.assertTrue(metrics.analysed)
        self.assertAlmostEqual(metrics.start_temperature_c, 21.0, delta=0.1)
        self.assertIsNotNone(metrics.time_to_target_s)
        self.assertGreater(metrics.overshoot_c, 0.0)
        self.assertEqual(metrics.time_to_stable_s, self.record["result"]["hold_reached_s"])
        self.assertIsNotNone(metrics.hold_rms_error_c)

    def test_measures_heat_after_switch_off(self) -> None:
        metrics = monitor.measure_run(self.record)
        # At the start of cooling the heater only ran at holding power, so the coast is small.
        self.assertGreaterEqual(metrics.coast_rise_c, 0.0)
        self.assertLess(metrics.coast_rise_c, 0.5)
        self.assertGreaterEqual(metrics.coast_s, 0.0)

    def test_heater_lag_causes_coasting_after_full_power(self) -> None:
        rise, seconds = monitor.coast_after_full_power(TRUE_OVEN, 40.0)
        self.assertGreater(rise, 1.0)
        self.assertGreater(seconds, 30.0)
        no_lag = ThermalModel(0.6, 600.0, 0.0, 0.0, 21.0)
        self.assertLess(monitor.coast_after_full_power(no_lag, 40.0)[0], 0.05)

    def test_fit_tolerates_a_noisy_sensor(self) -> None:
        record = synthetic_run(noise_c=0.1, seed=7)
        start, end = monitor._analysis_window(record)
        model = monitor.fit_model(*monitor.resample(record["samples"], start, end))
        self.assertIsNotNone(model)
        self.assertAlmostEqual(model.gain_c_per_percent, TRUE_OVEN.gain_c_per_percent, delta=0.2)
        self.assertAlmostEqual(model.total_lag_s, TRUE_OVEN.total_lag_s, delta=45.0)

    def test_empty_run_is_not_analysed(self) -> None:
        self.assertFalse(monitor.measure_run({"samples": [], "settings": {}}).analysed)


class FitModelTests(unittest.TestCase):
    def test_recovers_the_oven_from_a_run(self) -> None:
        record = synthetic_run()
        start, end = monitor._analysis_window(record)
        model = monitor.fit_model(*monitor.resample(record["samples"], start, end))
        self.assertIsNotNone(model)
        self.assertLess(model.fit_rmse_c, monitor.GOOD_FIT_RMSE_C)
        self.assertAlmostEqual(model.gain_c_per_percent, TRUE_OVEN.gain_c_per_percent, delta=0.15)
        self.assertAlmostEqual(model.total_lag_s, TRUE_OVEN.total_lag_s, delta=30.0)

    def test_flat_data_gives_no_model(self) -> None:
        times = monitor.np.arange(0, 300.0)
        self.assertIsNone(monitor.fit_model(times, times * 0 + 25.0, times * 0 + 10.0))

    def test_analysis_stops_when_the_door_opens(self) -> None:
        record = synthetic_run()
        _start, end = monitor._analysis_window(record)
        door = next(e["elapsed_s"] for e in record["events"] if e["type"] == "door_opened")
        self.assertEqual(end, door)


class TuningTests(unittest.TestCase):
    def test_suggestion_beats_poor_gains_in_simulation(self) -> None:
        poor = {"kp": 10.0, "ki": 0.1, "kd": 1.0}
        record = synthetic_run(gains=poor)
        started = time.perf_counter()
        report, entry, suggestion = monitor.analyse(record, [])
        self.assertLess(time.perf_counter() - started, 30.0)
        self.assertIsNotNone(suggestion)
        # Check against the true oven, not the fitted one.
        args = (40.0, TRUE_OVEN.ambient_c, config.FF_PERCENT_PER_DEGREE, 60.0)
        before = monitor.simulate_run(TRUE_OVEN, poor, *args)
        after = monitor.simulate_run(TRUE_OVEN, suggestion.gains, *args)
        self.assertLessEqual(after.overshoot_c, max(0.3, before.overshoot_c))
        self.assertLess(monitor._cost(after, 600.0), monitor._cost(before, 600.0))
        self.assertIn("Suggestion", report)
        self.assertEqual(entry["suggested_gains"], suggestion.gains)

    def test_suggested_gains_fit_the_gui_fields(self) -> None:
        gains = monitor.simc_gains(TRUE_OVEN)
        for name, value in gains.items():
            low, high, decimals = monitor.GAIN_LIMITS[name]
            self.assertTrue(low <= value <= high)
            self.assertEqual(value, round(value, decimals))

    def test_simulation_flags_unsafe_peaks(self) -> None:
        hot = ThermalModel(2.0, 300.0, 120.0, 10.0, 21.0)
        result = monitor.simulate_run(hot, {"kp": 10.0, "ki": 1.0, "kd": 0.0}, 45.0, 21.0, 1.0, 60.0)
        self.assertFalse(result.safe)


class StorageTests(unittest.TestCase):
    def test_append_writes_report_history_and_importable_settings(self) -> None:
        record = synthetic_run()
        with tempfile.TemporaryDirectory() as directory:
            log_path = monitor.append_recommendation(record, directory)
            self.assertIn("PID tuning report", log_path.read_text(encoding="utf-8"))
            history = [json.loads(line) for line in
                       (Path(directory) / monitor.HISTORY_FILE).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(history), 1)
            self.assertIsNotNone(history[0]["model"])
            from data_manager import DataManager
            settings = DataManager.import_settings(Path(directory) / monitor.SUGGESTED_SETTINGS_FILE)
            self.assertEqual(settings["target_temperature_c"], 40.0)
            self.assertEqual({k: settings[k] for k in ("kp", "ki", "kd")}, history[0]["suggested_gains"])

    def test_history_models_feed_later_runs(self) -> None:
        record = synthetic_run()
        _report, entry, _suggestion = monitor.analyse(record, [])
        _report, _entry, suggestion = monitor.analyse(record, [entry, entry])
        self.assertEqual(suggestion.models_used, 3)
        self.assertEqual(suggestion.confidence, "high")

    def test_short_run_gives_no_suggestion(self) -> None:
        record = synthetic_run()
        record["samples"] = record["samples"][:100]
        report, entry, suggestion = monitor.analyse(record, [])
        self.assertIsNone(suggestion)
        self.assertIsNone(entry["suggested_gains"])
        self.assertIn("None yet", report)


if __name__ == "__main__":
    unittest.main()
