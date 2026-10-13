"""Portable storage for complete kiln runs and reusable settings."""
from __future__ import annotations
import csv
import json
import math
from datetime import datetime
from pathlib import Path


class DataManager:
    RUN_VERSION = 1
    SAMPLE_FIELDS = (
        "elapsed_s", "temperature_c", "target_temperature_c",
        "heater_output_percent", "kp", "ki", "kd",
    )
    SETTING_FIELDS = ("target_temperature_c", "hold_duration_s", "kp", "ki", "kd")

    def __init__(self, directory):
        self.directory = Path(directory)

    @classmethod
    def build_run_record(cls, view, samples):
        stopped = view.state in ("paused", "aborted")
        progress = {"stage": view.stop_stage if stopped and view.stop_stage is not None else view.stage,
            "fraction": view.stop_fraction if stopped and view.stop_fraction is not None else view.fraction,
            "detail": view.stop_detail if stopped and view.stop_detail is not None else view.detail}
        return {
            "schema_version": cls.RUN_VERSION,
            "started_at": view.run_started_at,
            "settings": dict(view.run_settings or {}),
            "result": {"state": view.state, "status": view.status, "duration_s": view.elapsed_s,
                "reason": view.error or (view.detail if view.state in ("fault", "e_stop") else ""),
                "hold_reached_s": view.hold_reached_s, "cooldown_s": view.cooldown_s,
                "hold_completed": view.hold_completed, "stop_state": view.stop_state,
                "stop_elapsed_s": view.stop_elapsed_s, "hold_elapsed_s": view.hold_elapsed_s},
            "progress": progress,
            "events": [dict(event) for event in getattr(view, "events", ())],
            "metrics": {
                "peak": None if view.peak_c is None else {"temperature_c": view.peak_c, "elapsed_s": view.peak_time_s},
                "hold_minimum": None if view.hold_min_c is None else {"temperature_c": view.hold_min_c, "elapsed_s": view.hold_min_time_s},
            },
            "samples": list(samples),
        }

    def export_run(self, record, path=None):
        if path is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            path = self.directory / f"kiln-run-{stamp}.json"
        path = Path(path).with_suffix(".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2, allow_nan=False)
            stream.write("\n")
        return path

    @classmethod
    def import_run(cls, path):
        with Path(path).open(encoding="utf-8-sig") as stream:
            record = json.load(stream)
        if not isinstance(record, dict) or record.get("schema_version") != cls.RUN_VERSION:
            raise ValueError("Unsupported or invalid kiln run file")
        settings = record.get("settings")
        progress = record.get("progress")
        result = record.get("result")
        metrics = record.get("metrics")
        samples = record.get("samples")
        events = record.get("events", [])
        if not isinstance(events, list):
            raise ValueError("Run events must be a list")
        if not isinstance(settings, dict) or any(key not in settings for key in cls.SETTING_FIELDS):
            raise ValueError("Run file is missing its process settings")
        if not isinstance(progress, dict) or not isinstance(result, dict) or not isinstance(metrics, dict) or not isinstance(samples, list):
            raise ValueError("Run file is missing its progress, result, metrics, or samples")
        record["events"] = events
        try:
            for key in cls.SETTING_FIELDS:
                settings[key] = cls._number(settings[key], key)
            progress["stage"] = int(progress["stage"])
            progress["fraction"] = cls._number(progress["fraction"], "progress fraction")
            result["duration_s"] = cls._number(result["duration_s"], "run duration")
            if result["duration_s"] < 0 or not isinstance(result.get("status"), str):
                raise ValueError("Run file contains invalid outcome details")
            if not 0 <= progress["stage"] <= 4 or not 0.0 <= progress["fraction"] <= 1.0:
                raise ValueError("Run file contains invalid progress values")
            if result.get("state") not in {"complete", "paused", "aborted", "fault", "e_stop"}:
                raise ValueError("Run file contains an invalid terminal state")
            previous_elapsed = -1.0
            for sample in samples:
                for key in cls.SAMPLE_FIELDS:
                    sample[key] = cls._number(sample[key], key)
                if sample["elapsed_s"] < previous_elapsed or sample["elapsed_s"] < 0:
                    raise ValueError("Run samples must have increasing elapsed times")
                previous_elapsed = sample["elapsed_s"]
            for key in ("hold_reached_s", "cooldown_s", "stop_elapsed_s"):
                if result.get(key) is not None:
                    result[key] = cls._number(result[key], key)
            for key in ("peak", "hold_minimum"):
                metric = metrics.get(key)
                if metric is not None:
                    if not isinstance(metric, dict):
                        raise ValueError(f"{key} must be a temperature and elapsed-time pair")
                    metric["temperature_c"] = cls._number(metric["temperature_c"], f"{key} temperature")
                    metric["elapsed_s"] = cls._number(metric["elapsed_s"], f"{key} elapsed time")
            previous_event_time = -1.0
            for event in events:
                if (not isinstance(event, dict)
                        or not isinstance(event.get("type"), str)
                        or not isinstance(event.get("description"), str)):
                    raise ValueError("Run file contains an invalid event")
                event["elapsed_s"] = cls._number(event["elapsed_s"], "event elapsed time")
                if event["elapsed_s"] < previous_event_time or event["elapsed_s"] < 0:
                    raise ValueError("Run events must be in chronological order")
                previous_event_time = event["elapsed_s"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid kiln run file: {exc}") from exc
        return record

    @staticmethod
    def _number(value, field):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{field} must be finite")
        return number

    @classmethod
    def import_settings(cls, path):
        with Path(path).open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) != 1 or any(key not in rows[0] for key in cls.SETTING_FIELDS):
            raise ValueError("Settings CSV must contain one row with target, hold duration, and PID gains")
        try:
            return {key: cls._number(rows[0][key], key) for key in cls.SETTING_FIELDS}
        except (TypeError, ValueError) as exc:
            raise ValueError("Settings file contains an invalid numeric value") from exc

    @classmethod
    def export_settings(cls, settings, path):
        path = Path(path).with_suffix(".csv")
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=cls.SETTING_FIELDS)
            writer.writeheader()
            writer.writerow({key: settings[key] for key in cls.SETTING_FIELDS})
        return path
