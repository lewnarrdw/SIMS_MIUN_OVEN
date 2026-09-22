"""Desktop simulator for a Raspberry Pi oven controller."""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets


UPDATE_INTERVAL_MS = 200
SAFE_TEMPERATURE_C = 25.0
AMBIENT_TEMPERATURE_C = 22.0
MAX_TEMPERATURE_C = 80.0
MAX_OUTPUT_PERCENT = 100.0
MAX_DURATION_S = 120.0
CONTROL_TOLERANCE_C = 0.1
PID_OUTPUT_MIN = 0.0
PID_OUTPUT_MAX = 1.0
PID_INTEGRAL_LIMIT = 0.8
PID_ANTI_WINDUP_GAIN = 2.0
PID_DERIVATIVE_FILTER = 0.2


class StageProgress(QtWidgets.QWidget):
    """A vertical connected-node display for the oven-process stages."""

    stages = ("Initialised", "Warm-up", "Hold", "Cool-down")

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_stage = -1
        self.fraction = 0.0
        self.setMinimumWidth(180)
        self.setMinimumHeight(290)

    def set_progress(self, current_stage: int, fraction: float = 0.0) -> None:
        self.current_stage = current_stage
        self.fraction = max(0.0, min(1.0, fraction))
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        radius = 14
        left = 28
        top = 36
        bottom = self.height() - 36
        spacing = (bottom - top) / (len(self.stages) - 1)
        positions = [QtCore.QPointF(left, top + index * spacing) for index in range(len(self.stages))]

        connector_pen = QtGui.QPen(QtGui.QColor("#59636d"), 5, QtCore.Qt.PenStyle.SolidLine)
        complete_pen = QtGui.QPen(QtGui.QColor("#4caf70"), 5, QtCore.Qt.PenStyle.SolidLine)
        for index, (first, second) in enumerate(zip(positions, positions[1:])):
            painter.setPen(connector_pen)
            painter.drawLine(first, second)
            if self.current_stage > index + 1:
                painter.setPen(complete_pen)
                painter.drawLine(first, second)
            elif self.current_stage == index + 1:
                # Begin outside the completed node so a new fill is visible immediately.
                segment_start = QtCore.QPointF(first.x(), first.y() + radius)
                segment_end = QtCore.QPointF(second.x(), second.y() - radius)
                end = QtCore.QPointF(
                    segment_start.x() + (segment_end.x() - segment_start.x()) * self.fraction,
                    segment_start.y() + (segment_end.y() - segment_start.y()) * self.fraction,
                )
                painter.setPen(complete_pen)
                painter.drawLine(segment_start, end)
        for index, (stage, point) in enumerate(zip(self.stages, positions)):
            completed = index < self.current_stage
            active = index == self.current_stage
            fill = QtGui.QColor("#4caf70") if completed else QtGui.QColor("#39434d")
            painter.setPen(QtGui.QPen(QtGui.QColor("#d5dce2"), 2))
            painter.setBrush(fill)
            painter.drawEllipse(point, radius, radius)

            painter.setPen(QtGui.QColor("#e7edf2"))
            font = painter.font()
            font.setBold(active or completed)
            painter.setFont(font)
            painter.drawText(QtCore.QPointF(left + 30, point.y() + 5), stage)


class OvenWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Oven Controller - Simulator")
        self.resize(1280, 760)

        self.running = False
        self.stage = -1
        self.hold_elapsed_s = 0.0
        self.elapsed_s = 0.0
        self.temperature_c = AMBIENT_TEMPERATURE_C
        self.duty_cycle = 0.0
        self.integral = 0.0
        self.previous_error = 0.0
        self.previous_temperature = AMBIENT_TEMPERATURE_C
        self.filtered_derivative = 0.0
        self.times: deque[float] = deque(maxlen=900)
        self.temperatures: deque[float] = deque(maxlen=900)
        self.duties: deque[float] = deque(maxlen=900)
        self.goals: deque[float] = deque(maxlen=900)

        self._build_toolbar()
        self._build_layout()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_simulation)
        self.timer.start(UPDATE_INTERVAL_MS)
        self._update_status()

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Process")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for label, callback in (
            ("Run", self.start_process),
            ("Stop", self.stop_process),
            ("Reset", self.reset_process),
            ("Import", self.import_settings),
            ("Export", self.export_settings),
        ):
            action = QtGui.QAction(label, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)

    def _build_layout(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.graph = pg.PlotWidget()
        self.graph.setBackground("#20252b")
        self.graph.showGrid(x=True, y=True, alpha=0.32)
        self.graph.setLabel("left", "Temperature (C)", **{"color": "#f4f7fa", "font-size": "16pt", "font-weight": "bold"})
        self.graph.setLabel("bottom", "Elapsed time (s)", **{"color": "#f4f7fa", "font-size": "16pt", "font-weight": "bold"})
        self.legend = self.graph.addLegend(offset=(12, 12))
        self.legend.setBrush(pg.mkBrush("#2a3139"))
        self.legend.setPen(pg.mkPen("#71808d"))
        self.graph_item = self.graph.getPlotItem()
        self.graph_item.showAxis("right")
        self.graph_item.setLabel("right", "Heater output (%)", **{"color": "#8fc5f5", "font-size": "16pt", "font-weight": "bold"})
        axis_font = QtGui.QFont()
        axis_font.setPointSize(10)
        for axis_name in ("left", "bottom", "top"):
            axis = self.graph_item.getAxis(axis_name)
            axis.setPen(pg.mkPen("#d9e1e8"))
            axis.setTextPen(pg.mkPen("#d9e1e8"))
            axis.setStyle(tickFont=axis_font, tickTextOffset=8)
        right_axis = self.graph_item.getAxis("right")
        right_axis.setPen(pg.mkPen("#63a8e8"))
        right_axis.setTextPen(pg.mkPen("#63a8e8"))
        right_axis.setStyle(tickFont=axis_font, tickTextOffset=8)
        self.graph_item.vb.setLimits(xMin=0, xMax=MAX_DURATION_S, minXRange=5, maxXRange=MAX_DURATION_S, yMin=0, yMax=MAX_TEMPERATURE_C, minYRange=5, maxYRange=MAX_TEMPERATURE_C)
        self.graph_item.setXRange(0, MAX_DURATION_S, padding=0)
        self.graph_item.setYRange(0, MAX_TEMPERATURE_C, padding=0)
        self.duty_view = pg.ViewBox()
        self.duty_view.setLimits(xMin=0, xMax=MAX_DURATION_S, minXRange=5, maxXRange=MAX_DURATION_S, yMin=0, yMax=MAX_OUTPUT_PERCENT, minYRange=5, maxYRange=MAX_OUTPUT_PERCENT)
        self.duty_view.setYRange(0, MAX_OUTPUT_PERCENT, padding=0)
        self.graph_item.scene().addItem(self.duty_view)
        right_axis.linkToView(self.duty_view)
        self.duty_view.setXLink(self.graph_item)
        self.graph_item.vb.sigResized.connect(self._resize_duty_view)
        self.temperature_curve = self.graph.plot(name="Temperature", pen=pg.mkPen("#ff7566", width=3))
        self.goal_curve = self.graph.plot(name="Goal", pen=pg.mkPen("#ffd166", width=3, style=QtCore.Qt.PenStyle.DashLine))
        self.goal_curve.setVisible(False)
        self.duty_curve = pg.PlotCurveItem(pen=pg.mkPen("#63a8e8", width=3))
        self.duty_view.addItem(self.duty_curve)
        self.legend.addItem(self.duty_curve, "Duty cycle")
        layout.addWidget(self.graph, 1)

        side = QtWidgets.QVBoxLayout()
        side.setSpacing(12)
        layout.addLayout(side)

        controls_box = QtWidgets.QGroupBox("Controls")
        controls = QtWidgets.QFormLayout(controls_box)
        self.target_input = self._spin(30.0, 60.0, 60.0, " °C")
        self.kp_input = self._spin(0.0, 10.0, 0.08)
        self.ki_input = self._spin(0.0, 10.0, 0.006)
        self.kd_input = self._spin(0.0, 10.0, 0.02)
        self.hold_input = self._spin(1.0, MAX_DURATION_S, 120.0, " s", decimals=0)
        controls.addRow("Target temperature", self.target_input)
        controls.addRow(QtWidgets.QLabel("<span style='font-size: 16pt;'>K<sub>p</sub></span>"), self.kp_input)
        controls.addRow(QtWidgets.QLabel("<span style='font-size: 16pt;'>K<sub>i</sub></span>"), self.ki_input)
        controls.addRow(QtWidgets.QLabel("<span style='font-size: 16pt;'>K<sub>d</sub></span>"), self.kd_input)
        controls.addRow("Hold duration", self.hold_input)
        side.addWidget(controls_box)

        status_box = QtWidgets.QGroupBox("Process status")
        status = QtWidgets.QVBoxLayout(status_box)
        self.progress = StageProgress()
        status.addWidget(self.progress, 1)
        self.stage_label = QtWidgets.QLabel()
        self.door_label = QtWidgets.QLabel("Door: Unknown (simulator; not implemented)")
        self.door_label.setStyleSheet("color: #f4b942;")
        self.temperature_label = QtWidgets.QLabel()
        status.addWidget(self.stage_label)
        status.addWidget(self.temperature_label)
        status.addWidget(self.door_label)
        side.addWidget(status_box, 1)

    @staticmethod
    def _spin(minimum: float, maximum: float, value: float, suffix: str = "", decimals: int = 3) -> QtWidgets.QDoubleSpinBox:
        control = QtWidgets.QDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setValue(value)
        control.setDecimals(decimals)
        control.setSingleStep(1.0 if decimals == 0 else 0.01)
        control.setSuffix(suffix)
        return control

    def _resize_duty_view(self) -> None:
        self.duty_view.setGeometry(self.graph_item.vb.sceneBoundingRect())
        self.duty_view.linkedViewChanged(self.graph_item.vb, self.duty_view.XAxis)

    def _reset_graph_view(self) -> None:
        self.graph_item.setXRange(0, MAX_DURATION_S, padding=0)
        self.graph_item.setYRange(0, MAX_TEMPERATURE_C, padding=0)
        self.duty_view.setYRange(0, MAX_OUTPUT_PERCENT, padding=0)
    def start_process(self) -> None:
        if not self.running:
            self.running = True
            self.stage = 1
            self.hold_elapsed_s = 0.0
            self.integral = 0.0
            self.previous_error = 0.0
            self.previous_temperature = self.temperature_c
            self.filtered_derivative = 0.0
            self.goal_curve.setVisible(True)
            self._set_goal_line()
            self._update_status()
    def stop_process(self) -> None:
        self.running = False
        self.duty_cycle = 0.0
        self.goal_curve.setVisible(False)
        self.goal_curve.setData([], [])
        self._update_status()
    def reset_process(self) -> None:
        self.stop_process()
        self.stage = -1
        self.hold_elapsed_s = 0.0
        self.elapsed_s = 0.0
        self.temperature_c = AMBIENT_TEMPERATURE_C
        self.times.clear()
        self.temperatures.clear()
        self.duties.clear()
        self.goals.clear()
        self._redraw_graph()
        self._reset_graph_view()
        self._update_status()

    def _set_goal_line(self) -> None:
        goal = self.target_input.value() if self.stage in (1, 2) else SAFE_TEMPERATURE_C
        x = np.linspace(0.0, MAX_DURATION_S, 241)
        self.goal_curve.setData(x, np.full_like(x, goal))

    def _calculate_duty_cycle(self, goal: float, dt: float) -> float:
        """Calculate a bounded PID output with anti-windup safeguards."""
        error = goal - self.temperature_c
        # Derivative-on-measurement avoids a large kick when the target changes.
        measured_rate = (self.temperature_c - self.previous_temperature) / dt
        self.filtered_derivative += (measured_rate - self.filtered_derivative) * PID_DERIVATIVE_FILTER
        derivative_term = -self.kd_input.value() * self.filtered_derivative
        proportional_term = self.kp_input.value() * error
        unsaturated = proportional_term + self.integral + derivative_term
        output = max(PID_OUTPUT_MIN, min(PID_OUTPUT_MAX, unsaturated))

        # Back-calculation unwinds the integral whenever the output saturates.
        self.integral += (
            self.ki_input.value() * error
            + PID_ANTI_WINDUP_GAIN * (output - unsaturated)
        ) * dt
        self.integral = max(-PID_INTEGRAL_LIMIT, min(PID_INTEGRAL_LIMIT, self.integral))
        self.previous_error = error
        self.previous_temperature = self.temperature_c

        # Recalculate after the guarded integral update and clamp once more.
        return max(
            PID_OUTPUT_MIN,
            min(PID_OUTPUT_MAX, proportional_term + self.integral + derivative_term),
        )

    def update_simulation(self) -> None:
        dt = UPDATE_INTERVAL_MS / 1000
        goal = self.target_input.value() if self.stage in (1, 2) else SAFE_TEMPERATURE_C
        if self.running:
            if self.stage in (1, 2):
                self.duty_cycle = self._calculate_duty_cycle(goal, dt)
            else:
                self.duty_cycle = 0.0
                self.integral = 0.0
                self.previous_temperature = self.temperature_c

            heat_gain = 2.8 * self.duty_cycle
            cooling_loss = 0.035 * (self.temperature_c - AMBIENT_TEMPERATURE_C)
            self.temperature_c += (heat_gain - cooling_loss) * dt
            self.elapsed_s += dt
            if self.stage == 1 and self.temperature_c >= self.target_input.value() - CONTROL_TOLERANCE_C:
                self.stage = 2
                self.hold_elapsed_s = 0.0
            elif self.stage == 2:
                if abs(self.temperature_c - self.target_input.value()) <= CONTROL_TOLERANCE_C:
                    self.hold_elapsed_s += dt
                else:
                    # The hold requirement is continuous: leaving the band restarts it.
                    self.hold_elapsed_s = 0.0
                if self.hold_elapsed_s >= self.hold_input.value():
                    self.stage = 3
            elif self.stage == 3 and self.temperature_c <= SAFE_TEMPERATURE_C:
                self.stage = 4
                self.running = False
                self.duty_cycle = 0.0

        self.times.append(self.elapsed_s)
        self.temperatures.append(self.temperature_c)
        self.duties.append(self.duty_cycle * 100)
        self.goals.append(goal)
        self._redraw_graph()
        self._update_status()
    def _redraw_graph(self) -> None:
        x = np.fromiter(self.times, dtype=float)
        self.temperature_curve.setData(x, np.fromiter(self.temperatures, dtype=float))
        if self.running:
            self._set_goal_line()
        else:
            self.goal_curve.setVisible(False)
            self.goal_curve.setData([], [])
        self.duty_curve.setData(x, np.fromiter(self.duties, dtype=float))
        self.duty_view.setYRange(0, MAX_OUTPUT_PERCENT, padding=0)

    def _update_status(self) -> None:
        fractions = {1: min(1.0, max(0.0, (self.temperature_c - AMBIENT_TEMPERATURE_C) / max(1.0, self.target_input.value() - AMBIENT_TEMPERATURE_C))), 2: min(1.0, self.hold_elapsed_s / max(1.0, self.hold_input.value())), 3: min(1.0, max(0.0, (self.target_input.value() - self.temperature_c) / max(1.0, self.target_input.value() - SAFE_TEMPERATURE_C)))}
        self.progress.set_progress(self.stage, fractions.get(self.stage, 1.0 if self.stage == 4 else 0.0))
        names = { -1: "Ready", 1: "Warm-up", 2: "Holding temperature", 3: "Cooling down", 4: "Complete" }
        self.stage_label.setText(f"Stage: {names[self.stage]}")
        self.temperature_label.setText(f"Temperature: {self.temperature_c:.1f} °C    Output: {self.duty_cycle * 100:.0f}%")

    def export_settings(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export settings", "oven-settings.json", "JSON files (*.json)")
        if path:
            values = {"target_temperature_c": self.target_input.value(), "kp": self.kp_input.value(), "ki": self.ki_input.value(), "kd": self.kd_input.value(), "hold_duration_s": self.hold_input.value()}
            Path(path).write_text(json.dumps(values, indent=2), encoding="utf-8")

    def import_settings(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import settings", "", "JSON files (*.json)")
        if path:
            values = json.loads(Path(path).read_text(encoding="utf-8"))
            self.target_input.setValue(values["target_temperature_c"])
            self.kp_input.setValue(values["kp"])
            self.ki_input.setValue(values["ki"])
            self.kd_input.setValue(values["kd"])
            self.hold_input.setValue(values["hold_duration_s"])


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    window = OvenWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

