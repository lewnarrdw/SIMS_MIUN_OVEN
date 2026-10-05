"""Pure Qt user interface for the oven controller."""

from __future__ import annotations

import math
import sys

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets


DEFAULT_TARGET_TEMPERATURE_C = 45.0
DEFAULT_HOLD_DURATION_S = 60.0
DEFAULT_KP = 0.10
DEFAULT_KI = 0.006
DEFAULT_KD = 0.05
MAX_DURATION_S = 120.0
MAX_TEMPERATURE_DISPLAY_C = 65.0
MAX_OUTPUT_PERCENT = 100.0
GRAPH_WINDOW_S = 120.0


class StageProgress(QtWidgets.QWidget):
    """A vertical connected-node display for the oven-process stages."""

    stages = ("Ready", "Warming up", "Holding", "Cooling down")

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_stage = -1
        self.fraction = 0.0
        self.progress_detail = ""
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
        connector_pen = QtGui.QPen(QtGui.QColor("#59636d"), 5)
        complete_pen = QtGui.QPen(QtGui.QColor("#4caf70"), 5)
        for index, (first, second) in enumerate(zip(positions, positions[1:])):
            painter.setPen(connector_pen)
            painter.drawLine(first, second)
            if self.current_stage > index + 1:
                painter.setPen(complete_pen)
                painter.drawLine(first, second)
            elif self.current_stage == index + 1:
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
                fill, node_pen = QtGui.QColor("#4caf70"), QtGui.QPen(QtGui.QColor("#76d39a"), 2)
            elif active:
                fill, node_pen = QtGui.QColor("#d99a2b"), QtGui.QPen(QtGui.QColor("#ffd166"), 3)
            else:
                fill, node_pen = QtGui.QColor("#39434d"), QtGui.QPen(QtGui.QColor("#59636d"), 2)
            painter.setPen(node_pen)
            painter.setBrush(fill)
            painter.drawEllipse(point, radius, radius)
            if completed:
                check_pen = QtGui.QPen(QtGui.QColor("#f4f7fa"), 2.5)
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
                detail_rect = QtCore.QRectF(label_x, point.y() + 8, max(1, self.width() - label_x - 8), spacing - 10)
                detail_text = painter.fontMetrics().elidedText(
                    self.progress_detail, QtCore.Qt.TextElideMode.ElideRight, int(detail_rect.width())
                )
                painter.drawText(
                    detail_rect,
                    QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.TextFlag.TextSingleLine,
                    detail_text,
                )



class UnhighlightedDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """Keep arrow-button stepping from leaving the value text selected."""

    def stepBy(self, steps: int) -> None:
        super().stepBy(steps)
        self.lineEdit().deselect()

