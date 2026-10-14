"""Pure Qt user interface for the oven controller."""

from __future__ import annotations

import math
import sys

import config
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
GRAPH_INITIAL_BUFFER_S = 120.0
GRAPH_EXPANSION_TRIGGER = 0.8
GRAPH_EXPANSION_FACTOR = 1.25


class StageProgress(QtWidgets.QWidget):
    """A vertical connected-node display for the oven-process stages."""

    stages = ("Ready", "Warming up", "Holding", "Cooling down")

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_stage = -1
        self.fraction = 0.0
        self.progress_detail = ""
        self.setMinimumWidth(180)
        self.setMinimumHeight(200)

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
    """Oven controller window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Oven Controller")
        self._build_toolbar()
        self._build_layout()
        self._set_graph_auto_range_icon()
        self.resize(1280, 760)

    def _set_graph_auto_range_icon(self) -> None:
        """Use a fit-to-view glyph on Pyqtgraph's built-in auto-range button."""
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor("#d9a441"), 2.4)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        # Four open corners suggest expanding the plot to fit its contents.
        for x_sign, y_sign in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            x_inner, x_outer = (7, 3) if x_sign == 1 else (17, 21)
            y_inner, y_outer = (7, 3) if y_sign == 1 else (17, 21)
            painter.drawLine(QtCore.QPointF(x_outer, y_inner), QtCore.QPointF(x_inner, y_inner))
            painter.drawLine(QtCore.QPointF(x_inner, y_outer), QtCore.QPointF(x_inner, y_inner))
        painter.end()
        self.graph_item.autoBtn.setScale(20 / 14)
        self.graph_item.autoBtn.setPixmap(pixmap)
        self.graph_item.autoBtn.setToolTip("Auto-range graph")
        self.graph_item.autoBtn.clicked.connect(self._restore_empty_graph_range)
        self.graph_item.autoBtn.clicked.connect(self.fit_graph_to_data)

    def fit_graph_to_data(self, *_args) -> None:
        """Show the complete temperature run when the graph's fit button is used."""
        x_data, y_data = self.temperature_curve.getData()
        if x_data is None or len(x_data) == 0:
            self._restore_empty_graph_range()
            return
        x_max = float(max(x_data))
        x_pad = max(2.0, x_max * 0.03)
        y_values = list(y_data)
        goal_x, goal_y = self.goal_curve.getData()
        if goal_x is not None and len(goal_y):
            y_values.extend(goal_y)
        y_min, y_max = float(min(y_values)), float(max(y_values))
        y_pad = max(3.0, (y_max - y_min) * 0.08)
        full_x_max = max(GRAPH_WINDOW_S, x_max + x_pad)
        self.graph_item.vb.setLimits(xMin=0, xMax=full_x_max, minXRange=5, maxXRange=full_x_max)
        self.duty_view.setLimits(xMin=0, xMax=full_x_max, minXRange=5, maxXRange=full_x_max)
        self.graph_item.setXRange(0, x_max + x_pad, padding=0)
        self.graph_item.setYRange(max(0.0, y_min - y_pad), min(MAX_TEMPERATURE_DISPLAY_C, y_max + y_pad), padding=0)

    def _restore_empty_graph_range(self, *_args) -> None:
        """Give auto-range a useful default when the graph has no samples yet."""
        x_data, _y_data = self.temperature_curve.getData()
        if x_data is not None and len(x_data) > 0:
            return
        self.graph_item.setXRange(0, GRAPH_WINDOW_S, padding=0)
        self.graph_item.setYRange(0, MAX_TEMPERATURE_DISPLAY_C, padding=0)
        self.duty_view.setYRange(0, MAX_OUTPUT_PERCENT, padding=0)

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Process")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setIconSize(QtCore.QSize(22, 22))
        toolbar.setStyleSheet(
            "QToolButton { border: 1px solid transparent; border-radius: 5px; padding: 6px 9px; }"
            "QToolButton:hover { background-color: #343b42; border-color: #78838d; }"
            "QToolButton:pressed { background-color: #3d464e; border-color: #8a949d; }"
        )
        action_specs = (
            ("Run", self.start_process, "run", "#4caf70"),
            ("Stop", self.stop_process, "stop", "#d45b52"),
            ("Reset", self.reset_process, "reset", "#d99a2b"),
        )
        for label, callback, icon_name, accent in action_specs:
            action = QtGui.QAction(label, self)
            if label == "Run":
                self.run_action = action
            action.setIcon(self._toolbar_icon(icon_name, accent))
            action.setToolTip({
                "Import": "Import process settings",
                "Export": "Export run data",
                "Event log": "View events from this run",
            }.get(label, label))
            action.setStatusTip(action.toolTip())
            action.triggered.connect(callback)
            toolbar.addAction(action)

        toolbar.addSeparator()
        self.event_log_action = QtGui.QAction("Event log", self)
        self.event_log_action.setIcon(self._toolbar_icon("events", "#63a8e8"))
        self.event_log_action.setToolTip("View events from this run")
        self.event_log_action.triggered.connect(self.show_run_events)
        toolbar.addAction(self.event_log_action)
        toolbar.addSeparator()

        self.file_menu = QtWidgets.QMenu("File", self)
        self.file_actions: dict[str, QtGui.QAction] = {}
        for label, callback in (
            ("Import run", self.import_run),
            ("Export run", self.export_run_data),
            ("Import settings", self.import_settings),
            ("Export settings", self.export_settings),
        ):
            action = self.file_menu.addAction(label)
            action.triggered.connect(callback)
            self.file_actions[label] = action
        self.file_button = QtWidgets.QToolButton(self)
        self.file_button.setText("File")
        self.file_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.file_button.setMenu(self.file_menu)
        self.file_button.setToolTip("Import and export runs and settings")
        toolbar.addWidget(self.file_button)
        toolbar.addSeparator()

        self.manual_menu = QtWidgets.QMenu("Manual controls", self)
        self.manual_actions: dict[str, QtGui.QAction] = {}
        for label, command_name in (
            ("Open door", "open"),
            ("Close door", "close"),
            ("Read temperature", "read_temperature"),
            ("Heater on", "heater_on"),
            ("Heater off", "heater_off"),
        ):
            action = self.manual_menu.addAction(label)
            action.triggered.connect(lambda _checked=False, name=command_name: self.manual_command(name))
            self.manual_actions[label] = action
        self.manual_button = QtWidgets.QToolButton(self)
        self.manual_button.setText("Manual controls")
        self.manual_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.manual_button.setMenu(self.manual_menu)
        self.manual_button.setToolTip("Open door, close door, read temperature, or control the heater")
        toolbar.addWidget(self.manual_button)

        spacer = QtWidgets.QWidget(self)
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.emergency_stop_button = QtWidgets.QPushButton("E-STOP", self)
        self.emergency_stop_button.setMinimumHeight(34)
        self.emergency_stop_button.setToolTip("Immediately stop the process and cut heater output")
        self.emergency_stop_button.setStyleSheet(
            "QPushButton { background-color: #b3261e; color: white; font-weight: bold; "
            "border: 2px solid #ff6b62; border-radius: 5px; padding: 5px 16px; }"
            "QPushButton:hover { background-color: #d32f2f; }"
            "QPushButton:pressed { background-color: #7f1d1d; }"
        )
        self.emergency_stop_button.clicked.connect(self.emergency_stop)
        toolbar.addWidget(self.emergency_stop_button)


    @staticmethod
    def _toolbar_icon(symbol: str, accent: str) -> QtGui.QIcon:
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor(accent), 2.0)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

        if symbol == "run":
            painter.setBrush(QtGui.QColor(accent))
            painter.drawPolygon(QtGui.QPolygonF((QtCore.QPointF(7, 5), QtCore.QPointF(19, 12), QtCore.QPointF(7, 19))))
        elif symbol == "stop":
            painter.setBrush(QtGui.QColor(accent))
            painter.drawRoundedRect(QtCore.QRectF(6, 6, 12, 12), 2, 2)
        elif symbol == "reset":
            # Familiar reset glyph: an open counterclockwise loop with an arrowhead.
            arc = QtGui.QPainterPath(QtCore.QPointF(7, 6))
            arc.cubicTo(QtCore.QPointF(9, 4.5), QtCore.QPointF(11, 4), QtCore.QPointF(12, 4))
            arc.cubicTo(QtCore.QPointF(16.5, 4), QtCore.QPointF(20, 7.5), QtCore.QPointF(20, 12))
            arc.cubicTo(QtCore.QPointF(20, 16.5), QtCore.QPointF(16.5, 20), QtCore.QPointF(12, 20))
            arc.cubicTo(QtCore.QPointF(7.5, 20), QtCore.QPointF(4, 16.5), QtCore.QPointF(4, 12))
            painter.drawPath(arc)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(accent))
            painter.drawPolygon(QtGui.QPolygonF((
                QtCore.QPointF(4, 4), QtCore.QPointF(4, 11), QtCore.QPointF(10, 7),
            )))
        elif symbol in ("import_settings", "export_settings"):
            # Compact control sliders paired with the transfer direction.
            direction = "import" if symbol == "import_settings" else "export"
            painter.drawLine(QtCore.QPointF(4, 7), QtCore.QPointF(13, 7))
            painter.drawLine(QtCore.QPointF(4, 12), QtCore.QPointF(13, 12))
            painter.drawLine(QtCore.QPointF(4, 17), QtCore.QPointF(13, 17))
            painter.setBrush(QtGui.QColor(accent))
            for x, y in ((8, 7), (11, 12), (6, 17)):
                painter.drawEllipse(QtCore.QPointF(x, y), 1.5, 1.5)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            if direction == "import":
                painter.drawLine(QtCore.QPointF(18, 5), QtCore.QPointF(18, 18))
                painter.drawLine(QtCore.QPointF(15, 15), QtCore.QPointF(18, 18))
                painter.drawLine(QtCore.QPointF(21, 15), QtCore.QPointF(18, 18))
            else:
                painter.drawLine(QtCore.QPointF(18, 19), QtCore.QPointF(18, 6))
                painter.drawLine(QtCore.QPointF(15, 9), QtCore.QPointF(18, 6))
                painter.drawLine(QtCore.QPointF(21, 9), QtCore.QPointF(18, 6))
        elif symbol in ("import", "export"):
            # A tray makes the direction of the transfer unambiguous.
            tray = QtGui.QPainterPath(QtCore.QPointF(5, 14))
            tray.lineTo(5, 19)
            tray.quadTo(5, 20, 6, 20)
            tray.lineTo(18, 20)
            tray.quadTo(19, 20, 19, 19)
            tray.lineTo(19, 14)
            painter.drawPath(tray)
            if symbol == "import":
                painter.drawLine(QtCore.QPointF(12, 4), QtCore.QPointF(12, 15))
                painter.drawLine(QtCore.QPointF(8.5, 11.5), QtCore.QPointF(12, 15))
                painter.drawLine(QtCore.QPointF(15.5, 11.5), QtCore.QPointF(12, 15))
            else:
                painter.drawLine(QtCore.QPointF(12, 16), QtCore.QPointF(12, 5))
                painter.drawLine(QtCore.QPointF(8.5, 8.5), QtCore.QPointF(12, 5))
                painter.drawLine(QtCore.QPointF(15.5, 8.5), QtCore.QPointF(12, 5))
        elif symbol == "events":
            # A chronological event timeline on a page.
            painter.drawRoundedRect(QtCore.QRectF(4, 3, 16, 18), 2, 2)
            painter.drawLine(QtCore.QPointF(8, 6), QtCore.QPointF(8, 18))
            for y, line_end in ((7, 17), (12, 16), (17, 18)):
                painter.setBrush(QtGui.QColor(accent))
                painter.drawEllipse(QtCore.QPointF(8, y), 1.25, 1.25)
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                painter.drawLine(QtCore.QPointF(11, y), QtCore.QPointF(line_end, y))

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
        self.graph_item.getAxis('left').setWidth(72)
        right_axis.setWidth(72)
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
        self.temperature_marker_items: list[pg.GraphicsObject] = []
        self._temperature_marker_points: list[tuple[float, float]] = []
        self._temperature_marker_connectors: list[pg.PlotDataItem] = []
        self.goal_curve = self.graph.plot(name="Goal", pen=pg.mkPen((255, 209, 102, 160), width=3, style=QtCore.Qt.PenStyle.DashLine))
        self.duty_curve = pg.PlotCurveItem(pen=pg.mkPen("#63a8e8", width=3))
        self.duty_view.addItem(self.duty_curve)
        self.legend.addItem(self.duty_curve, "Duty cycle")
        layout.addWidget(self.graph, 1)
        self.graph_item.vb.sigRangeChanged.connect(self._fit_visible_temperature_annotations)
        self.graph_item.vb.sigResized.connect(self._fit_visible_temperature_annotations)

        side = QtWidgets.QVBoxLayout()
        side.setSpacing(12)
        layout.addLayout(side)
        controls_box = QtWidgets.QGroupBox("Controls")
        controls = QtWidgets.QFormLayout(controls_box)
        self.target_input = self._spin(config.MIN_SETPOINT, config.MAX_SETPOINT, DEFAULT_TARGET_TEMPERATURE_C, " °C", decimals=1)
        self.target_input.setSingleStep(0.1)
        self.kp_input = self._spin(0.0, 10.0, DEFAULT_KP, decimals=2, step=0.01)
        self.ki_input = self._spin(0.0, 10.0, DEFAULT_KI, decimals=3, step=0.001)
        self.kd_input = self._spin(0.0, 10.0, DEFAULT_KD, decimals=2, step=0.01)
        self.hold_input = self._spin(1.0, MAX_DURATION_S, DEFAULT_HOLD_DURATION_S, " s", decimals=0)
        self.restore_pid_button = QtWidgets.QPushButton("Restore defaults")
        self.restore_pid_button.setStyleSheet(
            "QPushButton { border: 1px solid #59636d; border-radius: 4px; padding: 5px 8px; }"
            "QPushButton:hover { background-color: #343b42; border-color: #78838d; }"
            "QPushButton:pressed { background-color: #3d464e; border-color: #8a949d; }"
        )
        self.restore_pid_button.clicked.connect(self.restore_pid_defaults)
        self.restore_process_button = QtWidgets.QPushButton("Restore defaults")
        self.restore_process_button.setStyleSheet(self.restore_pid_button.styleSheet())
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

    def _clear_temperature_markers(self) -> None:
        """Remove annotations from the previous run."""
        for item in (*self.temperature_marker_items, *self._temperature_marker_connectors):
            self.graph.removeItem(item)
        self.temperature_marker_items.clear()
        self._temperature_marker_points.clear()
        self._temperature_marker_connectors.clear()

    def _add_temperature_marker(
        self,
        elapsed_s: float,
        temperature_c: float,
        label: str,
        color: str,
    ) -> None:
        """Draw a point and label on the temperature axis."""
        marker = pg.ScatterPlotItem(
            [elapsed_s],
            [temperature_c],
            symbol="o",
            size=12,
            pen=pg.mkPen(color, width=1.5),
            brush=pg.mkBrush(color),
        )
        annotation = pg.TextItem(
            label,
            color="#fff1a8",
            anchor=(0.0, 1.0),
            fill=pg.mkBrush("#20252b"),
            border=pg.mkPen(color),
        )
        annotation.setPos(elapsed_s, temperature_c)
        self.graph.addItem(marker)
        self.graph.addItem(annotation)
        connector = pg.PlotDataItem(pen=pg.mkPen(color, width=1, style=QtCore.Qt.PenStyle.DotLine))
        self.graph.addItem(connector)
        self.temperature_marker_items.extend((marker, annotation))
        self._temperature_marker_points.append((elapsed_s, temperature_c))
        self._temperature_marker_connectors.append(connector)
        self._layout_temperature_markers()

    def _layout_temperature_markers(self) -> None:
        """Separate end-of-run markers and labels in screen space."""
        bounds = self.graph_item.vb.sceneBoundingRect().adjusted(8, 8, -8, -8)
        view = self.graph_item.vb
        occupied_labels: list[QtCore.QRectF] = []
        occupied_markers: list[QtCore.QPointF] = []
        candidates = ((12, -12, 0.0, 1.0), (-12, -12, 1.0, 1.0),
                      (12, 12, 0.0, 0.0), (-12, 12, 1.0, 0.0),
                      (18, -30, 0.0, 1.0), (-18, -30, 1.0, 1.0),
                      (18, 30, 0.0, 0.0), (-18, 30, 1.0, 0.0),
                      (0, -42, 0.5, 1.0), (0, 42, 0.5, 0.0))

        for index, (marker, annotation) in enumerate(zip(
            self.temperature_marker_items[::2],
            self.temperature_marker_items[1::2],
        )):
            data_point = QtCore.QPointF(*self._temperature_marker_points[index])
            scene_point = view.mapViewToScene(data_point)
            marker_candidates = [QtCore.QPointF(scene_point.x() + dx, scene_point.y() + dy)
                                 for dx, dy in ((0, 0), (11, 0), (-11, 0), (0, 11),
                                                (0, -11), (16, 16), (-16, 16),
                                                (16, -16), (-16, -16))]
            marker_scene = next((point for point in marker_candidates
                                 if bounds.contains(point) and all(
                                     math.hypot(point.x() - prior.x(), point.y() - prior.y()) >= 13
                                     for prior in occupied_markers)), marker_candidates[0])
            occupied_markers.append(marker_scene)
            marker_data = view.mapSceneToView(marker_scene)
            marker.setData([marker_data.x()], [marker_data.y()])
            self._temperature_marker_connectors[index].setData(
                [self._temperature_marker_points[index][0], marker_data.x()],
                [self._temperature_marker_points[index][1], marker_data.y()],
            )

            for dx, dy, anchor_x, anchor_y in candidates:
                annotation.setAnchor((anchor_x, anchor_y))
                label_data = view.mapSceneToView(marker_scene + QtCore.QPointF(dx, dy))
                annotation.setPos(label_data.x(), label_data.y())
                rect = annotation.mapRectToScene(annotation.boundingRect())
                if (bounds.contains(rect) and all(not rect.intersects(other.adjusted(-3, -3, 3, 3))
                                                  for other in occupied_labels)):
                    occupied_labels.append(rect)
                    break
            else:
                annotation.setAnchor((0.5, 1.0))
                label_data = view.mapSceneToView(marker_scene + QtCore.QPointF(0, -48))
                annotation.setPos(label_data.x(), label_data.y())
                occupied_labels.append(annotation.mapRectToScene(annotation.boundingRect()))

    def _fit_visible_temperature_annotations(self, *_args) -> None:
        """Reposition marker text as the plot is panned, zoomed, or resized."""
        if self.temperature_marker_items:
            self._layout_temperature_markers()

    def show_peak_temperature_marker(
        self,
        peak_elapsed_s: float,
        peak_temperature_c: float,
        target_temperature_c: float,
    ) -> None:
        """Annotate the maximum temperature after a run completes."""
        minutes, seconds = divmod(peak_elapsed_s, 60.0)
        difference_c = peak_temperature_c - target_temperature_c
        label = (
            f"Peak {peak_temperature_c:.2f} °C at "
            f"{int(minutes):02d}:{seconds:04.1f} "
            f"({difference_c:+.2f} °C vs target)"
        )
        self._add_temperature_marker(peak_elapsed_s, peak_temperature_c, label, "#ffe680")

    def show_hold_minimum_temperature_marker(
        self,
        minimum_elapsed_s: float,
        minimum_temperature_c: float,
    ) -> None:
        """Annotate the lowest hold temperature once the hold stage ends.

        Pass the time when that minimum was measured so the marker sits on
        the corresponding point in the temperature curve.
        """
        minutes, seconds = divmod(minimum_elapsed_s, 60.0)
        label = (
            f"Hold low {minimum_temperature_c:.2f} °C at "
            f"{int(minutes):02d}:{seconds:04.1f}"
        )
        self._add_temperature_marker(
            minimum_elapsed_s,
            minimum_temperature_c,
            label,
            "#8fc5f5",
        )

    def _on_hold_completed(
        self,
        minimum_elapsed_s: float,
        minimum_temperature_c: float,
    ) -> None:
        """Hold-end marker call site for the future run-data integration."""
        # self.show_hold_minimum_temperature_marker(
        #     minimum_elapsed_s, minimum_temperature_c
        # )
        pass

    def _on_process_completed(
        self,
        peak_elapsed_s: float,
        peak_temperature_c: float,
    ) -> None:
        """Peak marker call site for a successfully completed run."""
        # self.show_peak_temperature_marker(
        #     peak_elapsed_s, peak_temperature_c, self.target_input.value()
        # )
        pass

    def _resize_duty_view(self) -> None:
        self.duty_view.setGeometry(self.graph_item.vb.sceneBoundingRect())

    def _set_graph_time_extent(self, end_s: float) -> None:
        """Keep panning bounded to the time covered by the current run."""
        x_max = max(GRAPH_WINDOW_S, end_s)
        self.graph_item.vb.setLimits(xMin=0, xMax=x_max, maxXRange=x_max)
        self.duty_view.setLimits(xMin=0, xMax=x_max, maxXRange=x_max)

    def start_process(self) -> None:
        self._clear_temperature_markers()

    def stop_process(self) -> None:
        pass

    def emergency_stop(self) -> None:
        pass

    def manual_command(self, name: str) -> None:
        del name

    def reset_process(self) -> None:
        pass

    def import_run(self) -> None:
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


