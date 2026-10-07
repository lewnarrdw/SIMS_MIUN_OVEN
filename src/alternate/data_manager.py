"""CSV persistence for run samples and process settings."""
from __future__ import annotations
import csv
from datetime import datetime
from pathlib import Path

class DataManager:
    RUN_FIELDS = ("elapsed_s", "temperature_c", "target_temperature_c", "heater_output_percent")
    SETTING_FIELDS = ("target_temperature_c", "hold_duration_s", "kp", "ki", "kd")
    def __init__(self, directory):
        self.directory = Path(directory)
    def export_run(self, samples, path=None):
        if not samples: raise ValueError("There are no run samples to export")
        self.directory.mkdir(parents=True, exist_ok=True)
        if path is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            path = self.directory / f"kiln-run-{stamp}.csv"
        path = Path(path).with_suffix(".csv")
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.RUN_FIELDS)
            writer.writeheader()
            writer.writerows({field: sample[field] for field in self.RUN_FIELDS} for sample in samples)
        return path
    @classmethod
    def import_settings(cls, path):
        with Path(path).open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) != 1 or any(key not in rows[0] for key in cls.SETTING_FIELDS):
            raise ValueError("Settings CSV must contain one row with target, hold duration, and PID gains")
        try:
            return {key: float(rows[0][key]) for key in cls.SETTING_FIELDS}
        except (TypeError, ValueError) as exc:
            raise ValueError("Settings CSV contains a non-numeric value") from exc
    @classmethod
    def export_settings(cls, settings, path):
        path = Path(path).with_suffix(".csv")
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=cls.SETTING_FIELDS)
            writer.writeheader()
            writer.writerow({key: settings[key] for key in cls.SETTING_FIELDS})
        return path




