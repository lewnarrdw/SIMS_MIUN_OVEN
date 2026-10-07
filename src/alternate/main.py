"""Raspberry Pi 5 kiln application entry point."""
from __future__ import annotations
import sys
from datetime import datetime
from PySide6 import QtCore, QtWidgets
import config
import gui
from application import Application
from data_manager import DataManager

class KilnWindow(gui.OvenWindow):
    """Thin view adapter; device work stays in Application's worker."""
    def __init__(self, service):
        self.service = service
        self.data = DataManager(config.DATA_DIRECTORY)
        self._markers_run = None
        super().__init__()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.target_input.valueChanged.connect(self._tune)
        self.kp_input.valueChanged.connect(self._tune)
        self.ki_input.valueChanged.connect(self._tune)
        self.kd_input.valueChanged.connect(self._tune)
        self.restore_process_defaults()

    def _settings(self):
        return {"target_temperature_c": self.target_input.value(), "hold_duration_s": self.hold_input.value(),
                "kp": self.kp_input.value(), "ki": self.ki_input.value(), "kd": self.kd_input.value()}

    def _tune(self, *_):
        self.service.command("tune", self._settings())

    def start_process(self):
        self._clear_temperature_markers()
        self._markers_run = None
        self.graph_item.setXRange(0, 120.0, padding=0)
        self.temperature_curve.setData([], [])
        self.duty_curve.setData([], [])
        self.goal_curve.setData([], [])
        self.service.command("start", self._settings())

    def stop_process(self):
        self.service.command("stop")

    def reset_process(self):
        self._clear_temperature_markers()
        self.service.command("reset")

    def refresh(self):
        view, samples = self.service.snapshot()
        if samples:
            times = [row["elapsed_s"] for row in samples]
            self.temperature_curve.setData(times, [row["temperature_c"] for row in samples])
            self.goal_curve.setData(times, [row["target_temperature_c"] for row in samples])
            self.duty_curve.setData(times, [row["heater_output_percent"] for row in samples])
            if times[-1] > 120.0:
                self.graph_item.setXRange(max(0.0, times[-1] - 120.0), times[-1], padding=0)
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
        if view.hold_completed and view.hold_min_c is not None and self._markers_run != "hold":
            self.show_hold_minimum_temperature_marker(view.hold_min_time_s, view.hold_min_c)
            self._markers_run = "hold"
        if view.state == "complete" and view.peak_c is not None and self._markers_run != "complete":
            self.show_peak_temperature_marker(view.peak_time_s, view.peak_c, view.target_c)
            self._markers_run = "complete"

    def import_settings(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import settings", "", "CSV files (*.csv)")
        if not path:
            return
        try:
            settings = DataManager.import_settings(path)
            self.target_input.setValue(settings["target_temperature_c"])
            self.hold_input.setValue(settings["hold_duration_s"])
            self.kp_input.setValue(settings["kp"])
            self.ki_input.setValue(settings["ki"])
            self.kd_input.setValue(settings["kd"])
        except (OSError, ValueError, KeyError) as exc:
            self.statusBar().showMessage(str(exc), 8000)

    def export_run_data(self):
        _, samples = self.service.snapshot()
        if not samples:
            self.statusBar().showMessage("There are no run samples to export", 5000)
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export run data",
            f"kiln-run-{stamp}.csv", "CSV files (*.csv)")
        if path:
            try:
                saved = self.data.export_run(samples, path)
                self.statusBar().showMessage(f"Exported {saved}", 5000)
            except (OSError, ValueError) as exc:
                self.statusBar().showMessage(str(exc), 8000)

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
        QtWidgets.QMessageBox.information(self, "Run status", view.error or view.status)

    def closeEvent(self, event):
        self._timer.stop()
        self.service.close()
        super().closeEvent(event)

def main():
    app = QtWidgets.QApplication(sys.argv)
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




