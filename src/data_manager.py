"""Portable storage for complete kiln runs and reusable settings."""
from __future__ import annotations
import csv
import json
import math
from datetime import datetime
from pathlib import Path

from run_summary import validate_summary

RUN_FORMATS = (".csv", ".json")

# A run CSV holds one row per sample, with the graph data in the leftmost columns.
# The summary, event log and remaining run details sit in column groups to the
# right, each reading down from the first row, separated by an empty column.
SUMMARY_COLUMNS = ("summary_section", "summary_label", "summary_value", "summary_level", "summary_hint")
EVENT_COLUMNS = ("event_elapsed_s", "event_type", "event_severity", "event_description",
                 "event_time", "event_details")
INFO_COLUMNS = ("run_field", "run_value")
_EVENT_KEYS = {"event_elapsed_s": "elapsed_s", "event_type": "type", "event_severity": "severity",
               "event_description": "description", "event_time": "time"}


def _raise_constant(name):
    raise ValueError(name)


def _decode_cell(text: str):
    """A run detail cell: empty is None, JSON values are parsed, anything else is text."""
    if text == "":
        return None
    try:
        value = json.loads(text, parse_constant=_raise_constant)
    except ValueError:
        return text
    if isinstance(value, (bool, int, float, dict, list)) or text.startswith('"'):
        return value
    return text


def _encode_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        # Quote text that would otherwise read back as another value, such as "" or "42".
        decoded = _decode_cell(value)
        return value if isinstance(decoded, str) and decoded == value else json.dumps(value)
    return json.dumps(value, allow_nan=False)


def _flatten(prefix: str, value, rows: list) -> None:
    if isinstance(value, dict) and value:
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else key, item, rows)
    else:
        rows.append((prefix, _encode_cell(value)))


def run_csv_rows(record: dict) -> list[list[str]]:
    """The rows of a run CSV, header first."""
    samples = [[str(sample[key]) for key in DataManager.SAMPLE_FIELDS] for sample in record.get("samples", ())]
    def text(value) -> str:
        return "" if value is None else str(value)

    summary = [[text(item.get(column.removeprefix("summary_"))) for column in SUMMARY_COLUMNS]
               for item in record.get("summary") or ()]
    events = []
    for event in record.get("events", ()):
        # Fields without a column of their own, such as the settings of a run start.
        details = {key: value for key, value in event.items() if key not in _EVENT_KEYS.values()}
        events.append([text(event.get(_EVENT_KEYS[column])) for column in EVENT_COLUMNS[:-1]]
                      + [json.dumps(details, allow_nan=False) if details else ""])
    info: list = []
    for key, value in record.items():
        if key not in ("samples", "summary", "events"):
            _flatten(key, value, info)
    groups = [(DataManager.SAMPLE_FIELDS, samples), (SUMMARY_COLUMNS, summary),
              (EVENT_COLUMNS, events), (INFO_COLUMNS, [list(row) for row in info])]
    header: list[str] = []
    for index, (columns, _rows) in enumerate(groups):
        header += ([""] if index else []) + list(columns)
    rows = [header]
    for row_index in range(max(len(group_rows) for _columns, group_rows in groups)):
        row: list[str] = []
        for index, (columns, group_rows) in enumerate(groups):
            row += ([""] if index else []) + (group_rows[row_index] if row_index < len(group_rows)
                                              else [""] * len(columns))
        rows.append(row)
    return rows


def run_from_csv_rows(rows: list[list[str]]) -> dict:
    """The run record in a run CSV; import_run validates it."""
    if not rows:
        raise ValueError("Run CSV is empty")
    columns = {name: index for index, name in enumerate(rows[0]) if name}
    required = (*DataManager.SAMPLE_FIELDS, *SUMMARY_COLUMNS, *EVENT_COLUMNS, *INFO_COLUMNS)
    if any(name not in columns for name in required):
        raise ValueError("Unsupported or invalid kiln run file")

    def group(names, row):
        cells = {name: row[columns[name]] if columns[name] < len(row) else "" for name in names}
        return cells if any(cells.values()) else None

    record: dict = {}
    samples, summary, events = [], [], []
    for row in rows[1:]:
        if (cells := group(DataManager.SAMPLE_FIELDS, row)) is not None:
            samples.append(dict(cells))  # import_run converts and checks the numbers.
        if (cells := group(SUMMARY_COLUMNS, row)) is not None:
            item = {name.removeprefix("summary_"): value for name, value in cells.items()}
            item["level"] = item["level"] or None
            summary.append(item)
        if (cells := group(EVENT_COLUMNS, row)) is not None:
            details = cells["event_details"]
            try:
                event = json.loads(details) if details else {}
            except ValueError as exc:
                raise ValueError("Run CSV contains invalid event details") from exc
            if not isinstance(event, dict):
                raise ValueError("Run CSV contains invalid event details")
            for column, key in _EVENT_KEYS.items():
                if cells[column] or key in ("type", "description"):
                    event[key] = cells[column]
            events.append(event)
        if (cells := group(INFO_COLUMNS, row)) is not None:
            target = record
            *parents, leaf = cells["run_field"].split(".")
            for parent in parents:
                target = target.setdefault(parent, {})
                if not isinstance(target, dict):
                    raise ValueError(f"Run CSV contains conflicting fields at {cells['run_field']}")
            target[leaf] = _decode_cell(cells["run_value"])
    record["samples"], record["events"] = samples, events
    if summary:
        record["summary"] = summary
    return record


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
        stopped = view.state == "aborted"
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

    def export_run(self, record, path=None, run_format=None):
        """Save a run as CSV (the default) or JSON; a .csv or .json path picks the format."""
        if path is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            path = self.directory / f"kiln-run-{stamp}"
        path = Path(path)
        if path.suffix.lower() in RUN_FORMATS:
            run_format = path.suffix.lower()[1:]
        run_format = run_format or "csv"
        if f".{run_format}" not in RUN_FORMATS:
            raise ValueError(f"Unsupported run file format: {run_format}")
        path = path.with_suffix(f".{run_format}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if run_format == "csv":
            # The BOM lets Excel read the °C and minus signs in the summary.
            with path.open("w", newline="", encoding="utf-8-sig") as stream:
                csv.writer(stream).writerows(run_csv_rows(record))
        else:
            with path.open("w", encoding="utf-8") as stream:
                json.dump(record, stream, indent=2, allow_nan=False)
                stream.write("\n")
        return path

    @classmethod
    def import_run(cls, path):
        path = Path(path)
        if path.suffix.lower() == ".csv":
            with path.open(newline="", encoding="utf-8-sig") as stream:
                record = run_from_csv_rows(list(csv.reader(stream)))
        else:
            with path.open(encoding="utf-8-sig") as stream:
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
            if result.get("state") not in {"complete", "aborted", "fault", "e_stop"}:
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
            # Runs saved before summaries were stored have none; it is computed when shown.
            if "summary" in record:
                validate_summary(record["summary"])
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
