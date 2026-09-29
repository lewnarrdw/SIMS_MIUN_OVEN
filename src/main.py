"""Desktop simulator for a Raspberry Pi oven controller."""

from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime
from collections import deque
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets


UPDATE_INTERVAL_MS = 200
SAFE_TEMPERATURE_C = 25.0
AMBIENT_TEMPERATURE_C = 22.0
MAX_TEMPERATURE_DISPLAY_C = 65.0
MAX_OUTPUT_PERCENT = 100.0
MAX_DURATION_S = 120.0
DEFAULT_TARGET_TEMPERATURE_C = 45.0
DEFAULT_HOLD_DURATION_S = 60.0
DEFAULT_KP = 0.10
DEFAULT_KI = 0.006
DEFAULT_KD = 0.05
GRAPH_WINDOW_S = 120.0
CONTROL_TOLERANCE_C = 0.1
PID_OUTPUT_MIN = 0.0
PID_OUTPUT_MAX = 1.0
PID_INTEGRAL_LIMIT = 0.8
PID_ANTI_WINDUP_GAIN = 2.0
PID_DERIVATIVE_FILTER = 0.2


class StageProgress(QtWidgets.QWidget):
    """A vertical connected-node display for the oven-process stages."""

    stages = ("Ready", "Warming up", "Holding", "Cooling down")

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_stage = -1
        self.fraction = 0.0
        self.setMinimumWidth(180)
        self.setMinimumHeight(290)

    def set_progress(self, current_stage: int, fraction: float = 0.0, detail: str = "") -> None:
        self.current_stage = current_stage
        self.fraction = max(0.0, min(1.0, fraction))
        self.progress_detail = detail
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
        base_font = painter.font()

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
            if completed:
                fill = QtGui.QColor("#4caf70")
                node_pen = QtGui.QPen(QtGui.QColor("#76d39a"), 2)
            elif active:
                fill = QtGui.QColor("#d99a2b")
                node_pen = QtGui.QPen(QtGui.QColor("#ffd166"), 3)
            else:
                fill = QtGui.QColor("#39434d")
                node_pen = QtGui.QPen(QtGui.QColor("#59636d"), 2)
            painter.setPen(node_pen)
            painter.setBrush(fill)
            painter.drawEllipse(point, radius, radius)

            if completed:
                check_pen = QtGui.QPen(QtGui.QColor("#f4f7fa"), 2.5, QtCore.Qt.PenStyle.SolidLine)
                check_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
                painter.setPen(check_pen)
                painter.drawLine(QtCore.QPointF(point.x() - 6, point.y()), QtCore.QPointF(point.x() - 1, point.y() + 5))
                painter.drawLine(QtCore.QPointF(point.x() - 1, point.y() + 5), QtCore.QPointF(point.x() + 7, point.y() - 6))

            painter.setPen(QtGui.QColor("#e7edf2"))
            font = QtGui.QFont(base_font)
            font.setBold(active or completed)
            painter.setFont(font)
            label_x = left + 30
            painter.drawText(QtCore.QPointF(label_x, point.y() + 5), stage)
            if active and self.progress_detail:
                detail_font = painter.font()
                detail_font.setBold(False)
                detail_font.setPointSize(max(8, detail_font.pointSize() - 1))
                painter.setFont(detail_font)
                painter.setPen(QtGui.QColor("#cbd5df"))
                detail_rect = QtCore.QRectF(
                    label_x,
                    point.y() + 8,
                    max(1, self.width() - label_x - 8),
                    spacing - 10,
                )
                detail_text = painter.fontMetrics().elidedText(
                    self.progress_detail,
                    QtCore.Qt.TextElideMode.ElideRight,
                    int(detail_rect.width()),
                )
                painter.drawText(
                    detail_rect,
                    QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.TextFlag.TextSingleLine,
                    detail_text,
                )


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
        self.graph_fitted_at_completion = False
        self.aborted = False
        self.run_exported = False
        self.times: deque[float] = deque()
        self.temperatures: deque[float] = deque()
        self.duties: deque[float] = deque()
        self.goals: deque[float] = deque()

        self._build_toolbar()
        self._build_layout()
        self._redraw_graph()
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
            ("Export run", self.export_run_data),
            ("Export settings", self.export_settings),
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
        self.graph_item.vb.setLimits(xMin=0, xMax=GRAPH_WINDOW_S, minXRange=5, maxXRange=GRAPH_WINDOW_S, yMin=0, yMax=MAX_TEMPERATURE_DISPLAY_C, minYRange=5, maxYRange=MAX_TEMPERATURE_DISPLAY_C)
        self.graph_item.setXRange(0, GRAPH_WINDOW_S, padding=0)
        self.graph_item.setYRange(0, MAX_TEMPERATURE_DISPLAY_C, padding=0)
        self.duty_view = pg.ViewBox()
        self.duty_view.setLimits(xMin=0, xMax=GRAPH_WINDOW_S, minXRange=5, maxXRange=GRAPH_WINDOW_S, yMin=0, yMax=MAX_OUTPUT_PERCENT, minYRange=5, maxYRange=MAX_OUTPUT_PERCENT)
        self.duty_view.setYRange(0, MAX_OUTPUT_PERCENT, padding=0)
        self.graph_item.scene().addItem(self.duty_view)
        right_axis.linkToView(self.duty_view)
        self.duty_view.setXLink(self.graph_item)
        self.graph_item.vb.sigResized.connect(self._resize_duty_view)
        self.temperature_curve = self.graph.plot(name="Temperature", pen=pg.mkPen("#ff7566", width=3))
        self.goal_curve = self.graph.plot(name="Goal", pen=pg.mkPen((255, 209, 102, 160), width=3, style=QtCore.Qt.PenStyle.DashLine))
        self.peak_marker = pg.ScatterPlotItem(size=12, brush=pg.mkBrush("#fff2a8"), pen=pg.mkPen("#20252b", width=2))
        self.peak_label = pg.TextItem(color="#fff2a8", anchor=(0, 1), fill=pg.mkBrush(32, 37, 43, 210))
        self.graph_item.addItem(self.peak_marker)
        self.graph_item.addItem(self.peak_label)
        self.peak_marker.hide()
        self.peak_label.hide()
        self.duty_curve = pg.PlotCurveItem(pen=pg.mkPen("#63a8e8", width=3))
        self.duty_view.addItem(self.duty_curve)
        self.legend.addItem(self.duty_curve, "Duty cycle")
        layout.addWidget(self.graph, 1)

        side = QtWidgets.QVBoxLayout()
        side.setSpacing(12)
        layout.addLayout(side)

        controls_box = QtWidgets.QGroupBox("Controls")
        controls = QtWidgets.QFormLayout(controls_box)
        self.target_input = self._spin(30.0, 60.0, 45.0, " °C", decimals=1)
        self.target_input.setSingleStep(0.1)
        self.kp_input = self._spin(0.0, 10.0, DEFAULT_KP, decimals=2, step=0.01)
        self.ki_input = self._spin(0.0, 10.0, DEFAULT_KI, decimals=3, step=0.001)
        self.kd_input = self._spin(0.0, 10.0, DEFAULT_KD, decimals=2, step=0.01)
        self.hold_input = self._spin(1.0, MAX_DURATION_S, DEFAULT_HOLD_DURATION_S, " s", decimals=0)
        self.restore_pid_button = QtWidgets.QPushButton("Restore defaults")
        self.restore_pid_button.clicked.connect(self.restore_pid_defaults)
        self.restore_process_button = QtWidgets.QPushButton("Restore defaults")
        self.restore_process_button.clicked.connect(self.restore_process_defaults)
        self.restore_pid_button.setEnabled(False)
        self.restore_process_button.setEnabled(False)
        for control in (self.target_input, self.hold_input, self.kp_input, self.ki_input, self.kd_input):
            control.valueChanged.connect(self._update_restore_buttons)
        process_heading = QtWidgets.QLabel("<b>Process</b>")
        controls.addRow(process_heading)
        controls.addRow("Target", self.target_input)
        controls.addRow("Hold", self.hold_input)
        controls.addRow(self.restore_process_button)

        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        controls.addRow(separator)

        pid_heading = QtWidgets.QLabel("<b>PID tuning</b>")
        controls.addRow(pid_heading)
        controls.addRow(QtWidgets.QLabel("<span style='font-size: 16pt;'>K<sub>p</sub></span>"), self.kp_input)
        controls.addRow(QtWidgets.QLabel("<span style='font-size: 16pt;'>K<sub>i</sub></span>"), self.ki_input)
        controls.addRow(QtWidgets.QLabel("<span style='font-size: 16pt;'>K<sub>d</sub></span>"), self.kd_input)
        controls.addRow(self.restore_pid_button)
        self._set_process_inputs_enabled(True)
        self._update_restore_buttons()
        side.addWidget(controls_box)

        status_box = QtWidgets.QGroupBox("Process status")
        status = QtWidgets.QVBoxLayout(status_box)
        self.progress = StageProgress()
        status.addWidget(self.progress, 1)
        self.stage_label = QtWidgets.QLabel()
        self.elapsed_label = QtWidgets.QLabel()
        self.door_label = QtWidgets.QLabel("Door: Unknown (simulator; not implemented)")
        self.door_label.setStyleSheet("color: #f4b942;")
        self.temperature_label = QtWidgets.QLabel()
        status.addWidget(self.stage_label)
        status.addWidget(self.elapsed_label)
        status.addWidget(self.temperature_label)
        status.addWidget(self.door_label)
        side.addWidget(status_box, 1)

    @staticmethod
    def _spin(
        minimum: float,
        maximum: float,
        value: float,
        suffix: str = "",
        decimals: int = 3,
        step: float | None = None,
    ) -> QtWidgets.QDoubleSpinBox:
        control = QtWidgets.QDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setDecimals(decimals)
        control.setValue(value)
        control.setSingleStep(step if step is not None else (1.0 if decimals == 0 else 0.01))
        control.setSuffix(suffix)
        return control

    def restore_pid_defaults(self) -> None:
        """Restore PID gains without interrupting a running process."""
        self.kp_input.setValue(DEFAULT_KP)
        self.ki_input.setValue(DEFAULT_KI)
        self.kd_input.setValue(DEFAULT_KD)

    def restore_process_defaults(self) -> None:
        """Restore target and duration when the process is stopped."""
        if self.running:
            return
        self.target_input.setValue(DEFAULT_TARGET_TEMPERATURE_C)
        self.hold_input.setValue(DEFAULT_HOLD_DURATION_S)

    def _update_restore_buttons(self) -> None:
        pid_changed = any(
            not math.isclose(actual, default, rel_tol=0.0, abs_tol=1e-9)
            for actual, default in (
                (self.kp_input.value(), DEFAULT_KP),
                (self.ki_input.value(), DEFAULT_KI),
                (self.kd_input.value(), DEFAULT_KD),
            )
        )
        process_changed = any(
            not math.isclose(actual, default, rel_tol=0.0, abs_tol=1e-9)
            for actual, default in (
                (self.target_input.value(), DEFAULT_TARGET_TEMPERATURE_C),
                (self.hold_input.value(), DEFAULT_HOLD_DURATION_S),
            )
        )
        self.restore_pid_button.setEnabled(pid_changed)
        self.restore_process_button.setEnabled(process_changed and not self.running)

    def _set_process_inputs_enabled(self, enabled: bool) -> None:
        self.target_input.setEnabled(enabled)
        self.hold_input.setEnabled(enabled)
        self._update_restore_buttons()

    def _resize_duty_view(self) -> None:
        self.duty_view.setGeometry(self.graph_item.vb.sceneBoundingRect())
        self.duty_view.linkedViewChanged(self.graph_item.vb, self.duty_view.XAxis)

    def _reset_graph_view(self) -> None:
        self._set_graph_x_limits(GRAPH_WINDOW_S)
        self.graph_item.setXRange(0, GRAPH_WINDOW_S, padding=0)
        self.graph_item.setYRange(0, MAX_TEMPERATURE_DISPLAY_C, padding=0)
        self.duty_view.setYRange(0, MAX_OUTPUT_PERCENT, padding=0)

    def _set_graph_x_limits(self, maximum_time_s: float) -> None:
        """Restrict panning and zooming to the time currently recorded."""
        maximum_time_s = max(5.0, maximum_time_s)
        self.graph_item.vb.setLimits(
            xMin=0, xMax=maximum_time_s, minXRange=5, maxXRange=maximum_time_s
        )
        self.duty_view.setLimits(
            xMin=0, xMax=maximum_time_s, minXRange=5, maxXRange=maximum_time_s
        )

    def _follow_live_graph(self) -> None:
        """Keep the latest fixed-width time window visible during a process."""
        maximum_time_s = max(GRAPH_WINDOW_S, self.elapsed_s)
        self._set_graph_x_limits(maximum_time_s)
        window_start = max(0.0, self.elapsed_s - GRAPH_WINDOW_S)
        self.graph_item.setXRange(window_start, window_start + GRAPH_WINDOW_S, padding=0)

    def _fit_graph_to_completed_process(self) -> None:
        """Show the complete process history after cool-down has finished."""
        maximum_time_s = max(5.0, self.elapsed_s)
        self._set_graph_x_limits(maximum_time_s)
        self.graph_item.setXRange(0, maximum_time_s, padding=0)
    def start_process(self) -> None:
        """Start a fresh run, asking before replacing completed run data."""
        if self.running:
            return
        if self.stage == 4 and self.times:
            if not self._confirm_replacing_completed_run():
                return
            self._clear_run_data()
        elif self.aborted:
            self._clear_run_data()

        self.running = True
        self.aborted = False
        self.run_exported = False
        self.stage = 1
        self.hold_elapsed_s = 0.0
        self.integral = 0.0
        self.previous_error = 0.0
        self.previous_temperature = self.temperature_c
        self.filtered_derivative = 0.0
        self.graph_fitted_at_completion = False
        self._set_process_inputs_enabled(False)
        self._update_status()

    def stop_process(self) -> None:
        """Abort the active run and retain its graph for inspection."""
        if not self.running:
            return
        self.running = False
        self.aborted = True
        self.duty_cycle = 0.0
        self._set_process_inputs_enabled(True)
        self._update_status()

    def reset_process(self) -> None:
        """Clear the current run, prompting only before discarding completion data."""
        if self.running:
            self.stop_process()
        if self.stage == 4 and self.times:
            if not self._confirm_replacing_completed_run():
                return
        self._clear_run_data()
        self._set_process_inputs_enabled(True)
        self._update_status()

    def _clear_run_data(self) -> None:
        """Reset run state and graph while keeping all user settings unchanged."""
        self.running = False
        self.aborted = False
        self.run_exported = False
        self.stage = -1
        self.hold_elapsed_s = 0.0
        self.elapsed_s = 0.0
        self.temperature_c = AMBIENT_TEMPERATURE_C
        self.duty_cycle = 0.0
        self.integral = 0.0
        self.previous_error = 0.0
        self.previous_temperature = AMBIENT_TEMPERATURE_C
        self.filtered_derivative = 0.0
        self.graph_fitted_at_completion = False
        self.times.clear()
        self.temperatures.clear()
        self.duties.clear()
        self.goals.clear()
        self._redraw_graph()
        self._reset_graph_view()

    def _confirm_replacing_completed_run(self) -> bool:
        """Offer save, discard, or cancel before completed data is cleared."""
        if self.stage != 4 or not self.times or self.run_exported:
            return True

        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("Save completed run?")
        dialog.setText("The completed run data will be cleared.")
        dialog.setInformativeText("Would you like to export it first?")
        save_button = dialog.addButton(QtWidgets.QMessageBox.StandardButton.Save)
        discard_button = dialog.addButton(QtWidgets.QMessageBox.StandardButton.Discard)
        dialog.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(save_button)
        dialog.exec()

        clicked = dialog.clickedButton()
        if clicked is save_button:
            return self.export_run_data()
        return clicked is discard_button

    def _set_goal_line(self) -> None:
        if self.running and self.stage == 3:
            goal = SAFE_TEMPERATURE_C
        else:
            goal = self.target_input.value()
        x = np.array([0.0, max(GRAPH_WINDOW_S, self.elapsed_s)])
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

        # Conditional integration prevents the integral from building while the
        # heater is saturated in the same direction as the remaining error.
        saturated_high = output >= PID_OUTPUT_MAX and error > 0.0
        saturated_low = output <= PID_OUTPUT_MIN and error < 0.0
        if not (saturated_high or saturated_low):
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
        if not self.running:
            return
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
                self.aborted = False
                self.duty_cycle = 0.0
                self._set_process_inputs_enabled(True)

        self.times.append(self.elapsed_s)
        self.temperatures.append(self.temperature_c)
        self.duties.append(self.duty_cycle * 100)
        self.goals.append(goal)
        self._redraw_graph()
        if self.running:
            self._follow_live_graph()
        elif self.stage == 4 and not self.graph_fitted_at_completion:
            self._fit_graph_to_completed_process()
            self.graph_fitted_at_completion = True
        self._update_status()
    def _redraw_graph(self) -> None:
        x = np.fromiter(self.times, dtype=float)
        self.temperature_curve.setData(x, np.fromiter(self.temperatures, dtype=float))
        self._set_goal_line()
        self.duty_curve.setData(x, np.fromiter(self.duties, dtype=float))
        self.duty_view.setYRange(0, MAX_OUTPUT_PERCENT, padding=0)
        self._update_peak_marker()

    def _update_peak_marker(self) -> None:
        """Annotate the highest measured temperature after successful completion."""
        if self.stage != 4 or not self.temperatures:
            self.peak_marker.hide()
            self.peak_label.hide()
            return

        peak_index = max(range(len(self.temperatures)), key=self.temperatures.__getitem__)
        peak_temperature = self.temperatures[peak_index]
        peak_time = self.times[peak_index]
        minutes, seconds = divmod(peak_time, 60)
        target_delta = peak_temperature - self.target_input.value()
        self.peak_marker.setData([{"pos": (peak_time, peak_temperature)}])
        self.peak_label.setText(
            f"Peak {peak_temperature:.2f} \u00b0C at {int(minutes):02d}:{seconds:04.1f} "
            f"({target_delta:+.2f} \u00b0C vs target)"
        )
        self.peak_label.setPos(peak_time, peak_temperature)
        self.peak_marker.show()
        self.peak_label.show()
    def _update_status(self) -> None:
        fractions = {
            1: min(1.0, max(0.0, (self.temperature_c - AMBIENT_TEMPERATURE_C) / max(1.0, self.target_input.value() - AMBIENT_TEMPERATURE_C))),
            2: min(1.0, self.hold_elapsed_s / max(1.0, self.hold_input.value())),
            3: min(1.0, max(0.0, (self.target_input.value() - self.temperature_c) / max(1.0, self.target_input.value() - SAFE_TEMPERATURE_C))),
        }
        progress_stage = 0 if self.stage == -1 else self.stage
        self.progress.set_progress(progress_stage, fractions.get(self.stage, 1.0 if self.stage == 4 else 0.0))
        names = {-1: "Ready to run", 1: "Warming up", 2: "Holding temperature", 3: "Cooling down", 4: "Complete"}
        stage_text = "Aborted" if self.aborted else names[self.stage]
        self.stage_label.setText(f"Current stage: {stage_text}")
        progress_detail = ""
        if self.stage == 1:
            progress_detail = f"{self.temperature_c:.1f} / {self.target_input.value():.1f} °C · {fractions[1] * 100:.0f}%"
        elif self.stage == 2:
            progress_detail = f"{self.hold_elapsed_s:.1f} / {self.hold_input.value():.1f} s · {fractions[2] * 100:.0f}%"
        elif self.stage == 3:
            progress_detail = f"{self.temperature_c:.1f} / {SAFE_TEMPERATURE_C:.1f} °C · {fractions[3] * 100:.0f}%"
        self.progress.set_progress(
            progress_stage,
            fractions.get(self.stage, 1.0 if self.stage == 4 else 0.0),
            progress_detail,
        )
        elapsed_minutes = int(self.elapsed_s // 60)
        elapsed_seconds = self.elapsed_s - elapsed_minutes * 60
        self.elapsed_label.setText(f"Elapsed: {elapsed_minutes:02d}:{elapsed_seconds:04.1f}")
        self.temperature_label.setText(f"Temperature: {self.temperature_c:.2f} °C    Output: {self.duty_cycle * 100:.0f}%")

    def export_run_data(self) -> bool:
        """Export recorded measurements as a CSV file."""
        if not self.times:
            QtWidgets.QMessageBox.information(self, "No run data", "There is no run data to export yet.")
            return False

        default_name = f"oven-run-{datetime.now():%Y%m%d-%H%M%S}.csv"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export run data", default_name, "CSV files (*.csv)"
        )
        if not path:
            return False
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            with Path(path).open("w", newline="", encoding="utf-8") as run_file:
                writer = csv.writer(run_file)
                writer.writerow(("elapsed_s", "temperature_c", "target_c", "heater_output_percent"))
                writer.writerows(zip(self.times, self.temperatures, self.goals, self.duties))
        except OSError as error:
            QtWidgets.QMessageBox.critical(self, "Export failed", f"Could not save run data:\n{error}")
            return False

        self.run_exported = True
        return True

    def export_settings(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export settings", "oven-settings.json", "JSON files (*.json)")
        if path:
            values = {"target_temperature_c": self.target_input.value(), "kp": self.kp_input.value(), "ki": self.ki_input.value(), "kd": self.kd_input.value(), "hold_duration_s": self.hold_input.value()}
            Path(path).write_text(json.dumps(values, indent=2), encoding="utf-8")

    def import_settings(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import settings", "", "JSON files (*.json)")
        if path:
            values = json.loads(Path(path).read_text(encoding="utf-8"))
            if not self.running:
                self.target_input.setValue(values["target_temperature_c"])
                self.hold_input.setValue(values["hold_duration_s"])
            self.kp_input.setValue(values["kp"])
            self.ki_input.setValue(values["ki"])
            self.kd_input.setValue(values["kd"])


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    window = OvenWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

