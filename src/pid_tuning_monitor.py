"""Temporary PID tuning assistant for completed oven runs.

After every run this module:

1. measures how the run went (warm-up time, overshoot, hold accuracy, and how
   far the oven kept heating after the heater switched off);
2. fits a small thermal model of the oven to the run: a heater element that
   stores heat (heater lag), a transport delay (dead time), and an oven that
   loses heat to the room;
3. simulates the real controller (``control.pid.PID`` with the configured
   feed-forward and stage rules) against that model, and searches for gains
   that reach a stable target quickly without overshoot, also when the oven is
   a bit slower or stronger than the model says;
4. writes a plain-language report, a history line, and a settings CSV that can
   be loaded with File > Import settings.

It is advisory only: it never changes controller settings or hardware output.
Remove it and its application hook once the PID behaves well (the report says
when the suggested gains stop changing).

Run it by hand on saved runs as well:

    python pid_tuning_monitor.py data/kiln-run-*.csv
"""
from __future__ import annotations

import csv
import glob
import json
import math
import statistics
import sys
from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

import numpy as np

import config
from control.pid import PID
from control.stability import StabilityDetector

LOG_FILE = "pid_tuning_log.txt"
HISTORY_FILE = "pid_tuning_history.jsonl"
SUGGESTED_SETTINGS_FILE = "pid_suggested_settings.csv"

# The GUI's gain fields: (minimum, maximum, decimals). Suggestions must be enterable.
# The same ranges as the GUI's PID inputs (MAX_KP, MAX_KI and MAX_KD in gui.py).
GAIN_LIMITS = {"kp": (0.0, 200.0, 2), "ki": (0.0, 5.0, 3), "kd": (0.0, 2000.0, 1)}
STEP_S = 1.0
MAX_DEAD_TIME_S = 90
HEATER_LAG_GRID_S = (0.0, 2.0, 4.0, 7.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0)
FREE_RUN_CANDIDATES = 20
SMOOTHING_S = 15.0
MIN_FIT_SECONDS = 60.0
MIN_TEMPERATURE_SPAN_C = 2.0
MIN_OUTPUT_SPREAD_PERCENT = 5.0
GOOD_FIT_RMSE_C = 0.35
USABLE_FIT_RMSE_C = 0.8
HISTORY_MODELS_USED = 5
OVERSHOOT_LIMIT_C = 0.3
SAFETY_MARGIN_C = 1.5
COAST_SEARCH_S = 600.0
SWING_HYSTERESIS_C = 0.05
SETTLED_CHANGE_FRACTION = 0.15


@dataclass(frozen=True)
class ThermalModel:
    """Oven model: heater element lag, dead time, and first-order heat loss to the room.

    gain_c_per_percent: how many degrees above the room 1 % heater output holds the oven at.
    time_constant_s: how quickly the oven settles towards that temperature.
    heater_lag_s: how slowly the heater element warms and cools (the heat that keeps
        coming after the heater is switched off).
    dead_time_s: pure delay before any heater change shows on the sensor.
    """

    gain_c_per_percent: float
    time_constant_s: float
    heater_lag_s: float
    dead_time_s: float
    ambient_c: float
    fit_rmse_c: float = 0.0
    fit_r2: float = 0.0

    @property
    def total_lag_s(self) -> float:
        return self.dead_time_s + self.heater_lag_s


@dataclass(frozen=True)
class RunMetrics:
    analysed: bool
    start_temperature_c: float | None = None
    target_c: float | None = None
    time_to_target_s: float | None = None
    rise_time_s: float | None = None
    overshoot_c: float | None = None
    peak_c: float | None = None
    peak_after_s: float | None = None
    time_to_stable_s: float | None = None
    hold_rms_error_c: float | None = None
    hold_max_error_c: float | None = None
    hold_swings: int | None = None
    saturated_fraction: float | None = None
    coast_rise_c: float | None = None
    coast_s: float | None = None


@dataclass(frozen=True)
class SimulationResult:
    time_to_stable_s: float
    overshoot_c: float
    peak_c: float
    hold_rms_error_c: float
    safe: bool

    @property
    def reaches_stable(self) -> bool:
        return math.isfinite(self.time_to_stable_s)


@dataclass(frozen=True)
class Suggestion:
    gains: dict
    predicted: SimulationResult
    current_predicted: SimulationResult
    feedforward_per_degree: float
    ideal_feedforward_per_degree: float
    feedforward_predicted: SimulationResult | None
    feedforward_gains: dict | None
    limited_by_gui: tuple
    confidence: str
    models_used: int


# ---------------------------------------------------------------- run data


def _event_time(record: dict, description: str, after_s: float = -1.0) -> float | None:
    for event in record.get("events", ()):
        if event.get("description") == description and float(event.get("elapsed_s", -1)) > after_s:
            return float(event["elapsed_s"])
    return None