def show_run_summary_preview(window: OvenWindow) -> None:
    """Display a disposable, fake-data run summary preview in the status panel."""
    examples = {
        "Completed": dict(outcome="Completed successfully", duration=186.4, target=45.0,
                          peak=(47.3, 132.0), low=(44.2, 78.0),
                          reason="Process reached its planned end.", color="#4caf70"),
        "Manually stopped": dict(outcome="Stopped by operator", duration=104.8, target=45.0,
                                 peak=(46.1, 81.0), low=(42.7, 37.0),
                                 reason="Operator requested a stop during the holding stage.", color="#d99a2b"),
        "Forced stop": dict(outcome="Emergency stop", duration=62.3, target=45.0,
                            peak=(43.8, 58.0), low=(31.4, 12.0),
                            reason="Emergency stop was activated; heater output is off.", color="#d45b52"),
    }
    status_box = next((box for box in window.findChildren(QtWidgets.QGroupBox)
                       if box.title() == "Process status"), None)
    if status_box is None or status_box.layout() is None:
        return

    status_layout = status_box.layout()
    status_layout.addWidget(QtWidgets.QLabel("<b>Run summary preview</b>", status_box))
    selector = QtWidgets.QComboBox(status_box)
    selector.addItems(examples)
    selector.setToolTip("Switch between fabricated run outcomes")
    status_layout.addWidget(selector)

    card = QtWidgets.QFrame(status_box)
    card.setObjectName("runSummaryPreviewCard")
    card.setStyleSheet(
        "QFrame#runSummaryPreviewCard { background: #2a3139; border: 1px solid #59636d; border-radius: 6px; }"
        "QLabel { border: none; }"
    )
    card_layout = QtWidgets.QVBoxLayout(card)
    card_layout.setContentsMargins(10, 8, 10, 8)
    card_layout.setSpacing(4)
    outcome_label = QtWidgets.QLabel(card)
    outcome_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
    duration_label = QtWidgets.QLabel(card)
    stats_label = QtWidgets.QLabel(card)
    stats_label.setWordWrap(True)
    detail_button = QtWidgets.QPushButton("Run details…", card)
    detail_button.setMaximumWidth(130)
    card_layout.addWidget(outcome_label)
    card_layout.addWidget(duration_label)
    card_layout.addWidget(stats_label)
    card_layout.addWidget(detail_button, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
    status_layout.addWidget(card)

    def time_text(seconds: float) -> str:
        minutes, remainder = divmod(seconds, 60.0)
        return f"{int(minutes):02d}:{remainder:04.1f}"

    def selected_example() -> tuple[str, dict[str, object]]:
        name = selector.currentText()
        return name, examples[name]

    def refresh_card(_index: int = 0) -> None:
        name, example = selected_example()
        peak_c, peak_s = example["peak"]
        low_c, low_s = example["low"]
        difference_c = peak_c - example["target"]
        outcome_label.setText(example["outcome"])
        outcome_label.setStyleSheet(
            f"font-weight: bold; font-size: 11pt; color: {example['color']};"
        )
        duration_label.setText(f"Duration  {time_text(example['duration'])}")
        stats_label.setText(
            f"Peak  {peak_c:.1f} °C at {time_text(peak_s)}  ({difference_c:+.1f} °C vs target)\n"
            f"Lowest observed  {low_c:.1f} °C at {time_text(low_s)}"
        )

        # These samples and markers are fake and exist only for this preview.
        end_s = example["duration"]
        points = sorted(set((0.0, end_s * 0.2, low_s, end_s * 0.6, peak_s, end_s)))
        temperatures = [
            25.0 + (low_c - 25.0) * index / max(1, len(points) - 1)
            for index in range(len(points))
        ]
        temperatures[points.index(low_s)] = low_c
        temperatures[points.index(peak_s)] = peak_c
        window.temperature_curve.setData(points, temperatures)
        window.goal_curve.setData([0.0, end_s], [example["target"], example["target"]])
        window.duty_curve.setData(points, [max(0.0, 80.0 - index * 12.0) for index in range(len(points))])
        window._clear_temperature_markers()
        if name == "Completed":
            window.show_hold_minimum_temperature_marker(low_s, low_c)
            window.show_peak_temperature_marker(peak_s, peak_c, example["target"])
        else:
            if name == "Manually stopped":
                window.show_hold_minimum_temperature_marker(low_s, low_c)
            window._add_temperature_marker(end_s, temperatures[-1], "Stopped", example["color"])
        window._set_graph_time_extent(end_s)
        window.graph_item.setXRange(max(0.0, end_s - GRAPH_WINDOW_S), max(GRAPH_WINDOW_S, end_s), padding=0)

    def show_details() -> None:
        name, example = selected_example()
        peak_c, peak_s = example["peak"]
        low_c, low_s = example["low"]
        difference_c = peak_c - example["target"]
        dialog = QtWidgets.QDialog(window)
        dialog.setWindowTitle(f"Run details — {name}")
        dialog.setMinimumWidth(430)
        layout = QtWidgets.QVBoxLayout(dialog)
        outcome = QtWidgets.QLabel(f"<b>{example['outcome']}</b>")
        outcome.setStyleSheet(f"color: {example['color']}; font-size: 13pt;")
        layout.addWidget(outcome)
        details = QtWidgets.QFormLayout()
        details.addRow("Outcome", QtWidgets.QLabel(name))
        details.addRow("Duration", QtWidgets.QLabel(time_text(example["duration"])))
        details.addRow("Target", QtWidgets.QLabel(f"{example['target']:.1f} °C"))
        details.addRow("Peak", QtWidgets.QLabel(f"{peak_c:.1f} °C at {time_text(peak_s)}"))
        details.addRow("Peak vs target", QtWidgets.QLabel(f"{difference_c:+.1f} °C"))
        details.addRow("Lowest observed", QtWidgets.QLabel(f"{low_c:.1f} °C at {time_text(low_s)}"))
        details.addRow("Stop information", QtWidgets.QLabel(example["reason"]))
        layout.addLayout(details)
        note = QtWidgets.QLabel(
            "Fabricated preview data. Temperatures describe the run up to its end; "
            "only a completed run receives the peak graph annotation."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #cbd5df;")
        layout.addWidget(note)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    selector.currentIndexChanged.connect(refresh_card)
    detail_button.clicked.connect(show_details)
    refresh_card()


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    window = OvenWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
