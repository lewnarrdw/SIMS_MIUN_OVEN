"""Raspberry Pi 5 kiln application entry point."""
from __future__ import annotations
import signal
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from PySide6 import QtCore, QtGui, QtWidgets
import config
import gui
from application import LATCHED_FAULTS, Application, describe_fault
from data_manager import DataManager


def event_severity(event: dict) -> str | None:
    """Return an event's severity; older run files only mark faults and E-Stops by type."""
    severity = event.get("severity")
    if severity is None:
        severity = {"run_faulted": "error", "emergency_stop": "error"}.get(event.get("type"))
    return severity


class KilnWindow(gui.OvenWindow):
    """Thin view adapter; device work stays in Application's worker."""
    def __init__(self, service):
        self.service = service
        self.data = DataManager(config.DATA_DIRECTORY)
        self._markers_run = set()
        self._historical_record = None
        self._historical_range_pending = False
        self._last_run_record = None
        self._attention_events = []
        self._unread_attention = {}
        self._seen_attention = set()
        self._active_startup_reason = None
        self._live_run_started_at = None
        self._stage_detail_shows_fault = False
        super().__init__()
        self._event_log_base_icon = self.event_log_action.icon()
        self._live_graph_x_max = gui.GRAPH_WINDOW_S
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.target_input.valueChanged.connect(self._tune)
        self.kp_input.valueChanged.connect(self._tune)
        self.ki_input.valueChanged.connect(self._tune)
        self.kd_input.valueChanged.connect(self._tune)
        for checkbox, _value_input in self.pid_enable_inputs:
            checkbox.toggled.connect(self._tune)
        for widget in (self.target_input, self.hold_input, self.kp_input, self.ki_input, self.kd_input):
            widget.valueChanged.connect(self._sync_start_settings)
        for checkbox, _value_input in self.pid_enable_inputs:
            checkbox.toggled.connect(self._sync_start_settings)
        # Fullscreen has no title bar, so offer Quit in the File menu and as Ctrl+Q.
        self.quit_action = QtGui.QAction("Quit", self)
        self.quit_action.setShortcut(QtGui.QKeySequence("Ctrl+Q"))
        self.quit_action.setShortcutContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
        self.quit_action.triggered.connect(self.request_exit)
        self.addAction(self.quit_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.quit_action)
        self.restore_pid_defaults()
        self.restore_process_defaults()
        self._sync_start_settings()

    def _settings(self):
        return {"target_temperature_c": self.target_input.value(), "hold_duration_s": self.hold_input.value(),
                "kp": self.kp_input.value() if self.kp_enable.isChecked() else 0.0,
                "ki": self.ki_input.value() if self.ki_enable.isChecked() else 0.0,
                "kd": self.kd_input.value() if self.kd_enable.isChecked() else 0.0}

    def _tune(self, *_):
        if self._historical_record is None:
            self.service.command("tune", self._settings())

    def _sync_start_settings(self, *_):
        self.service.command("settings", self._settings())

    # Toolbar and manual-menu buttons queue the same commands as the physical
    # buttons (application.OPERATOR_BUTTON_COMMANDS); any view updates happen in
    # refresh() so both input paths behave identically.
    def start_process(self):
        self.service.command("start")

    def stop_process(self):
        self.service.command("stop")

    def manual_command(self, name):
        self.service.command(name)

    def _update_manual_actions(self, view):
        """Grey out manual controls the worker would reject in the live state."""
        door_moving = getattr(view, "door_motor", "idle") in ("opening", "closing")
        door_enabled = not view.running and not door_moving
        enabled = {
            "Open door": door_enabled,
            "Close door": door_enabled,
            "Heater on": not view.running and view.state not in ("fault", "e_stop"),
        }
        for label, action in self.manual_actions.items():
            action.setEnabled(enabled.get(label, True))

    def _begin_live_run(self, view):
        """Clear the graph for a new run, however it was started."""
        self._historical_record = None
        self._last_run_record = None
        self._clear_temperature_markers()
        self._markers_run.clear()
        hold_duration_s = (view.run_settings or {}).get("hold_duration_s", self.hold_input.value())
        self._live_graph_x_max = max(
            gui.GRAPH_WINDOW_S,
            hold_duration_s + gui.GRAPH_INITIAL_BUFFER_S,
        )
        self._set_graph_time_extent(self._live_graph_x_max)
        self._set_graph_x_range(self._live_graph_x_max)
        self.temperature_curve.setData([], [])
        self.duty_curve.setData([], [])
        self.goal_curve.setData([], [])
        self.planned_curve.setData([], [])

    def reset_process(self):
        self._historical_record = None
        self._clear_temperature_markers()
        self._markers_run.clear()
        self.service.command("reset")
        self._update_profile_preview()

    def refresh(self):
        live_view, live_samples = self.service.snapshot()
        self.set_emergency_stop(getattr(live_view, "estop_active", False))
        self._update_manual_actions(live_view)
        run_started_at = getattr(live_view, "run_started_at", None)
        if run_started_at is not None and run_started_at != self._live_run_started_at:
            self._live_run_started_at = run_started_at
            self._begin_live_run(live_view)
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
                hold_elapsed_s=result.get("hold_elapsed_s", 0.0),
                run_settings=record["settings"],
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
                self._set_graph_x_range(self._live_graph_x_max)
        bounded_extent_s = max(
            time_extent_s,
            self._live_graph_x_max if self._historical_record is None else 0.0,
        )
        self._set_graph_time_extent(bounded_extent_s)
        if samples:
            times = [row["elapsed_s"] for row in samples]
            self.temperature_curve.setData(times, [row["temperature_c"] for row in samples])
            x_min, x_max = self.graph_item.vb.viewRange()[0]
            target_c = samples[-1]["target_temperature_c"]
            self.goal_curve.setData([x_min, x_max], [target_c, target_c])
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
        state = view.state
        self.run_action.setText("Run")
        self.run_action.setToolTip("Start a new run")
        progress_stage = {"ready": 0, "heating": 1, "holding": 2, "cooling": 3,
                          "complete": 4}.get(state, -1)
        if state == "aborted":
            progress_stage = max(1, min(3, getattr(view, "stop_stage", None) or 1))
        if state == "aborted" and getattr(view, "stop_state", None) == "door_moving":
            progress_stage = -1
        self.progress.set_progress(progress_stage, view.fraction, view.detail)
        titles = {"ready": "Ready to run", "door_moving": "Closing door", "heating": "Heating",
                  "holding": "Holding", "cooling": "Cooling down",
                  "complete": "Complete", "aborted": "Stopped", "fault": "Fault", "e_stop": "Emergency stop"}
        opening_door = (state == "cooling" and
                        getattr(view, "current_temperature_c", None) is not None and
                        view.current_temperature_c <= config.DOOR_SAFE_TEMPERATURE and view.door != "open")
        self.stage_label.setText("Current stage: " + ("Opening door" if opening_door else titles.get(state, view.status)))
        temperature = getattr(view, "current_temperature_c", None)
        if temperature is None and samples:
            temperature = samples[-1]["temperature_c"]

        stage_detail = ""
        if state in ("heating", "holding", "cooling") and len(samples) >= 2:
            stage_names = {"heating": "Warm up started", "holding": "Hold started",
                           "cooling": "Cool down started"}
            starts = [event["elapsed_s"] for event in view.events
                      if event.get("description") == stage_names[state]]
            stage_start = starts[-1] if starts else samples[0]["elapsed_s"]
            stage_samples = [row for row in samples if row["elapsed_s"] >= stage_start]
            if state in ("heating", "cooling") and len(stage_samples) >= 2:
                recent = [row for row in stage_samples if row["elapsed_s"] >= stage_samples[-1]["elapsed_s"] - 30.0]
                if len(recent) >= 2:
                    mean_time = sum(row["elapsed_s"] for row in recent) / len(recent)
                    mean_temp = sum(row["temperature_c"] for row in recent) / len(recent)
                    variance = sum((row["elapsed_s"] - mean_time) ** 2 for row in recent)
                    slope = (sum((row["elapsed_s"] - mean_time) * (row["temperature_c"] - mean_temp)
                                 for row in recent) / variance * 60.0) if variance else 0.0
                    rate = slope if state == "heating" else -slope
                    stage_detail = (f"Heating rate: {rate:+.1f} °C/min" if state == "heating"
                                    else f"Cooling rate: {rate:.1f} °C/min")
            elif state == "holding" and stage_samples:
                errors = [abs(row["temperature_c"] - row["target_temperature_c"]) for row in stage_samples]
                stage_detail = f"Average error: {sum(errors) / len(errors):.2f} °C"
        show_fault = state == "fault" and bool(view.detail)
        if show_fault:
            next_step = "Fix it, then press Reset." if view.detail in LATCHED_FAULTS else "Fix it to continue."
            stage_detail = f"Cause: {describe_fault(view.detail)}. {next_step}"
        if show_fault != self._stage_detail_shows_fault:
            self._stage_detail_shows_fault = show_fault
            self.stage_detail_label.setStyleSheet(
                f"color: {'#ff8a80' if show_fault else '#9aa1ab'}; font-size: 9pt;"
            )
        self.stage_detail_label.setText(stage_detail)

        minutes, seconds = divmod(view.elapsed_s, 60.0)
        self.elapsed_label.setText(f"Elapsed: {int(minutes):02d}:{seconds:04.1f}")
        output = getattr(view, "output_percent", None)
        self.temperature_label.setText("N/A" if temperature is None else f"{temperature:.1f} °C")
        self.output_label.setText("N/A" if output is None else f"{output:.0f} %")
        label_target_c = view.target_c
        start_target_c = getattr(view, "start_target_c", None)
        if self._historical_record is None and not view.running and start_target_c is not None:
            # Between live runs the controller keeps its last target; show the next run's.
            label_target_c = start_target_c
        self.target_status_label.setText(f"Target {label_target_c:.1f} °C")
        hold_elapsed = getattr(view, "hold_elapsed_s", 0.0) or 0.0
        hold_remaining = max(0.0, self.hold_input.value() - hold_elapsed)
        hold_minutes, hold_seconds = divmod(hold_remaining, 60.0)
        self.hold_remaining_label.setText(
            f"{int(hold_minutes):02d}:{int(hold_seconds):02d}"
        )
        door_text = "N/A" if not view.door or view.door == "unknown" else view.door.title()
        self.door_label.setText(f"Door: {door_text}")
        color = "#4caf70" if view.door == "closed" else "#f4b942"
        self.door_label.setStyleSheet(f"color: {color};")
        for widget in (self.target_input, self.hold_input):
            widget.setEnabled(not view.running)
        if view.error:
            self.statusBar().showMessage(view.error, 8000)
        self._update_attention_badge(view)
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
        self.show_hold_window(gui.hold_window_span(
            view.state, view.hold_reached_s, getattr(view, "hold_elapsed_s", 0.0), view.elapsed_s,
        ))
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
        enable_widgets = (
            (self.kp_enable, "kp", self.kp_input),
            (self.ki_enable, "ki", self.ki_input),
            (self.kd_enable, "kd", self.kd_input),
        )
        blockers = [QtCore.QSignalBlocker(widget) for widget, _ in widgets]
        blockers.extend(QtCore.QSignalBlocker(checkbox) for checkbox, _key, _input in enable_widgets)
        for widget, key in widgets:
            widget.setValue(settings[key])
        for checkbox, key, input_widget in enable_widgets:
            checkbox.setChecked(settings[key] != 0.0)
            input_widget.setEnabled(settings[key] != 0.0)
        del blockers
        self.service.command("defaults", self._settings())

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
        for checkbox, value_input, default in (
            (self.kp_enable, self.kp_input, config.PID_KP),
            (self.ki_enable, self.ki_input, config.PID_KI),
            (self.kd_enable, self.kd_input, config.PID_KD),
        ):
            value_input.setValue(default)
            checkbox.setChecked(default != 0.0)

    def restore_process_defaults(self):
        self.target_input.setValue(min(45.0, config.MAX_SETPOINT))
        self.hold_input.setValue(60.0)

    def show_run_events(self):
        view, _ = self.service.snapshot()
        events = (self._historical_record or {}).get("events", ()) if self._historical_record else view.events
        events = list(events) + [event for event in self._attention_events
                                 if event.get("type") == "attention"]
        if not events:
            text = "No events recorded for this run."
        else:
            lines = []
            for event in events:
                elapsed = event["elapsed_s"]
                severity = event_severity(event)
                tag = f"[{severity.upper()}] " if severity else ""
                line = f"{int(elapsed // 60):02d}:{elapsed % 60:05.2f}  {tag}{event['description']}"
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
        self._unread_attention.clear()
        self._update_attention_badge(view)
        dialog.exec()

    def _update_attention_badge(self, view):
        def add_attention(key, severity, description, elapsed_s=0.0, event_type="attention"):
            if key in self._seen_attention:
                return
            self._seen_attention.add(key)
            event = {"elapsed_s": elapsed_s, "type": event_type,
                     "description": description, "severity": severity}
            self._attention_events.append(event)
            self._unread_attention[key] = event

        blocked = (getattr(view, "state", "ready") in ("ready", "complete", "aborted", "fault", "e_stop")
                   and not getattr(view, "can_start", True))
        if blocked:
            reason = getattr(view, "ready_reason", "Process cannot start")
            severity = "error" if any(word in reason.lower() for word in
                                        ("fault", "invalid", "above", "emergency", "error")) else "warning"
            if reason != self._active_startup_reason:
                add_attention(("startup", len(self._attention_events), reason), severity,
                              f"Cannot start: {reason}")
            self._active_startup_reason = reason
        else:
            self._active_startup_reason = None
        run_key = getattr(view, "run_started_at", None) or "current"
        for event in (() if self._historical_record is not None else getattr(view, "events", ())):
            event_type = event.get("type", "")
            severity = event_severity(event)
            if severity:
                key = (run_key, event.get("elapsed_s"), event_type, event.get("description"))
                add_attention(key, severity, event.get("description", "Attention event"),
                              event.get("elapsed_s", 0.0), event_type)
        self._refresh_event_log_icon()

    def _refresh_event_log_icon(self):
        color = None
        if self._unread_attention:
            color = "#e34b4b" if any(item["severity"] == "error"
                                     for item in self._unread_attention.values()) else "#f4c542"
        pixmap = self._event_log_base_icon.pixmap(QtCore.QSize(24, 24))
        if color:
            painter = QtGui.QPainter(pixmap)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            painter.setPen(QtGui.QPen(QtGui.QColor("#252b31"), 1.5))
            painter.setBrush(QtGui.QColor(color))
            painter.drawEllipse(QtCore.QPointF(20, 4), 4.0, 4.0)
            painter.end()
        self.event_log_action.setIcon(QtGui.QIcon(pixmap))

    def request_exit(self):
        """Exit at once when idle; during a run, ask before stopping it."""
        view, _samples = self.service.snapshot()
        if view.running and not self._confirm_exit_during_run():
            return
        self.close()

    def _confirm_exit_during_run(self):
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Exit during a run")
        box.setText("A run is in progress.")
        box.setInformativeText(
            "Exiting stops the run and turns the heater off. The data recorded so far is saved."
        )
        exit_button = box.addButton("Stop run and exit", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        keep_button = box.addButton("Keep running", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        exit_button.setStyleSheet(
            "QPushButton { background: #b8423a; border-color: #c6413a; color: white; font-weight: 600; }"
            "QPushButton:hover { background: #c6413a; }"
        )
        box.setDefaultButton(keep_button)
        box.setEscapeButton(keep_button)
        box.exec()
        return box.clickedButton() is exit_button

    def closeEvent(self, event):
        self._timer.stop()
        try:
            self.service.close()
        except RuntimeError as exc:
            # The heater has already been forced off; report it and close anyway.
            sys.stderr.write(f"Shutdown: {exc}\n")
        super().closeEvent(event)

def _qt_message_handler(_mode, _context, message):
    # Qt platform plugins without size-hint support warn whenever the window's
    # minimum size changes; the warning is harmless.
    if "propagateSizeHints" in message:
        return
    sys.stderr.write(message + "\n")

def main():
    QtCore.qInstallMessageHandler(_qt_message_handler)
    app = QtWidgets.QApplication(sys.argv)
    try:
        service = Application(config)
    except Exception as exc:
        QtWidgets.QMessageBox.critical(None, "Kiln startup failed", str(exc))
        return 1
    app.aboutToQuit.connect(service.close)
    window = KilnWindow(service)
    window.showFullScreen()
    # SIGTERM (pkill over SSH) and SIGINT (Ctrl+C) close the window like Quit,
    # so the worker's shutdown turns the heater off and stops the motor.
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signal_number, lambda *_: QtCore.QTimer.singleShot(0, window.close))
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