class OvenWindow(QtWidgets.QMainWindow):
    """Oven controller window; actions are placeholders for future wiring."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Oven Controller")
        self.resize(1280, 760)
        self._build_toolbar()
        self._build_layout()

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Process")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setIconSize(QtCore.QSize(22, 22))
        action_specs = (
            ("Run", self.start_process, "run", "#4caf70"),
            ("Stop", self.stop_process, "stop", "#d45b52"),
            ("Reset", self.reset_process, "reset", "#d99a2b"),
            ("Import", self.import_settings, "import", "#63a8e8"),
            ("Export run", self.export_run_data, "export", "#63a8e8"),
            ("Run events...", self.show_run_events, "events", "#63a8e8"),
            ("Export settings", self.export_settings, "settings", "#63a8e8"),
        )
        for label, callback, icon_name, accent in action_specs:
            action = QtGui.QAction(label, self)
            action.setIcon(self._toolbar_icon(icon_name, accent))
            action.setToolTip(label)
            action.triggered.connect(callback)
            toolbar.addAction(action)

    @staticmethod
    def _toolbar_icon(symbol: str, accent: str) -> QtGui.QIcon:
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor(accent), 2))
        painter.setBrush(QtGui.QColor(accent))
        if symbol == "run":
            painter.drawPolygon(QtGui.QPolygonF((QtCore.QPointF(7, 5), QtCore.QPointF(19, 12), QtCore.QPointF(7, 19))))
        elif symbol == "stop":
            painter.drawRoundedRect(QtCore.QRectF(6, 6, 12, 12), 2, 2)
        elif symbol == "reset":
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawArc(QtCore.QRectF(5, 5, 14, 14), 45 * 16, 280 * 16)
            painter.drawLine(QtCore.QPointF(5, 7), QtCore.QPointF(5, 13))
            painter.drawLine(QtCore.QPointF(5, 7), QtCore.QPointF(11, 7))
        elif symbol in ("import", "export"):
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QtCore.QRectF(5, 10, 14, 10), 2, 2)
            arrow_top = symbol == "import"
            painter.drawLine(QtCore.QPointF(12, 4 if arrow_top else 10), QtCore.QPointF(12, 15 if arrow_top else 20))
            if arrow_top:
                painter.drawLine(QtCore.QPointF(8, 11), QtCore.QPointF(12, 15))
                painter.drawLine(QtCore.QPointF(16, 11), QtCore.QPointF(12, 15))
            else:
                painter.drawLine(QtCore.QPointF(8, 13), QtCore.QPointF(12, 9))
                painter.drawLine(QtCore.QPointF(16, 13), QtCore.QPointF(12, 9))
        elif symbol == "events":
            for y in (6, 12, 18):
                painter.drawEllipse(QtCore.QPointF(6, y), 1, 1)
                painter.drawLine(QtCore.QPointF(10, y), QtCore.QPointF(19, y))
        else:
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QtCore.QRectF(5, 5, 14, 14), 2, 2)
            painter.drawEllipse(QtCore.QPointF(12, 12), 3, 3)
            for angle in range(0, 360, 60):
                radians = math.radians(angle)
                inner = QtCore.QPointF(12 + 7 * math.cos(radians), 12 + 7 * math.sin(radians))
                outer = QtCore.QPointF(12 + 10 * math.cos(radians), 12 + 10 * math.sin(radians))
                painter.drawLine(inner, outer)
        painter.end()
        return QtGui.QIcon(pixmap)

    def _build_layout(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.graph = pg.PlotWidget()
        self.graph.setMinimumWidth(560)
        self.graph.setMinimumHeight(300)
        self.graph.setBackground("#20252b")
        self.graph.showGrid(x=True, y=True, alpha=0.32)
        self.graph.setLabel("left", "Temperature (C)", **{"color": "#f4f7fa", "font-size": "16pt", "font-weight": "bold"})
        self.graph.setLabel("bottom", "Elapsed time (s)", **{"color": "#f4f7fa", "font-size": "16pt", "font-weight": "bold"})
        self.legend = self.graph.addLegend(offset=(12, 12), labelTextColor="#f4f7fa", labelTextSize="12pt")
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
        self.duty_curve = pg.PlotCurveItem(pen=pg.mkPen("#63a8e8", width=3))
        self.duty_view.addItem(self.duty_curve)
        self.legend.addItem(self.duty_curve, "Duty cycle")
        layout.addWidget(self.graph, 1)

        side = QtWidgets.QVBoxLayout()
        side.setSpacing(12)
        layout.addLayout(side)
        controls_box = QtWidgets.QGroupBox("Controls")
        controls = QtWidgets.QFormLayout(controls_box)
        self.target_input = self._spin(30.0, 60.0, DEFAULT_TARGET_TEMPERATURE_C, " °C", decimals=1)
        self.target_input.setSingleStep(0.1)
        self.kp_input = self._spin(0.0, 10.0, DEFAULT_KP, decimals=2, step=0.01)
        self.ki_input = self._spin(0.0, 10.0, DEFAULT_KI, decimals=3, step=0.001)
        self.kd_input = self._spin(0.0, 10.0, DEFAULT_KD, decimals=2, step=0.01)
        self.hold_input = self._spin(1.0, MAX_DURATION_S, DEFAULT_HOLD_DURATION_S, " s", decimals=0)
        self.restore_pid_button = QtWidgets.QPushButton("Restore defaults")
        self.restore_pid_button.clicked.connect(self.restore_pid_defaults)
        self.restore_process_button = QtWidgets.QPushButton("Restore defaults")
        self.restore_process_button.clicked.connect(self.restore_process_defaults)
        controls.addRow(QtWidgets.QLabel("<b>Process</b>"))
        controls.addRow("Target", self.target_input)
        controls.addRow("Hold", self.hold_input)
        controls.addRow(self.restore_process_button)
        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        controls.addRow(separator)
        controls.addRow(QtWidgets.QLabel("<b>PID tuning</b>"))
        controls.addRow(QtWidgets.QLabel("<span style='font-size: 16pt;'>K<sub>p</sub></span>"), self.kp_input)
        controls.addRow(QtWidgets.QLabel("<span style='font-size: 16pt;'>K<sub>i</sub></span>"), self.ki_input)
        controls.addRow(QtWidgets.QLabel("<span style='font-size: 16pt;'>K<sub>d</sub></span>"), self.kd_input)
        controls.addRow(self.restore_pid_button)
        side.addWidget(controls_box)

        status_box = QtWidgets.QGroupBox("Process status")
        status = QtWidgets.QVBoxLayout(status_box)
        self.progress = StageProgress()
        status.addWidget(self.progress, 1)
        self.stage_label = QtWidgets.QLabel("Current stage: Ready to run")
        self.elapsed_label = QtWidgets.QLabel("Elapsed: 00:00.0")
        self.temperature_label = QtWidgets.QLabel("Temperature: -- °C    Output: --%")
        self.door_label = QtWidgets.QLabel("Door: Unknown")
        self.door_label.setStyleSheet("color: #f4b942;")
        status.addWidget(self.stage_label)
        status.addWidget(self.elapsed_label)
        status.addWidget(self.temperature_label)
        status.addWidget(self.door_label)
        side.addWidget(status_box, 1)

    @staticmethod
    def _spin(minimum: float, maximum: float, value: float, suffix: str = "", decimals: int = 3, step: float | None = None) -> QtWidgets.QDoubleSpinBox:
        control = UnhighlightedDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setDecimals(decimals)
        control.setValue(value)
        control.setSingleStep(step if step is not None else (1.0 if decimals == 0 else 0.01))
        control.setSuffix(suffix)
        return control

    def _resize_duty_view(self) -> None:
        self.duty_view.setGeometry(self.graph_item.vb.sceneBoundingRect())

    def start_process(self) -> None:
        pass

    def stop_process(self) -> None:
        pass

    def reset_process(self) -> None:
        pass

    def import_settings(self) -> None:
        pass

    def export_run_data(self) -> None:
        pass

    def show_run_events(self) -> None:
        pass

    def export_settings(self) -> None:
        pass

    def restore_pid_defaults(self) -> None:
        pass

    def restore_process_defaults(self) -> None:
        pass


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    window = OvenWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
