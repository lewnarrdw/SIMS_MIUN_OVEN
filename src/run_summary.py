"""Run summary: the key metrics of a run record, without any Qt dependency.

The worker saves the summary in each run file, so an imported run shows what was
shown when it finished; files saved before that are summarised when opened.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import math
import statistics

# Overshoot above this is worth flagging, matching the PID tuning target.
OVERSHOOT_WARNING_C = 0.3
# The hold should spend at least this share of its time inside the tolerance band.
HOLD_IN_BAND_WARNING = 0.9
MISSING = "—"
LEVELS = (None, "good", "warning", "error")


@dataclass(frozen=True)
class Metric:
    section: str
    label: str
    value: str
    level: str | None = None  # good, warning, error or None
    hint: str = ""


def peak_error(
    target_c: float,
    peak: tuple[float, float] | None,
    hold_low: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """Return (elapsed_s, temperature_c) of the larger deviation from the target.

    peak and hold_low are (elapsed_s, temperature_c): the run's highest temperature
    and the lowest during the hold. A peak below the target or a low above it is not
    a deviation in its direction, so it does not count.
    """
    candidates = []
    if peak is not None and peak[1] >= target_c:
        candidates.append(peak)
    if hold_low is not None and hold_low[1] <= target_c:
        candidates.append(hold_low)
    if not candidates:
        candidates = [point for point in (peak, hold_low) if point is not None]
    return max(candidates, key=lambda point: abs(point[1] - target_c), default=None)


def format_error_c(error_c: float) -> str:
    """Signed error vs target, with a true minus sign below it."""
    return f"{'−' if error_c < 0 else '+'}{abs(error_c):.2f} °C"


def format_duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return MISSING
    minutes, rest = divmod(max(0.0, seconds), 60.0)
    return f"{int(minutes):02d}:{rest:04.1f}"


def _celsius(value: float | None, decimals: int = 2) -> str:
    return MISSING if value is None else f"{value:.{decimals}f} °C"


def _event_time(events: list[dict], description: str) -> float | None:
    times = [event["elapsed_s"] for event in events if event.get("description") == description]
    return times[-1] if times else None


def _between(samples: list[dict], start_s: float | None, end_s: float | None) -> list[dict]:
    if start_s is None:
        return []
    end_s = math.inf if end_s is None else end_s
    return [row for row in samples if start_s <= row["elapsed_s"] <= end_s]


def _time_weighted_mean(rows: list[dict], key: str) -> float | None:
    if len(rows) < 2:
        return rows[0][key] if rows else None
    total = sum(b["elapsed_s"] - a["elapsed_s"] for a, b in zip(rows, rows[1:]))
    if total <= 0:
        return statistics.fmean(row[key] for row in rows)
    return sum(a[key] * (b["elapsed_s"] - a["elapsed_s"]) for a, b in zip(rows, rows[1:])) / total


def _outcome(record: dict) -> tuple[str, str]:
    result = record.get("result", {})
    state = result.get("state", "")
    if state == "complete":
        return "Completed", "good"
    if state == "aborted":
        during = (result.get("stop_state") or "run").replace("_", " ")
        return f"Stopped during {during}", "warning"
    if state == "fault":
        from application import describe_fault  # Imported here: application imports this module.
        return f"Fault: {describe_fault(result.get('reason') or 'unknown')}", "error"
    if state == "e_stop":
        return "Emergency stop", "error"
    return "In progress", None


def summarize(record: dict, tolerance_c: float) -> list[Metric]:
    """The run's key metrics, grouped into sections, from a run record."""
    settings, result = record.get("settings", {}), record.get("result", {})
    events, samples = list(record.get("events", ())), list(record.get("samples", ()))
    target = settings.get("target_temperature_c")
    if target is None and samples:
        target = samples[-1]["target_temperature_c"]
    metrics: list[Metric] = []

    def add(section: str, label: str, value: str, level: str | None = None, hint: str = "") -> None:
        metrics.append(Metric(section, label, value, level, hint))

    # Run
    outcome, outcome_level = _outcome(record)
    add("Run", "Outcome", outcome, outcome_level)
    try:
        started = datetime.fromisoformat(record["started_at"]).strftime("%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError):
        started = MISSING
    add("Run", "Started", started)
    add("Run", "Duration", format_duration(result.get("duration_s")))
    add("Run", "Target", _celsius(target, 1))
    add("Run", "Hold setting", format_duration(settings.get("hold_duration_s")))
    if all(key in settings for key in ("kp", "ki", "kd")):
        add("Run", "PID gains", f"Kp {settings['kp']:g} · Ki {settings['ki']:g} · Kd {settings['kd']:g}")

    # Timing
    start_temperature = samples[0]["temperature_c"] if samples else None
    in_band_at = None
    if target is not None:
        in_band_at = next((row["elapsed_s"] for row in samples
                           if abs(row["temperature_c"] - target) <= tolerance_c), None)
    hold_at = result.get("hold_reached_s")
    hold_s = result.get("hold_elapsed_s") or 0.0
    cooling_at = _event_time(events, "Cool down started")
    add("Timing", "Start temperature", _celsius(start_temperature))
    add("Timing", f"Time to reach ±{tolerance_c:g} °C", format_duration(in_band_at),
        hint="From the start until the temperature first entered the band around the target")
    if in_band_at and start_temperature is not None and in_band_at > 0:
        rate = (samples_at(samples, in_band_at) - start_temperature) / in_band_at * 60.0
        add("Timing", "Average heating rate", f"{rate:+.2f} °C/min")
    else:
        add("Timing", "Average heating rate", MISSING)
    settle = None if hold_at is None or in_band_at is None else hold_at - in_band_at
    add("Timing", "Settling (band to hold)", format_duration(settle),
        hint="Time the stability check needed after the temperature reached the band")
    add("Timing", "Warm-up (start to hold)", format_duration(hold_at))
    add("Timing", "Hold time", format_duration(hold_s if hold_at is not None else None))
    add("Timing", "Cool-down", format_duration(result.get("cooldown_s")),
        hint="From the end of the hold until the door opened")
    if cooling_at is not None:
        cooling = _between(samples, cooling_at, None)
        if len(cooling) >= 2 and cooling[-1]["elapsed_s"] > cooling[0]["elapsed_s"]:
            drop = (cooling[0]["temperature_c"] - cooling[-1]["temperature_c"])
            rate = drop / (cooling[-1]["elapsed_s"] - cooling[0]["elapsed_s"]) * 60.0
            add("Timing", "Average cooling rate", f"{rate:.2f} °C/min")

    # Accuracy
    peak = (record.get("metrics") or {}).get("peak")
    low = (record.get("metrics") or {}).get("hold_minimum")
    peak_point = None if not peak else (peak["elapsed_s"], peak["temperature_c"])
    low_point = None if not low else (low["elapsed_s"], low["temperature_c"])
    if peak_point:
        add("Accuracy", "Peak temperature", f"{_celsius(peak_point[1])} at {format_duration(peak_point[0])}")
    else:
        add("Accuracy", "Peak temperature", MISSING)
    overshoot = None if not peak_point or target is None else max(0.0, peak_point[1] - target)
    add("Accuracy", "Overshoot", _celsius(overshoot),
        None if overshoot is None else "warning" if overshoot > OVERSHOOT_WARNING_C else "good")
    error_point = None if target is None else peak_error(target, peak_point, low_point)
    if error_point:
        error_c = error_point[1] - target
        add("Accuracy", "Peak error", f"{format_error_c(error_c)} at {format_duration(error_point[0])}",
            "good" if abs(error_c) <= tolerance_c else "warning",
            hint="The larger of the overshoot (+) and the hold stage's dip below target (−)")
    else:
        add("Accuracy", "Peak error", MISSING)
    hold_rows = _between(samples, hold_at, None if hold_at is None else hold_at + hold_s)
    values = [row["temperature_c"] for row in hold_rows]
    if values and target is not None:
        errors = [value - target for value in values]
        in_band = sum(abs(error) <= tolerance_c for error in errors) / len(errors)
        add("Accuracy", "Hold mean", _celsius(statistics.fmean(values), 3))
        add("Accuracy", "Hold standard deviation",
            _celsius(statistics.pstdev(values) if len(values) > 1 else 0.0, 3))
        add("Accuracy", "Hold min / max", f"{min(values):.2f} / {max(values):.2f} °C")
        add("Accuracy", "Hold mean absolute error", _celsius(statistics.fmean(abs(e) for e in errors), 3))
        add("Accuracy", "Hold RMS error", _celsius(math.sqrt(statistics.fmean(e * e for e in errors)), 3))
        add("Accuracy", f"Hold time within ±{tolerance_c:g} °C", f"{in_band * 100:.1f} %",
            "good" if in_band >= HOLD_IN_BAND_WARNING else "warning")
    else:
        add("Accuracy", "Hold statistics", "No hold stage in this run")

    # Heater
    warm_up = _between(samples, 0.0, hold_at if hold_at is not None else cooling_at)
    add("Heater", "Mean output, warm-up", _percent(_time_weighted_mean(warm_up, "heater_output_percent")))
    add("Heater", "Mean output, hold", _percent(_time_weighted_mean(hold_rows, "heater_output_percent")),
        hint="The power needed to hold the target")
    full_power = sum(b["elapsed_s"] - a["elapsed_s"] for a, b in zip(warm_up, warm_up[1:])
                     if a["heater_output_percent"] >= 99.5)
    add("Heater", "Time at full power", format_duration(full_power if warm_up else None))
    on_time = sum((b["elapsed_s"] - a["elapsed_s"]) * a["heater_output_percent"] / 100.0
                  for a, b in zip(samples, samples[1:]))
    add("Heater", "Equivalent full-power time", format_duration(on_time if samples else None),
        hint="Total heater energy expressed as time at 100 % output")

    # Data
    add("Data", "Samples", f"{len(samples):,}".replace(",", " "))
    span = samples[-1]["elapsed_s"] - samples[0]["elapsed_s"] if len(samples) > 1 else 0.0
    add("Data", "Sample rate", MISSING if span <= 0 else f"{(len(samples) - 1) / span:.1f} Hz")
    gap = max((b["elapsed_s"] - a["elapsed_s"] for a, b in zip(samples, samples[1:])), default=None)
    add("Data", "Largest gap between samples", MISSING if gap is None else f"{gap:.2f} s",
        None if gap is None else "warning" if gap > 1.0 else None)
    levels = [event.get("severity") for event in events]
    add("Data", "Events", f"{len(events)} ({levels.count('warning')} warnings, {levels.count('error')} errors)",
        "error" if "error" in levels else "warning" if "warning" in levels else None)
    return metrics


def samples_at(samples: list[dict], elapsed_s: float) -> float:
    """Temperature of the first sample at or after elapsed_s."""
    return next(row["temperature_c"] for row in samples if row["elapsed_s"] >= elapsed_s)


def _percent(value: float | None) -> str:
    return MISSING if value is None else f"{value:.1f} %"


def summary_text(metrics: list[Metric]) -> str:
    """Plain text for the clipboard, one section per block."""
    lines, section = [], None
    for metric in metrics:
        if metric.section != section:
            section = metric.section
            lines.append(("\n" if lines else "") + section)
        lines.append(f"  {metric.label}: {metric.value}")
    return "\n".join(lines)


def attach_summary(record: dict, tolerance_c: float) -> dict:
    """Store the summary in the record so it is saved with the run."""
    record["summary"] = [asdict(metric) for metric in summarize(record, tolerance_c)] if record.get("samples") else []
    return record


def record_summary(record: dict | None, tolerance_c: float) -> list[Metric]:
    """The summary saved with the record, or one computed for older files."""
    if not record:
        return []
    saved = record.get("summary")
    if isinstance(saved, list) and saved:
        return [Metric(**item) for item in saved]
    return summarize(record, tolerance_c) if record.get("samples") else []


def validate_summary(saved: object) -> None:
    """Raise ValueError unless saved is a summary as attach_summary stores it."""
    if not isinstance(saved, list):
        raise ValueError("Run summary must be a list")
    for item in saved:
        if (not isinstance(item, dict)
                or any(not isinstance(item.get(key), str) for key in ("section", "label", "value", "hint"))
                or item.get("level") not in LEVELS):
            raise ValueError("Run file contains an invalid summary entry")