def _analysis_window(record: dict) -> tuple[float, float]:
    """Heating start to the first door opening after it (the door changes heat loss)."""
    samples = record.get("samples", ())
    start = _event_time(record, "Warm up started")
    if start is None:
        start = float(samples[0]["elapsed_s"]) if samples else 0.0
    end = float(samples[-1]["elapsed_s"]) if samples else start
    for event in record.get("events", ()):
        if event.get("type") == "door_opened" and float(event.get("elapsed_s", -1)) > start:
            end = min(end, float(event["elapsed_s"]))
            break
    return start, end


def resample(samples: list, start_s: float, end_s: float, step_s: float = STEP_S):
    """Average samples into fixed steps; return (times, temperatures, outputs) arrays."""
    rows = [row for row in samples if start_s <= float(row["elapsed_s"]) <= end_s]
    if len(rows) < 2:
        return np.empty(0), np.empty(0), np.empty(0)
    times = np.array([float(row["elapsed_s"]) for row in rows])
    temperatures = np.array([float(row["temperature_c"]) for row in rows])
    outputs = np.array([float(row["heater_output_percent"]) for row in rows])
    grid = np.arange(times[0], times[-1] + 1e-9, step_s)
    bins = np.minimum(((times - times[0]) / step_s).astype(int), len(grid) - 1)
    counts = np.bincount(bins, minlength=len(grid))
    filled = counts > 0
    temperature_grid = np.bincount(bins, temperatures, len(grid))[filled] / counts[filled]
    output_grid = np.bincount(bins, outputs, len(grid))[filled] / counts[filled]
    centres = grid[filled]
    # Fill empty steps (sensor dropouts) by interpolation so the model sees even time.
    return grid, np.interp(grid, centres, temperature_grid), np.interp(grid, centres, output_grid)


def measure_run(record: dict) -> RunMetrics:
    """Measure the run's actual performance from its logged samples."""
    samples = record.get("samples") or []
    settings = record.get("settings") or {}
    result = record.get("result") or {}
    if len(samples) < 2 or "target_temperature_c" not in settings:
        return RunMetrics(analysed=False)
    target = float(settings["target_temperature_c"])
    heating_start = _event_time(record, "Warm up started")
    if heating_start is None:
        heating_start = float(samples[0]["elapsed_s"])
    end = float(samples[-1]["elapsed_s"])
    times, temperatures, outputs = resample(samples, heating_start, end)
    if len(times) < 2:
        return RunMetrics(analysed=False)
    start_temperature = float(temperatures[0])
    cooling_start = _event_time(record, "Cool down started", heating_start)
    controlled_end = cooling_start if cooling_start is not None else end
    controlled = times <= controlled_end

    crossing = np.nonzero(temperatures >= target)[0] if target >= start_temperature else np.nonzero(temperatures <= target)[0]
    time_to_target = float(times[crossing[0]] - heating_start) if len(crossing) else None
    rise_time = None
    span = target - start_temperature
    if abs(span) > 0.5:
        progress = (temperatures - start_temperature) / span
        low, high = np.nonzero(progress >= 0.1)[0], np.nonzero(progress >= 0.9)[0]
        if len(low) and len(high):
            rise_time = float(times[high[0]] - times[low[0]])

    overshoot = peak = peak_after = None
    if len(crossing) and target >= start_temperature:
        window = controlled & (times >= times[crossing[0]])
        if window.any():
            index = int(np.argmax(np.where(window, temperatures, -np.inf)))
            peak = float(temperatures[index])
            overshoot = max(0.0, peak - target)
            peak_after = float(times[index] - heating_start)

    hold_reached = result.get("hold_reached_s")
    time_to_stable = None if hold_reached is None else max(0.0, float(hold_reached) - heating_start)
    hold_rms = hold_max = swings = None
    if hold_reached is not None:
        in_hold = controlled & (times >= float(hold_reached))
        if in_hold.sum() >= 3:
            errors = temperatures[in_hold] - target
            hold_rms = float(np.sqrt(np.mean(errors ** 2)))
            hold_max = float(np.max(np.abs(errors)))
            swings, side = 0, 0
            for error in errors:
                new_side = 1 if error > SWING_HYSTERESIS_C else -1 if error < -SWING_HYSTERESIS_C else side
                if side and new_side != side:
                    swings += 1
                side = new_side

    saturated = float(np.mean((outputs[controlled] >= 99.5) | (outputs[controlled] <= 0.5))) if controlled.any() else None

    coast_rise = coast_s = None
    if cooling_start is not None:
        # The heater is forced off at the start of cooling: a clean view of stored heat.
        after = (times >= cooling_start) & (times <= cooling_start + COAST_SEARCH_S)
        if after.sum() >= 3:
            start_index = int(np.nonzero(after)[0][0])
            index = int(np.argmax(np.where(after, temperatures, -np.inf)))
            coast_rise = max(0.0, float(temperatures[index] - temperatures[start_index]))
            coast_s = float(times[index] - times[start_index])

    return RunMetrics(
        analysed=True, start_temperature_c=start_temperature, target_c=target,
        time_to_target_s=time_to_target, rise_time_s=rise_time, overshoot_c=overshoot,
        peak_c=peak, peak_after_s=peak_after, time_to_stable_s=time_to_stable,
        hold_rms_error_c=hold_rms, hold_max_error_c=hold_max, hold_swings=swings,
        saturated_fraction=saturated, coast_rise_c=coast_rise, coast_s=coast_s,
    )


