"""Temporary, read-only PID performance notes for completed oven runs.

This module never changes controller settings or hardware output. Remove it and
its application hook when enough real-oven tuning data has been collected.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def build_recommendation(record: dict) -> str:
    """Summarize a run and suggest one conservative next tuning experiment."""
    settings = record.get("settings", {})
    result = record.get("result", {})
    metrics = record.get("metrics", {})
    samples = record.get("samples", [])
    target = float(settings.get("target_temperature_c", 0.0))
    peak = (metrics.get("peak") or {}).get("temperature_c")
    peak_time = (metrics.get("peak") or {}).get("elapsed_s")
    state = result.get("state", "unknown")
    lines = [
        f"Run {record.get('started_at') or 'unknown'} | state={state} | target={target:.2f} C",
        "PID: Kp={kp:.4g}, Ki={ki:.4g}, Kd={kd:.4g}".format(
            kp=float(settings.get("kp", 0.0)),
            ki=float(settings.get("ki", 0.0)),
            kd=float(settings.get("kd", 0.0)),
        ),
    ]

    if samples:
        first = samples[0]
        crossing = next(
            (sample for sample in samples if float(sample["temperature_c"]) >= target),
            None,
        )
        warmup = "n/a" if crossing is None else f"{float(crossing['elapsed_s']):.1f} s"
        lines.append(
            "Warm-up: {warmup} to first target crossing | duration={duration:.1f} s".format(
                warmup=warmup,
                duration=float(result.get("duration_s", samples[-1]["elapsed_s"])),
            )
        )
        if peak is not None:
            overshoot = float(peak) - target
            lines.append(
                f"Peak: {float(peak):.2f} C ({overshoot:+.2f} C vs target)"
                + (f" at {float(peak_time):.1f} s" if peak_time is not None else "")
            )
        else:
            overshoot = None
        lines.append(
            f"Starting temperature: {float(first['temperature_c']):.2f} C | "
            f"samples: {len(samples)}"
        )
    else:
        overshoot = None
        lines.append("No temperature samples were recorded.")

    if state != "complete":
        advice = "No gain recommendation: compare completed runs under the same load."
    elif len(samples) < 20 or peak is None:
        advice = "No gain recommendation: collect a longer run with at least 20 samples."
    elif overshoot >= 1.0:
        advice = (
            "Next experiment: reduce Kp by 10%, keep Ki/Kd unchanged, and repeat with "
            "the same load. Compare overshoot and warm-up time."
        )
    elif overshoot <= -0.5:
        advice = (
            "Next experiment: increase Kp by 5%, keep Ki/Kd unchanged, and repeat with "
            "the same load. Check settling and overshoot before further changes."
        )
    else:
        advice = (
            "No gain change suggested by peak error alone; compare hold stability and "
            "warm-up time across repeated runs before adjusting gains."
        )
    lines.extend((f"Recommendation: {advice}", "Advisory only; do not apply automatically."))
    return "\n".join(lines)


def append_recommendation(record: dict, directory: str | Path) -> Path:
    """Append a completed-run analysis to the temporary tuning log."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "pid_tuning_log.txt"
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}]\n")
        stream.write(build_recommendation(record))
        stream.write("\n")
    return log_path
