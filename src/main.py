"""Raspberry Pi 5 kiln application entry point."""
from __future__ import annotations
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from PySide6 import QtCore, QtWidgets
import config
import gui
from application import Application
from data_manager import DataManager


class _ImportPreviewService:
    """Minimal inert service for temporarily previewing imported runs on Windows."""

    last_run_record = None

    def __init__(self):
        self._view = SimpleNamespace(
            running=False, stage=0, fraction=0.0, detail="", status="Ready to run",
            elapsed_s=0.0, target_c=config.MIN_SETPOINT, output_percent=0.0,
            door="unknown", state="ready", peak_time_s=None, peak_c=None,
            hold_min_time_s=None, hold_min_c=None, hold_completed=False,
            hold_reached_s=None, cooldown_s=None, stop_state=None,
            stop_elapsed_s=None, error="", events=(), run_started_at=None,
        )

    def snapshot(self):
        return self._view, []

    def command(self, _name, _values=None):
        return None

    def close(self):
        return None

class KilnWindow(gui.OvenWindow):
    """Thin view adapter; device work stays in Application's worker."""
    def __init__(self, service):
        self.service = service
        self.data = DataManager(config.DATA_DIRECTORY)
        self._markers_run = set()
        self._historical_record = None
        self._historical_range_pending = False
        self._last_run_record = None
        super().__init__()
        self._live_graph_x_max = gui.GRAPH_WINDOW_S
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.target_input.valueChanged.connect(self._tune)
        self.kp_input.valueChanged.connect(self._tune)
        self.ki_input.valueChanged.connect(self._tune)
        self.kd_input.valueChanged.connect(self._tune)
        for widget in (self.target_input, self.hold_input, self.kp_input, self.ki_input, self.kd_input):
            widget.valueChanged.connect(self._sync_start_settings)
        self.restore_pid_defaults()
        self.restore_process_defaults()
        self._sync_start_settings()

    def _settings(self):
        return {"target_temperature_c": self.target_input.value(), "hold_duration_s": self.hold_input.value(),
                "kp": self.kp_input.value(), "ki": self.ki_input.value(), "kd": self.kd_input.value()}

    def _tune(self, *_):
        if self._historical_record is None:
            self.service.command("tune", self._settings())

    def _sync_start_settings(self, *_):
        self.service.command("settings", self._settings())

    def start_process(self):
        self._historical_record = None
        self._last_run_record = None
        self._clear_temperature_markers()
        self._markers_run.clear()
        self._live_graph_x_max = max(
            gui.GRAPH_WINDOW_S,
            self.hold_input.value() + gui.GRAPH_INITIAL_BUFFER_S,
        )
        self._set_graph_time_extent(self._live_graph_x_max)
        self.graph_item.setXRange(0, self._live_graph_x_max, padding=0)
        self.temperature_curve.setData([], [])
        self.duty_curve.setData([], [])
        self.goal_curve.setData([], [])
        self.service.command("start")

    def stop_process(self):
        self.service.command("stop")

    def reset_process(self):
        self._historical_record = None
        self._clear_temperature_markers()
        self._markers_run.clear()
        self.service.command("reset")

    def refresh(self):
        live_view, live_samples = self.service.snapshot()
        if self._historical_record is not None and live_view.running:
            self._historical_record = None
            self._clear_temperature_markers()
            self._markers_run.clear()
        if self._historical_record is None:
            view, samples = live_view, live_samples
        else:
            record = self._historical_record
            result, metrics, progress = record["result"], record["metrics"], record["progress"]
            peak = metrics.get("peak") or {}
            low = metrics.get("hold_minimum") or {}
            view = SimpleNamespace(
                running=False, stage=progress["stage"], fraction=progress["fraction"],
                detail=progress.get("detail", ""), status=result["status"],
                elapsed_s=result["duration_s"], target_c=record["settings"]["target_temperature_c"],
                output_percent=record["samples"][-1]["heater_output_percent"] if record["samples"] else 0.0,
                door="unknown", state=result["state"], peak_time_s=peak.get("elapsed_s"),
                peak_c=peak.get("temperature_c"), hold_min_time_s=low.get("elapsed_s"),
                hold_min_c=low.get("temperature_c"), hold_completed=result.get("hold_completed", False),
                hold_reached_s=result.get("hold_reached_s"), cooldown_s=result.get("cooldown_s"),
                stop_state=result.get("stop_state"), stop_elapsed_s=result.get("stop_elapsed_s"),
                error=result.get("reason", ""), events=tuple(record.get("events", ())),
            )
            samples = record["samples"]
        time_extent_s = max(view.elapsed_s, samples[-1]["elapsed_s"] if samples else 0.0)
        if samples and self._historical_record is None:
            if time_extent_s >= self._live_graph_x_max * gui.GRAPH_EXPANSION_TRIGGER:
                self._live_graph_x_max = max(
                    self._live_graph_x_max * gui.GRAPH_EXPANSION_FACTOR,
                    time_extent_s * gui.GRAPH_EXPANSION_FACTOR,
                )
                self.graph_item.setXRange(0, self._live_graph_x_max, padding=0)
        bounded_extent_s = max(
            time_extent_s,
            self._live_graph_x_max if self._historical_record is None else 0.0,
        )
        self._set_graph_time_extent(bounded_extent_s)
        if samples:
            times = [row["elapsed_s"] for row in samples]
            self.temperature_curve.setData(times, [row["temperature_c"] for row in samples])
            self.goal_curve.setData(times, [row["target_temperature_c"] for row in samples])
            self.duty_curve.setData(times, [row["heater_output_percent"] for row in samples])
            if self._historical_record is not None:
                end_s = self._historical_record["result"]["duration_s"]
                if self._historical_range_pending:
                    self.fit_graph_to_data()
                    self._historical_range_pending = False
        else:
            self.temperature_curve.setData([], [])
            self.goal_curve.setData([], [])
            self.duty_curve.setData([], [])
        self.progress.set_progress(view.stage, view.fraction, view.detail)
        self.stage_label.setText(f"Current stage: {view.status}")
        minutes, seconds = divmod(view.elapsed_s, 60.0)
        self.elapsed_label.setText(f"Elapsed: {int(minutes):02d}:{seconds:04.1f}")
        temp_text = "-- °C" if not samples else f"{samples[-1]['temperature_c']:.2f} °C"
        self.temperature_label.setText(f"Temperature: {temp_text}    Output: {view.output_percent:.1f}%")
        self.door_label.setText(f"Door: {view.door.title()}")
        color = "#4caf70" if view.door == "closed" else "#f4b942"
        self.door_label.setStyleSheet(f"color: {color};")
        for widget in (self.target_input, self.hold_input, self.restore_process_button):
            widget.setEnabled(not view.running)
        if view.error:
            self.statusBar().showMessage(view.error, 8000)
        terminal = view.state in ("complete", "aborted", "fault", "e_stop")
        if terminal and view.hold_min_c is not None and "hold_low" not in self._markers_run:
            self.show_hold_minimum_temperature_marker(view.hold_min_time_s, view.hold_min_c)
            self._markers_run.add("hold_low")
        if view.state == "complete" and view.peak_c is not None and "peak" not in self._markers_run:
            self.show_peak_temperature_marker(view.peak_time_s, view.peak_c, view.target_c)
            self._markers_run.add("peak")
        if view.hold_reached_s is not None and "hold_reached" not in self._markers_run:
            self._show_event_marker(view.hold_reached_s, "Hold reached", "#8fc5f5", samples)
            self._markers_run.add("hold_reached")
        if view.state == "aborted" and view.stop_elapsed_s is not None and "stopped" not in self._markers_run:
            stopped_during = (view.stop_state or "run").replace("_", " ").title()
            self._show_event_marker(view.stop_elapsed_s, f"Stopped during {stopped_during}", "#d45b52", samples)
            self._markers_run.add("stopped")
        if terminal and view.cooldown_s is not None and "cooldown" not in self._markers_run:
            self._show_event_marker(view.elapsed_s, f"Cooldown {view.cooldown_s:.0f} s", "#63a8e8", samples)
            self._markers_run.add("cooldown")
        if terminal and "final_fit" not in self._markers_run:
            self.fit_graph_to_data()
            self._markers_run.add("final_fit")

    def _show_event_marker(self, elapsed_s, label, color, samples):
        if not samples:
            return
        nearest = min(samples, key=lambda row: abs(row["elapsed_s"] - elapsed_s))
        self._add_temperature_marker(elapsed_s, nearest["temperature_c"], label, color)

    def import_settings(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import settings", "", "CSV files (*.csv)")
        if not path:
            return
        try:
            settings = DataManager.import_settings(path)
            self._apply_settings(settings)
        except (OSError, ValueError, KeyError) as exc:
            self.statusBar().showMessage(str(exc), 8000)

    def export_run_data(self):
        if self._historical_record is not None:
            record = self._historical_record
        elif self._last_run_record is not None:
            record = self._last_run_record
        elif self.service.last_run_record is not None:
            record = self.service.last_run_record
        else:
            view, samples = self.service.snapshot()
            if view.running or view.state not in ("complete", "aborted", "fault", "e_stop"):
                self.statusBar().showMessage("A run can be exported after it ends", 5000)
                return
            record = self.service.last_run_record or DataManager.build_run_record(view, samples)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export run",
            f"kiln-run-{stamp}.json", "Kiln run (*.json)")
        if path:
            try:
                saved = self.data.export_run(record, path)
                self.statusBar().showMessage(f"Exported {saved}", 5000)
            except (OSError, ValueError) as exc:
                self.statusBar().showMessage(str(exc), 8000)

    def import_run(self):
        view, _ = self.service.snapshot()
        if view.running:
            self.statusBar().showMessage("Stop the active run before importing a run", 5000)
            return
        sample_path = Path(__file__).with_name("sample_completed_run.json")
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import run", str(sample_path), "Kiln run (*.json)"
        )
        if not path:
            return
        try:
            record = DataManager.import_run(path)
            self._validate_settings(record["settings"])
            self._apply_settings(record["settings"])
            self._historical_record = record
            self._historical_range_pending = True
            self._last_run_record = record
            self._clear_temperature_markers()
            self._markers_run.clear()
            self.refresh()
            self.statusBar().showMessage("Run and settings imported", 5000)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.statusBar().showMessage(str(exc), 8000)

    def _validate_settings(self, settings):
        if not config.MIN_SETPOINT <= settings["target_temperature_c"] <= config.MAX_SETPOINT:
            raise ValueError("Run target is outside the configured temperature limits")
        if not config.HOLD_DURATION_MIN_S <= settings["hold_duration_s"] <= config.HOLD_DURATION_MAX_S:
            raise ValueError("Run hold duration is outside the configured limits")
        if any(not 0.0 <= settings[key] <= 10.0 for key in ("kp", "ki", "kd")):
            raise ValueError("Run PID gains are outside the supported range")

    def _apply_settings(self, settings):
        self._validate_settings(settings)
        view, _ = self.service.snapshot()
        if view.running:
            raise ValueError("Settings cannot be imported during an active run")
        widgets = ((self.target_input, "target_temperature_c"), (self.hold_input, "hold_duration_s"),
                   (self.kp_input, "kp"), (self.ki_input, "ki"), (self.kd_input, "kd"))
        blockers = [QtCore.QSignalBlocker(widget) for widget, _ in widgets]
        for widget, key in widgets:
            widget.setValue(settings[key])
        del blockers
        self.service.command("defaults", settings)

    def export_settings(self):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export settings",
            f"kiln-settings-{stamp}.csv", "CSV files (*.csv)")
        if path:
            try:
                DataManager.export_settings(self._settings(), path)
            except (OSError, ValueError) as exc:
                self.statusBar().showMessage(str(exc), 8000)

    def restore_pid_defaults(self):
        self.kp_input.setValue(config.PID_KP)
        self.ki_input.setValue(config.PID_KI)
        self.kd_input.setValue(config.PID_KD)

    def restore_process_defaults(self):
        self.target_input.setValue(min(45.0, config.MAX_SETPOINT))
        self.hold_input.setValue(60.0)

    def show_run_events(self):
        view, _ = self.service.snapshot()
        events = (self._historical_record or {}).get("events", ()) if self._historical_record else view.events
        if not events:
            text = "No events recorded for this run."
        else:
            lines = []
            for event in events:
                elapsed = event["elapsed_s"]
                line = f"{int(elapsed // 60):02d}:{elapsed % 60:05.2f}  {event['description']}"
                changes = event.get("changes", {})
                if changes:
                    values = ", ".join(
                        f"{key}: {change['from']} -> {change['to']}"
                        for key, change in changes.items()
                    )
                    line += f" ({values})"
                lines.append(line)
            text = "\n".join(lines)
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Run events")
        dialog.resize(680, 460)
        layout = QtWidgets.QVBoxLayout(dialog)
        event_text = QtWidgets.QPlainTextEdit(dialog)
        event_text.setReadOnly(True)
        event_text.setPlainText(text)
        layout.addWidget(event_text)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def closeEvent(self, event):
        self._timer.stop()
        self.service.close()
        super().closeEvent(event)

def main():
    app = QtWidgets.QApplication(sys.argv)

    # TEMPORARY WINDOWS import preview: it starts no service or hardware.
    if sys.platform == "win32":
        window = KilnWindow(_ImportPreviewService())
        window.setWindowTitle("Oven Controller (UI only - no hardware)")
        for toolbar in window.findChildren(QtWidgets.QToolBar):
            for action in toolbar.actions():
                if action.text() != "Import run":
                    action.setEnabled(False)
        window.show()
        return app.exec()

    try:
        service = Application(config)
    except Exception as exc:
        QtWidgets.QMessageBox.critical(None, "Kiln startup failed", str(exc))
        return 1
    app.aboutToQuit.connect(service.close)
    window = KilnWindow(service)
    window.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