# ---------------------------------------------------------------- model


def _lagged(outputs: np.ndarray, lag_s: float, step_s: float = STEP_S) -> np.ndarray:
    """Heater element state before any dead time; the element starts cold (0 %)."""
    if lag_s <= 0.0:
        return outputs.astype(float)
    alpha = 1.0 - math.exp(-step_s / lag_s)
    state = np.empty(len(outputs))
    value = 0.0
    for index, output in enumerate(outputs):
        value += alpha * (output - value)
        state[index] = value
    return state


def _delayed(values: np.ndarray, dead_steps: int) -> np.ndarray:
    # Delay and lag commute from a cold start, so one lagged series serves every dead time.
    return np.concatenate((np.zeros(dead_steps), values))[: len(values)]


def _heater_state(outputs: np.ndarray, dead_steps: int, lag_s: float) -> np.ndarray:
    """Heater element state: the output delayed by the dead time, then lagged."""
    return _delayed(_lagged(outputs, lag_s), dead_steps)


def _centred_average(values: np.ndarray, window_s: float, step_s: float = STEP_S) -> np.ndarray:
    half = max(0, int(round(window_s / step_s)) // 2)
    if half == 0 or len(values) <= 2 * half:
        return values.astype(float)
    kernel = np.ones(2 * half + 1)
    # Divide by the number of samples actually covered so the ends are not pulled down.
    return np.convolve(values, kernel, "same") / np.convolve(np.ones(len(values)), kernel, "same")


def _free_run(temperatures0: float, heater: np.ndarray, a: float, b: float, c: float) -> np.ndarray:
    predicted = np.empty(len(heater))
    temperature = temperatures0
    for index, value in enumerate(heater):
        predicted[index] = temperature
        temperature += a * value - b * temperature + c
    return predicted


def fit_model(times: np.ndarray, temperatures: np.ndarray, outputs: np.ndarray) -> ThermalModel | None:
    """Fit the oven model to one run; None when the data cannot support a model."""
    if len(times) < MIN_FIT_SECONDS / STEP_S:
        return None
    if np.ptp(temperatures) < MIN_TEMPERATURE_SPAN_C or np.ptp(outputs) < MIN_OUTPUT_SPREAD_PERCENT:
        return None
    # Rates from raw readings are mostly sensor noise; a centred average adds no delay.
    smooth = _centred_average(temperatures, SMOOTHING_S)
    rises = smooth[1:] - smooth[:-1]
    max_dead = min(MAX_DEAD_TIME_S, len(times) // 3)
    candidates = []
    for lag_s in HEATER_LAG_GRID_S:
        lagged = _lagged(outputs, lag_s)
        per_lag = []
        for dead_steps in range(0, max_dead + 1):
            heater = _delayed(lagged, dead_steps)
            # One step: T[k+1] - T[k] = a*h[k] - b*T[k] + c, linear in (a, b, c).
            design = np.column_stack((heater[:-1], -smooth[:-1], np.ones(len(rises))))
            (a, b, c), *_ = np.linalg.lstsq(design, rises, rcond=None)
            if a <= 0.0 or b <= 0.0:
                continue
            residual = float(np.sum((design @ np.array((a, b, c)) - rises) ** 2))
            per_lag.append((residual, lag_s, dead_steps, a, b, c))
        # Keep each lag's best few: one-step residuals barely separate nearby dead times.
        candidates += sorted(per_lag, key=lambda item: item[0])[:3]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    variance = float(np.sum((temperatures - temperatures.mean()) ** 2)) or 1e-9
    best = None
    for _residual, lag_s, dead_steps, a, b, c in candidates[:FREE_RUN_CANDIDATES]:
        ambient = c / b
        if not -10.0 <= ambient <= 45.0 or not 5.0 <= 1.0 / b <= 50000.0:
            continue
        # Rank by the full free-running prediction: that is what tuning relies on.
        predicted = _free_run(float(temperatures[0]), _heater_state(outputs, dead_steps, lag_s), a, b, c)
        error = float(np.sum((predicted - temperatures) ** 2))
        if best is None or error < best[0]:
            best = (error, lag_s, dead_steps, a, b, c)
    if best is None:
        return None
    error, lag_s, dead_steps, a, b, c = best
    return ThermalModel(
        gain_c_per_percent=float(a / b), time_constant_s=float(STEP_S / b), heater_lag_s=float(lag_s),
        dead_time_s=float(dead_steps * STEP_S), ambient_c=float(c / b),
        fit_rmse_c=math.sqrt(error / len(temperatures)), fit_r2=float(1.0 - error / variance),
    )


class SimulatedOven:
    """Steps the fitted model forward; matches the discretisation used in fit_model."""

    def __init__(self, model: ThermalModel, temperature_c: float, step_s: float = STEP_S) -> None:
        self.model, self.step_s = model, step_s
        self.temperature_c = temperature_c
        self.heater = 0.0
        self.delay = deque([0.0] * int(round(model.dead_time_s / step_s)))
        self.alpha = 1.0 if model.heater_lag_s <= 0 else 1.0 - math.exp(-step_s / model.heater_lag_s)

    def step(self, output_percent: float) -> float:
        self.delay.append(output_percent)
        self.heater += self.alpha * (self.delay.popleft() - self.heater)
        model = self.model
        self.temperature_c += self.step_s * (
            model.gain_c_per_percent * self.heater - (self.temperature_c - model.ambient_c)
        ) / model.time_constant_s
        return self.temperature_c


def coast_after_full_power(model: ThermalModel, target_c: float) -> tuple[float, float]:
    """Heat full on from room temperature until the target, then off: (extra rise °C, seconds rising)."""
    oven = SimulatedOven(model, model.ambient_c)
    for _ in range(36000):
        if oven.step(100.0) >= target_c:
            break
    switched_off_at = peak = oven.temperature_c
    seconds = peak_seconds = 0.0
    while seconds < 3600.0:
        temperature = oven.step(0.0)
        seconds += STEP_S
        if temperature > peak:
            peak, peak_seconds = temperature, seconds
        elif seconds - peak_seconds > 120.0:
            break
    return peak - switched_off_at, peak_seconds


def simulate_run(model: ThermalModel, gains: dict, target_c: float, start_c: float,
                 feedforward_per_degree: float, hold_s: float,
                 max_duration_s: float = 3600.0) -> SimulationResult:
    """Run the real PID and stage rules (warm up until stable, then hold) on the model.

    A run that is not stable by max_duration_s reports a time to stable of infinity.
    """
    pid = PID(gains["kp"], gains["ki"], gains["kd"], getattr(config, "PID_INTEGRAL_ZONE_C", math.inf))
    oven = SimulatedOven(model, start_c)
    feedforward = max(0.0, feedforward_per_degree * (target_c - config.ROOM_TEMPERATURE))
    period = max(STEP_S, float(config.LOOP_PERIOD_S))
    stability = StabilityDetector(config.STABLE_TOLERANCE, config.STABLE_TIME_S,
                                  getattr(config, "STABLE_IN_BAND_SHARE", 1.0))
    temperature, output, next_control = start_c, 0.0, 0.0
    hold_started = None
    peak, crossed, hold_errors = start_c, False, []
    elapsed = 0.0
    while hold_started is not None or elapsed <= max_duration_s:
        if elapsed >= next_control:
            output = pid.update(target_c, temperature, period, feedforward)
            next_control += period
        temperature = oven.step(output)
        elapsed += STEP_S
        crossed = crossed or temperature >= target_c
        if crossed:
            peak = max(peak, temperature)
            if peak >= config.MAX_TEMPERATURE - SAFETY_MARGIN_C:
                break
        if hold_started is None:
            if stability.update(elapsed, temperature, target_c):
                hold_started = elapsed
        else:
            hold_errors.append(temperature - target_c)
            if elapsed - hold_started >= hold_s:
                break
    return SimulationResult(
        time_to_stable_s=math.inf if hold_started is None else hold_started,
        overshoot_c=max(0.0, peak - target_c) if crossed else 0.0,
        peak_c=peak,
        hold_rms_error_c=math.sqrt(sum(e * e for e in hold_errors) / len(hold_errors)) if hold_errors else math.inf,
        safe=peak < config.MAX_TEMPERATURE - SAFETY_MARGIN_C,
    )


# ---------------------------------------------------------------- tuning


def _cost(result: SimulationResult, reference_s: float) -> float:
    """Lower is better: time to stable, plus heavy penalties for overshoot and danger."""
    if not result.safe:
        return 1e9
    if not result.reaches_stable:
        return 1e7 + result.overshoot_c * 1e4
    overshoot_penalty = 600.0 * max(0.0, result.overshoot_c - 0.1) + 3000.0 * max(0.0, result.overshoot_c - OVERSHOOT_LIMIT_C)
    return result.time_to_stable_s + overshoot_penalty + 0.25 * reference_s * min(result.hold_rms_error_c, 10.0)


def _model_variants(model: ThermalModel) -> tuple[ThermalModel, ...]:
    """The fitted model plus plausible errors in it, so the gains are not knife-edge."""
    return (
        model,
        replace(model, gain_c_per_percent=model.gain_c_per_percent * 1.25),
        replace(model, gain_c_per_percent=model.gain_c_per_percent * 0.8),
        replace(model, dead_time_s=round(model.dead_time_s * 1.3) + 2.0, heater_lag_s=model.heater_lag_s * 1.3),
        replace(model, dead_time_s=round(model.dead_time_s * 0.7), heater_lag_s=model.heater_lag_s * 0.7),
        replace(model, time_constant_s=model.time_constant_s * 0.75, ambient_c=model.ambient_c + 2.0),
    )


NOMINAL_WEIGHT = 0.3


def _robust_score(model: ThermalModel, gains: dict, target_c: float, start_c: float,
                  feedforward: float, hold_s: float, bound: float = math.inf) -> tuple[float, SimulationResult]:
    """Weighted nominal and worst-case cost; gives up early once it cannot beat bound.

    Weighting the worst case keeps the gains away from knife-edge settings that
    only work if the model is exactly right.
    """
    reference = max(60.0, model.time_constant_s)
    # Each cost is at least its time to stable, so simulations past these limits cannot win.
    nominal = simulate_run(model, gains, target_c, start_c, feedforward, hold_s,
                           min(3600.0, bound / NOMINAL_WEIGHT))
    nominal_cost = _cost(nominal, reference)
    worst = nominal_cost
    if NOMINAL_WEIGHT * nominal_cost + (1 - NOMINAL_WEIGHT) * worst >= bound:
        return math.inf, nominal
    for variant in _model_variants(model)[1:]:
        result = simulate_run(variant, gains, target_c, start_c, feedforward, hold_s,
                              min(3600.0, bound / (1 - NOMINAL_WEIGHT)))
        worst = max(worst, _cost(result, reference))
        if NOMINAL_WEIGHT * nominal_cost + (1 - NOMINAL_WEIGHT) * worst >= bound:
            return math.inf, nominal
    return NOMINAL_WEIGHT * nominal_cost + (1 - NOMINAL_WEIGHT) * worst, nominal


def _round_gains(gains: dict) -> dict:
    rounded = {}
    for name, value in gains.items():
        low, high, decimals = GAIN_LIMITS[name]
        rounded[name] = round(min(high, max(low, value)), decimals)
    return rounded


def simc_gains(model: ThermalModel) -> dict:
    """Skogestad SIMC starting point for a lag-plus-delay process, in this PID's units.

    The closed-loop time constant equals the total lag, a robust setting that
    accepts slower response to avoid overshoot from stored heat.
    """
    theta, tau, tau2 = model.dead_time_s, model.time_constant_s, model.heater_lag_s
    if tau2 > tau:
        tau, tau2 = tau2, tau
    closed_loop = max(theta + 0.5 * tau2, 5.0)
    gain = model.gain_c_per_percent
    kc = tau / (gain * (closed_loop + theta))
    integral_time = min(tau, 4.0 * (closed_loop + theta))
    derivative_time = tau2
    # Series to parallel form.
    kp = kc * (1.0 + derivative_time / integral_time)
    return _round_gains({
        "kp": kp,
        "ki": kc / integral_time,
        "kd": kc * derivative_time,
    })


def tune(model: ThermalModel, current: dict, target_c: float, start_c: float,
         feedforward: float, hold_s: float) -> tuple[dict, SimulationResult]:
    """Pattern search over the gains from the best of several starting points."""
    def score(gains, bound=math.inf):
        return _robust_score(model, gains, target_c, start_c, feedforward, hold_s, bound)

    simc = simc_gains(model)
    # A coarse grid as well as the current and SIMC gains keeps the local search
    # from getting stuck near a poor starting point.
    starts = [_round_gains(current), simc, _round_gains({name: value * 0.5 for name, value in simc.items()})]
    starts.append(_round_gains({"kp": config.PID_KP, "ki": config.PID_KI, "kd": config.PID_KD}))
    starts += [_round_gains({"kp": kp, "ki": ki, "kd": kd})
               for kp in (3.0, 10.0, 30.0) for ki in (0.02, 0.08, 0.2) for kd in (0.0, 100.0, 600.0)]
    best, best_score, best_result = starts[0], math.inf, None
    tried = set()

    def consider(trial):
        nonlocal best, best_score, best_result
        key = tuple(sorted(trial.items()))
        if key in tried:
            return False
        tried.add(key)
        trial_score, trial_result = score(trial, best_score)
        if trial_score < best_score - 1e-6:
            best, best_score, best_result = trial, trial_score, trial_result
            return True
        return False

    for start in starts:
        consider(start)
    if best_result is None:
        # Every candidate was pruned only if all scored infinity; report the current gains.
        best_result = simulate_run(model, best, target_c, start_c, feedforward, hold_s)
    for factor in (2.0, 1.4, 1.15):
        improved = True
        while improved:
            improved = False
            for name in ("kp", "ki", "kd"):
                for direction in (factor, 1.0 / factor):
                    trial = dict(best)
                    base = trial[name] if trial[name] > 0 else 10 ** -GAIN_LIMITS[name][2]
                    trial[name] = base * direction
                    improved = consider(_round_gains(trial)) or improved
            # Removing derivative action can be best when the heater lag is small.
            if best["kd"] > 0:
                improved = consider(dict(best, kd=0.0)) or improved
    return best, best_result


def combine_models(models: list[ThermalModel]) -> ThermalModel:
    """Median of recent run models: one odd run cannot swing the suggestion."""
    def median(name):
        return statistics.median(getattr(model, name) for model in models)
    return ThermalModel(
        gain_c_per_percent=median("gain_c_per_percent"), time_constant_s=median("time_constant_s"),
        heater_lag_s=median("heater_lag_s"), dead_time_s=round(median("dead_time_s")),
        ambient_c=models[-1].ambient_c, fit_rmse_c=median("fit_rmse_c"), fit_r2=median("fit_r2"),
    )


def suggest(models: list[ThermalModel], record: dict, metrics: RunMetrics) -> Suggestion | None:
    usable = [model for model in models if model.fit_rmse_c <= USABLE_FIT_RMSE_C]
    if not usable or not metrics.analysed:
        return None
    model = combine_models(usable[-HISTORY_MODELS_USED:])
    settings = record["settings"]
    current = {name: float(settings.get(name, 0.0)) for name in ("kp", "ki", "kd")}
    target = float(settings["target_temperature_c"])
    hold_s = float(settings.get("hold_duration_s", 60.0))
    start = float(metrics.start_temperature_c)
    feedforward = float(config.FF_PERCENT_PER_DEGREE)
    gains, predicted = tune(model, current, target, start, feedforward, hold_s)
    current_predicted = simulate_run(model, current, target, start, feedforward, hold_s)
    ideal_feedforward = round(1.0 / model.gain_c_per_percent, 2)
    feedforward_gains = feedforward_predicted = None
    if abs(ideal_feedforward - feedforward) > 0.2 * max(feedforward, ideal_feedforward):
        ff_gains, ff_predicted = tune(model, gains, target, start, ideal_feedforward, hold_s)
        reference = max(60.0, model.time_constant_s)
        # Only worth a config change when it clearly beats the gains-only suggestion.
        if _cost(ff_predicted, reference) < 0.9 * _cost(predicted, reference):
            feedforward_gains, feedforward_predicted = ff_gains, ff_predicted
    limited = tuple(name for name, value in gains.items() if value >= GAIN_LIMITS[name][1])
    good_fits = sum(model.fit_rmse_c <= GOOD_FIT_RMSE_C for model in usable[-HISTORY_MODELS_USED:])
    confidence = "high" if good_fits >= 3 else "medium" if good_fits >= 1 else "low"
    return Suggestion(
        gains=gains, predicted=predicted, current_predicted=current_predicted,
        feedforward_per_degree=feedforward, ideal_feedforward_per_degree=ideal_feedforward,
        feedforward_predicted=feedforward_predicted, feedforward_gains=feedforward_gains,
        limited_by_gui=limited, confidence=confidence, models_used=len(usable[-HISTORY_MODELS_USED:]),
    )


# ---------------------------------------------------------------- report


def _seconds(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "never"
    minutes, seconds = divmod(value, 60.0)
    return f"{int(minutes)} min {seconds:02.0f} s" if minutes else f"{seconds:.0f} s"


def _verdict(metrics: RunMetrics) -> str:
    if not metrics.analysed:
        return "Not enough data to judge the run."
    problems = []
    if metrics.overshoot_c is not None and metrics.overshoot_c > OVERSHOOT_LIMIT_C:
        problems.append(f"it overshot the target by {metrics.overshoot_c:.2f} °C")
    if metrics.time_to_target_s is None:
        problems.append("it never reached the target")
    elif metrics.time_to_stable_s is None:
        problems.append("it never settled within the stable band")
    if metrics.hold_swings is not None and metrics.hold_swings >= 4:
        problems.append(f"it swung around the target {metrics.hold_swings} times during the hold")
    if not problems:
        return "Good run: no overshoot problem and the hold was steady."
    return "Needs work: " + "; ".join(problems) + "."


def _gain_line(name: str, current: float, suggested: float) -> str:
    decimals = GAIN_LIMITS[name][2]
    if round(current, decimals) == suggested:
        change = "  (same)"
    elif current == 0:
        change = "  (new)"
    else:
        change = f"  ({(suggested / current - 1) * 100:+.0f} %)"
    return f"    {name.upper():<3} {current:>9.{decimals}f} {suggested:>11.{decimals}f}{change}"


def build_report(record: dict, metrics: RunMetrics, model: ThermalModel | None,
                 suggestion: Suggestion | None, previous_gains: dict | None = None) -> str:
    settings = record.get("settings", {})
    result = record.get("result", {})
    lines = [
        f"=== PID tuning report: run {record.get('started_at') or 'unknown'} ({result.get('state', 'unknown')}) ===",
        "Settings used: target {t:.1f} °C, Kp {kp:g}, Ki {ki:g}, Kd {kd:g}, feed-forward {ff:g} %/°C".format(
            t=float(settings.get("target_temperature_c", 0.0)), kp=float(settings.get("kp", 0.0)),
            ki=float(settings.get("ki", 0.0)), kd=float(settings.get("kd", 0.0)),
            ff=float(config.FF_PERCENT_PER_DEGREE)),
        "",
        "What happened",
    ]
    if metrics.analysed:
        lines.append(f"  Warm-up: {metrics.start_temperature_c:.1f} -> {metrics.target_c:.1f} °C, "
                     f"first reached the target after {_seconds(metrics.time_to_target_s)}")
        if metrics.overshoot_c is not None:
            lines.append(f"  Overshoot: {metrics.overshoot_c:+.2f} °C (peak {metrics.peak_c:.2f} °C after "
                         f"{_seconds(metrics.peak_after_s)})")
        lines.append(f"  Stable (±{config.STABLE_TOLERANCE:g} °C for {config.STABLE_TIME_S:g} s) after "
                     f"{_seconds(metrics.time_to_stable_s)}")
        if metrics.hold_rms_error_c is not None:
            lines.append(f"  Hold: typical error {metrics.hold_rms_error_c:.2f} °C, worst {metrics.hold_max_error_c:.2f} °C, "
                         f"{metrics.hold_swings} swings across the target")
        if metrics.coast_rise_c is not None and metrics.coast_rise_c < 0.02:
            lines.append("  Heat after switch-off: the oven started cooling as soon as the heater turned off for cooling")
        elif metrics.coast_rise_c is not None:
            lines.append(f"  Heat after switch-off: when the heater turned off for cooling, the oven kept "
                         f"warming for {_seconds(metrics.coast_s)} and rose another {metrics.coast_rise_c:.2f} °C")
        if metrics.saturated_fraction is not None and metrics.saturated_fraction > 0.5:
            lines.append(f"  Heater was fully on or fully off {metrics.saturated_fraction:.0%} of the time")
    lines += ["", "Verdict: " + _verdict(metrics), "", "What the oven is like (model fitted to this run)"]
    if model is None:
        lines.append("  No model: the run was too short or the temperature and heater output barely changed.")
    else:
        quality = ("good" if model.fit_rmse_c <= GOOD_FIT_RMSE_C else
                   "usable" if model.fit_rmse_c <= USABLE_FIT_RMSE_C else "poor, not used")
        lines += [
            f"  Fit: the model follows the measured temperature within {model.fit_rmse_c:.2f} °C ({quality})",
            f"  Strength: 100 % heater output would eventually hold the oven "
            f"{model.gain_c_per_percent * 100:.0f} °C above {model.ambient_c:.0f} °C",
            f"  Speed: the oven reacts with a time constant of {_seconds(model.time_constant_s)}",
            f"  Lag: {_seconds(model.dead_time_s)} delay plus a heater element lag of {_seconds(model.heater_lag_s)}"
            " - this lag is why it keeps heating after the heater turns off",
        ]
        target = float(settings.get("target_temperature_c", 0.0))
        if target > model.ambient_c:
            rise, seconds = coast_after_full_power(model, target)
            lines.append(f"  Coasting: heated at full power and switched off at {target:.1f} °C, the oven would keep "
                         f"rising for {_seconds(seconds)} to about {target + rise:.1f} °C - the controller must "
                         "back off this much before the target")
    lines += ["", "Suggestion"]
    if suggestion is None:
        lines.append("  None yet: collect a complete run that warms up from room temperature.")
        return "\n".join(lines)
    current = {name: float(settings.get(name, 0.0)) for name in ("kp", "ki", "kd")}
    lines += [
        f"  Based on {suggestion.models_used} run model(s), confidence {suggestion.confidence}. "
        "Predicted with a simulation of this exact controller:",
        "           current   suggested",
    ]
    lines += [_gain_line(name, current[name], suggestion.gains[name]) for name in ("kp", "ki", "kd")]
    now, then = suggestion.current_predicted, suggestion.predicted
    lines.append(f"  Predicted: overshoot {now.overshoot_c:+.2f} -> {then.overshoot_c:+.2f} °C, "
                 f"stable after {_seconds(now.time_to_stable_s)} -> {_seconds(then.time_to_stable_s)}")
    for name in suggestion.limited_by_gui:
        lines.append(f"  Note: {name.upper()} is at the GUI maximum ({GAIN_LIMITS[name][1]:g}); more might help.")
    if suggestion.feedforward_gains is not None:
        ff = suggestion.feedforward_predicted
        lines += [
            f"  Feed-forward: the model says FF_PERCENT_PER_DEGREE = {suggestion.ideal_feedforward_per_degree:g} "
            f"in config.py (now {suggestion.feedforward_per_degree:g}) would hold the target without waiting for "
            "the integral.",
            "    With that change, use Kp {kp:g}, Ki {ki:g}, Kd {kd:g}: predicted overshoot {o:+.2f} °C, "
            "stable after {s}.".format(**suggestion.feedforward_gains, o=ff.overshoot_c, s=_seconds(ff.time_to_stable_s)),
        ]
    if previous_gains and all(
        abs(suggestion.gains[name] - previous_gains.get(name, 0.0))
        <= SETTLED_CHANGE_FRACTION * max(abs(suggestion.gains[name]), 1e-9)
        for name in ("kp", "ki", "kd")
    ) and _verdict(metrics).startswith("Good"):
        lines.append("  The suggestion has settled and this run was good: the PID is tuned. "
                     "This monitor can be removed (see TODO.md).")
    lines.append(f"  To try it: File > Import settings > {SUGGESTED_SETTINGS_FILE}. Repeat a run, then check the next report.")
    return "\n".join(lines)


# ---------------------------------------------------------------- storage


def _load_history(directory: Path) -> list[dict]:
    path = directory / HISTORY_FILE
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _history_models(entries: list[dict]) -> list[ThermalModel]:
    models = []
    for entry in entries:
        try:
            models.append(ThermalModel(**entry["model"]))
        except (KeyError, TypeError):
            continue
    return models


def analyse(record: dict, history: list[dict]) -> tuple[str, dict, Suggestion | None]:
    """Analyse one run against earlier history; return (report, history entry, suggestion)."""
    metrics = measure_run(record)
    start, end = _analysis_window(record)
    model = fit_model(*resample(record.get("samples") or [], start, end))
    models = _history_models(history) + ([model] if model is not None else [])
    suggestion = suggest(models, record, metrics) if model is not None else None
    previous = next((entry.get("suggested_gains") for entry in reversed(history) if entry.get("suggested_gains")), None)
    report = build_report(record, metrics, model, suggestion, previous)
    entry = {
        "started_at": record.get("started_at"),
        "state": (record.get("result") or {}).get("state"),
        "settings": record.get("settings"),
        "metrics": asdict(metrics),
        "model": None if model is None else asdict(model),
        "suggested_gains": None if suggestion is None else suggestion.gains,
    }
    return report, entry, suggestion


def append_recommendation(record: dict, directory: str | Path) -> Path:
    """Analyse a finished run; append the report and history, refresh the suggested settings."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report, entry, suggestion = analyse(record, _load_history(directory))
    log_path = directory / LOG_FILE
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}]\n{report}\n")
    with (directory / HISTORY_FILE).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, allow_nan=False) + "\n")
    if suggestion is not None:
        settings = record["settings"]
        with (directory / SUGGESTED_SETTINGS_FILE).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=("target_temperature_c", "hold_duration_s", "kp", "ki", "kd"))
            writer.writeheader()
            writer.writerow({
                "target_temperature_c": settings["target_temperature_c"],
                "hold_duration_s": settings["hold_duration_s"],
                **suggestion.gains,
            })
    return log_path


def main(argv: list[str]) -> int:
    """Analyse saved run files in order, as if each had just finished; print the reports."""
    paths = sorted({path for pattern in argv for path in (glob.glob(pattern) or [pattern])})
    if not paths:
        print("Usage: python pid_tuning_monitor.py data/kiln-run-*.csv")
        return 2
    history: list[dict] = []
    for path in paths:
        try:
            if Path(path).suffix.lower() == ".csv":
                from data_manager import DataManager  # Only the CLI reads CSV runs.
                record = DataManager.import_run(path)
            else:
                record = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            print(f"Skipping {path}: {exc}")
            continue
        report, entry, _suggestion = analyse(record, history)
        history.append(entry)
        print(f"\n{path}\n{report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
