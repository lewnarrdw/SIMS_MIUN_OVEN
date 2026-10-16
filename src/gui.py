"""Pure Qt user interface for the oven controller."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import config
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets


DEFAULT_TARGET_TEMPERATURE_C = 45.0
DEFAULT_HOLD_DURATION_S = 60.0
DEFAULT_KP = 0.10
DEFAULT_KI = 0.006
DEFAULT_KD = 0.05
MAX_DURATION_S = 120.0
MAX_TEMPERATURE_DISPLAY_C = 50.0
MAX_OUTPUT_PERCENT = 100.0
GRAPH_WINDOW_S = 120.0
GRAPH_INITIAL_BUFFER_S = 120.0
GRAPH_EXPANSION_TRIGGER = 0.8
GRAPH_EXPANSION_FACTOR = 1.25
FONT_ASSET_DIRECTORY = Path(__file__).resolve().parent / "assets" / "fonts"


def _load_ui_fonts() -> None:
    """Register the bundled IBM Plex faces so Qt does not substitute fonts."""
    font_files = (
        "IBMPlexSans-Regular.ttf",
        "IBMPlexSans-Medium.ttf",
        "IBMPlexSans-SemiBold.ttf",
        "IBMPlexMono-Regular.ttf",
        "IBMPlexMono-Medium.ttf",
        "IBMPlexMono-SemiBold.ttf",
    )
    registered = QtGui.QFontDatabase.applicationFontFamilies
    for font_file in font_files:
        font_path = FONT_ASSET_DIRECTORY / font_file
        if not font_path.is_file():
            raise FileNotFoundError(f"Required application font is missing: {font_path}")
        font_id = QtGui.QFontDatabase.addApplicationFont(str(font_path))
        if font_id < 0 or not registered(font_id):
            raise RuntimeError(f"Could not load application font: {font_path}")


class StageProgress(QtWidgets.QWidget):
    """A compact horizontal connected-node display for process stages."""

    stages = ("Ready", "Heating", "Holding", "Cooling", "Done")

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_stage = -1
        self.fraction = 0.0
        self.progress_detail = ""
        self.setMinimumWidth(260)
        self.setMinimumHeight(66)

    def set_progress(self, current_stage: int, fraction: float = 0.0, detail: str = "") -> None:
        self.current_stage = current_stage
        self.fraction = max(0.0, min(1.0, fraction))
        self.progress_detail = detail
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        radius = 11
        left = 24
        right = self.width() - 24
        center_y = 18
        spacing = (right - left) / (len(self.stages) - 1)
        positions = [QtCore.QPointF(left + index * spacing, center_y) for index in range(len(self.stages))]
        base_font = painter.font()
        base_font.setPointSize(9)
        for index, (stage, point) in enumerate(zip(self.stages, positions)):
            completed = index < self.current_stage
            active = index == self.current_stage
            if active:
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(QtGui.QColor(232, 179, 61, 56))
                painter.drawEllipse(point, radius + 4, radius + 4)
                fill, node_pen = QtGui.QColor("#e8b33d"), QtGui.QPen(QtGui.QColor("#e8b33d"), 2)
            elif completed:
                fill, node_pen = QtGui.QColor("#6fbf98"), QtGui.QPen(QtGui.QColor("#6fbf98"), 2)
            else:
                fill, node_pen = QtGui.QColor("#1c1f24"), QtGui.QPen(QtGui.QColor("#454c57"), 2)
            painter.setPen(node_pen)
            painter.setBrush(fill)
            painter.drawEllipse(point, radius, radius)
            if completed:
                check_pen = QtGui.QPen(QtGui.QColor("#15171b"), 2.2)
                check_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
                check_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
                painter.setPen(check_pen)
                painter.drawLine(QtCore.QPointF(point.x() - 5, point.y()), QtCore.QPointF(point.x() - 1, point.y() + 4))
                painter.drawLine(QtCore.QPointF(point.x() - 1, point.y() + 4), QtCore.QPointF(point.x() + 6, point.y() - 5))
            painter.setPen(QtGui.QColor("#f2d48a" if active else "#c3c8cf" if completed else "#8b929c"))
            font = QtGui.QFont(base_font)
            font.setBold(active or completed)
            painter.setFont(font)
            painter.drawText(
                QtCore.QRectF(point.x() - 31, point.y() + 20, 62, 16),
                QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop,
                stage,
            )

class DurationSpinBox(QtWidgets.QDoubleSpinBox):
    """Display a duration as minutes and seconds while storing total seconds."""

    def textFromValue(self, value: float) -> str:
        minutes, seconds = divmod(int(round(value)), 60)
        return f"{minutes}:{seconds:02d}"

    def valueFromText(self, text: str) -> float:
        parts = text.strip().split(":")
        try:
            if len(parts) == 1:
                return float(int(parts[0]))
            if len(parts) == 2:
                return float(int(parts[0]) * 60 + int(parts[1]))
        except ValueError:
            pass
        return float(self.minimum())

    def validate(self, text: str, position: int):
        text = text.strip()
        if not text:
            return QtGui.QValidator.State.Intermediate, text, position
        parts = text.split(":")
        if len(parts) == 1:
            if not parts[0].isdigit():
                return QtGui.QValidator.State.Invalid, text, position
            state = (QtGui.QValidator.State.Acceptable
                     if int(parts[0]) <= self.maximum()
                     else QtGui.QValidator.State.Invalid)
            return state, text, position
        if len(parts) != 2 or not parts[0].isdigit():
            return QtGui.QValidator.State.Invalid, text, position
        if not parts[1]:
            return QtGui.QValidator.State.Intermediate, text, position
        if not parts[1].isdigit() or len(parts[1]) > 2 or int(parts[1]) > 59:
            return QtGui.QValidator.State.Invalid, text, position
        total_seconds = int(parts[0]) * 60 + int(parts[1])
        state = (QtGui.QValidator.State.Acceptable
                 if total_seconds <= self.maximum()
                 else QtGui.QValidator.State.Invalid)
        return state, text, position


class TargetLegendMarker(QtWidgets.QWidget):
    """Paint the Target swatch with the same color and dash spacing as its curve."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(25, 12)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor(255, 209, 102, 160), 2)
        pen.setStyle(QtCore.Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([4.0, 4.5])
        painter.setPen(pen)
        y = self.height() / 2
        painter.drawLine(QtCore.QPointF(0, y), QtCore.QPointF(self.width() - 1, y))
        painter.end()


class PidTermCheckBox(QtWidgets.QCheckBox):
    """Compact themed PID toggle with a clear check or cross state marker."""

    def __init__(self, label: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(44)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        box = QtCore.QRectF(1, (self.height() - 16) / 2, 16, 16)
        checked = self.isChecked()
        hovered = self.underMouse()
        fill = QtGui.QColor("#2f8a5f" if checked else "#15181c")
        outline = QtGui.QColor("#6fbf98" if checked else ("#9aa1ab" if hovered else "#454c57"))
        if hovered and checked:
            fill = QtGui.QColor("#28764f")
        painter.setPen(QtGui.QPen(outline, 1.4))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 3, 3)

        if checked:
            mark_pen = QtGui.QPen(QtGui.QColor("#e4e7eb"), 1.8)
            mark_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            mark_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
            painter.setPen(mark_pen)
            mark = QtGui.QPainterPath(QtCore.QPointF(4.5, box.center().y()))
            mark.lineTo(7, box.center().y() + 2.5)
            mark.lineTo(13.5, box.center().y() - 3.5)
            painter.drawPath(mark)

        painter.setPen(QtGui.QColor("#b4bac3"))
        font = QtGui.QFont("IBM Plex Sans")
        font.setPointSizeF(10.5)  # 14 px, matching the HTML example's field labels.
        font.setWeight(QtGui.QFont.Weight.DemiBold)
        painter.setFont(font)
        font_metrics = QtGui.QFontMetricsF(font)
        baseline = (self.height() - font_metrics.height()) / 2 + font_metrics.ascent()
        painter.drawText(QtCore.QPointF(24, baseline), "K")
        subscript = self.text()[-1:]
        subscript_font = QtGui.QFont(font)
        subscript_font.setPointSizeF(8.0)
        painter.setFont(subscript_font)
        subscript_metrics = QtGui.QFontMetricsF(subscript_font)
        painter.drawText(
            QtCore.QPointF(24 + font_metrics.horizontalAdvance("K"),
                           baseline + subscript_metrics.ascent() * 0.55),
            subscript,
        )


class OvenWindow(QtWidgets.QMainWindow):
    """Oven controller window."""

    def __init__(self) -> None:
        super().__init__()
        _load_ui_fonts()
        QtWidgets.QApplication.instance().setFont(QtGui.QFont("IBM Plex Sans", 10))
        self.setWindowTitle("Oven Controller")
        self._build_toolbar()
        self._build_layout()
        for control in (self.target_input, self.kp_input, self.ki_input, self.kd_input):
            control.lineEdit().installEventFilter(self)
        self._apply_visual_theme()
        self._set_graph_auto_range_icon()
        self.resize(1280, 760)
        self.pid_toggle.setChecked(True)
        self.centralWidget().layout().activate()
        self.adjustSize()
        expanded_window_height = max(760, self.height(), self.minimumSizeHint().height())
        self.pid_toggle.setChecked(False)
        self.setMinimumHeight(expanded_window_height)
        self.resize(1280, expanded_window_height)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if (
            event.type() == QtCore.QEvent.Type.FocusOut
            and isinstance(watched, QtWidgets.QLineEdit)
        ):
            watched.deselect()
        return super().eventFilter(watched, event)

    def _apply_visual_theme(self) -> None:
        """Match the revised HTML reference without changing the window geometry."""
        self.setFont(QtGui.QFont("IBM Plex Sans", 10))
        self.setStyleSheet("""
            QMainWindow { background-color: #15171b; color: #e4e7eb;
                font-family: "IBM Plex Sans"; font-size: 10pt; }
            QWidget { color: #e4e7eb; font-family: "IBM Plex Sans"; font-size: 10pt; }
            QWidget#centralWidget { background-color: #15171b; }
            QToolBar { background: #1a1d22; border: none;
                border-bottom: 1px solid #262a31; spacing: 8px; padding: 4px 20px; }
            QToolBar::separator { width: 1px; height: 28px; background: #2e333b; margin: 0 6px; }
            QToolBar QToolButton { min-height: 40px; padding: 0 14px; border-radius: 8px;
                border: 1px solid #2e333b; background: #1f2329; color: #e4e7eb;
                font-family: "IBM Plex Sans"; font-size: 10pt; font-weight: 500; }
            QToolBar QToolButton:hover { background: #262b32; }
            QToolBar QToolButton:pressed { background: #2e333b; }
            QToolBar QToolButton#runToolButton { background: #2f8a5f; border-color: #2f8a5f; color: white; font-weight: 600; }
            QToolBar QToolButton#runToolButton:hover { background: #28764f; }
            QGroupBox { background: #1c1f24; border: 1px solid #262a31; border-radius: 12px;
                margin-top: 0px; padding-top: 0px; color: #9aa1ab; font-weight: 500; }
            QGroupBox::title { subcontrol-origin: margin; left: 18px; padding: 0 4px; }
            QLineEdit, QDoubleSpinBox, QSpinBox { min-height: 38px; padding: 0 10px;
                border-radius: 6px; border: 1px solid #343a43; background: #15181c;
                color: #e4e7eb; font-family: "IBM Plex Mono"; font-weight: 500; }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                subcontrol-origin: border; subcontrol-position: top right; width: 24px; height: 18px;
                border-left: 1px solid #343a43; background: #1a1d22; }
            QDoubleSpinBox::down-button { subcontrol-position: bottom right; }
            QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background: #262b32; }
            QPushButton { border: 1px solid #343a43; border-radius: 6px; padding: 7px 10px;
                background: #1f2329; color: #e4e7eb; }
            QPushButton:hover { background: #262b32; border-color: #454c57; }
            QPushButton:pressed { background: #2e333b; }
            QMenu { background: #1c1f24; color: #e4e7eb; border: 1px solid #343a43; }
            QMenu::item { padding: 6px 24px; }
            QMenu::item:selected { background: #262b32; }
            QFrame#plotPanel { background: #1c1f24; border: 1px solid #262a31; border-radius: 12px; }
        """)
    def _set_graph_auto_range_icon(self) -> None:
        """Use a fit-to-view glyph on Pyqtgraph's built-in auto-range button."""
        pixmap = QtGui.QPixmap(24, 24)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor("#e8b33d"), 2.4)
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
        self._set_graph_time_extent(full_x_max)
        self._set_graph_x_range(full_x_max)
        self.graph_item.setYRange(0, MAX_TEMPERATURE_DISPLAY_C, padding=0)
        self.duty_view.setYRange(0, MAX_OUTPUT_PERCENT, padding=0)

    def _restore_empty_graph_range(self, *_args) -> None:
        """Give auto-range a useful default when the graph has no samples yet."""
        x_data, _y_data = self.temperature_curve.getData()
        if x_data is not None and len(x_data) > 0:
            return
        self._set_graph_time_extent(GRAPH_WINDOW_S)
        self._set_graph_x_range(GRAPH_WINDOW_S)
        self.graph_item.setYRange(0, MAX_TEMPERATURE_DISPLAY_C, padding=0)
        self.duty_view.setYRange(0, MAX_OUTPUT_PERCENT, padding=0)

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Process")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setIconSize(QtCore.QSize(22, 22))
        toolbar.setStyleSheet(
            "QToolButton { min-height: 40px; border: 1px solid #2e333b; border-radius: 8px; padding: 0 14px; background-color: #1f2329; color: #e4e7eb; font-size: 10pt; font-weight: 500; }"
            "QToolButton:hover { background-color: #262b32; border-color: #9aa1ab; }"
            "QToolButton:pressed { background-color: #2e333b; border-color: #454c57; }"
        )
        action_specs = (
            ("Run", self.start_process, "run", "#6fbf98"),
            ("Stop", self.stop_process, "stop", "#b8423a"),
            ("Reset", self.reset_process, "reset", "#e8b33d"),
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
            action_button = toolbar.widgetForAction(action)
            if action_button is not None and label == "Run":
                action_button.setObjectName("runToolButton")
            if label == "Run":
                action.setShortcut(QtGui.QKeySequence("F5"))
                action.setShortcutContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
            elif label == "Stop":
                action.setShortcut(QtGui.QKeySequence("Esc"))
                action.setShortcutContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)

        toolbar.addSeparator()
        self.event_log_action = QtGui.QAction("Event log", self)
        self.event_log_action.setIcon(self._toolbar_icon("events", "#5aa9e6"))
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
        self.file_actions["Import run"].setShortcut(QtGui.QKeySequence("Ctrl+O"))
        self.file_actions["Export run"].setShortcut(QtGui.QKeySequence("Ctrl+S"))
        self.file_button = QtWidgets.QToolButton(self)
        self.file_button.setText("File  ▾")
        self.file_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.file_button.setIcon(self._toolbar_icon("folder", "#5aa9e6"))
        self.file_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.file_button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
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
        self.manual_button.setText("Manual controls  ▾")
        self.manual_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.manual_button.setIcon(self._toolbar_icon("controls", "#e8b33d"))
        self.manual_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.manual_button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
        self.manual_button.setMenu(self.manual_menu)
        self.manual_button.setToolTip("Open door, close door, read temperature, or control the heater")
        toolbar.addWidget(self.manual_button)

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
        elif symbol == "folder":
            # A classic tabbed folder silhouette for run and settings files.
            folder = QtGui.QPainterPath(QtCore.QPointF(3, 7))
            folder.lineTo(9, 7)
            folder.lineTo(11, 10)
            folder.lineTo(21, 10)
            folder.lineTo(19, 19)
            folder.quadTo(18.8, 20, 17.5, 20)
            folder.lineTo(4.5, 20)
            folder.quadTo(3, 20, 3, 18.5)
            folder.closeSubpath()
            painter.setBrush(QtGui.QColor(accent))
            painter.drawPath(folder)
        elif symbol == "controls":
            # Three distinct adjustment tracks, the familiar tune/controls glyph.
            for x, knob_y in ((6, 8), (12, 15), (18, 10)):
                painter.drawLine(QtCore.QPointF(x, 4), QtCore.QPointF(x, knob_y - 2.5))
                painter.drawLine(QtCore.QPointF(x, knob_y + 2.5), QtCore.QPointF(x, 20))
                painter.setBrush(QtGui.QColor(accent))
                painter.drawEllipse(QtCore.QPointF(x, knob_y), 2.5, 2.5)
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

        painter.end()
        return QtGui.QIcon(pixmap)

    def _build_layout(self) -> None:
        central = QtWidgets.QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(18)

        graph_column = QtWidgets.QVBoxLayout()
        graph_column.setContentsMargins(0, 0, 0, 0)
        graph_column.setSpacing(16)
        self.graph = pg.PlotWidget()
        self.graph.setMinimumWidth(560)
        self.graph.setMinimumHeight(300)
        self.graph.setBackground("#1c1f24")
        self.graph.showGrid(x=False, y=False)
        self.graph.setLabel("left", "")
        self.graph.setLabel("bottom", "Elapsed time (s)", **{
            "color": "#9aa1ab",
            "font-size": "10pt",
            "font-family": "IBM Plex Mono",
            "font-weight": "400",
        })
        self.graph_item = self.graph.getPlotItem()
        self.graph_item.layout.setContentsMargins(12, 8, 12, 0)
        self.graph_item.showAxis("right")
        self.graph_item.setLabel("right", "")
        self.grid_item = pg.GridItem(pen=pg.mkPen("#343a43", width=1), textPen=None)
        self.grid_item.setTickSpacing(x=[20.0], y=[10.0])
        self.grid_item.setZValue(-20)
        self.graph_item.addItem(self.grid_item, ignoreBounds=True)
        axis_font = QtGui.QFont()
        axis_font.setFamily("IBM Plex Mono")
        axis_font.setPointSize(10)
        axis_font.setWeight(QtGui.QFont.Weight.Normal)
        left_axis = self.graph_item.getAxis("left")
        bottom_axis = self.graph_item.getAxis("bottom")
        for axis_name in ("left", "bottom", "top"):
            axis = self.graph_item.getAxis(axis_name)
            axis.setPen(pg.mkPen("#454c57"))
            axis.setTextPen(pg.mkPen("#9aa1ab" if axis_name == "bottom" else "#f08a7c"))
            axis.setStyle(showValues=True, tickFont=axis_font, tickTextOffset=8)
        left_axis.setTicks([[(value, str(value)) for value in range(0, 51, 10)]])
        right_axis = self.graph_item.getAxis("right")
        right_axis.setPen(pg.mkPen("#454c57"))
        right_axis.setTextPen(pg.mkPen("#7fb8ea"))
        right_axis.setStyle(showValues=True, tickFont=axis_font, tickTextOffset=8)
        right_axis.setTicks([[(value, str(value)) for value in range(0, 101, 20)]])
        left_axis.setWidth(72)
        bottom_axis.setStyle(showValues=True, tickFont=axis_font, tickTextOffset=8)
        bottom_axis.setTicks([[(value, str(value)) for value in range(10, int(GRAPH_WINDOW_S), 10)]])
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
        self.x_axis_min_label = QtWidgets.QLabel("0", self.graph.viewport())
        self.x_axis_max_label = QtWidgets.QLabel("120", self.graph.viewport())
        for endpoint_label in (self.x_axis_min_label, self.x_axis_max_label):
            endpoint_label.setStyleSheet(
                "color: #9aa1ab; background: transparent; "
                "font-family: IBM Plex Mono; font-size: 10pt; padding: 0;"
            )
            endpoint_label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            endpoint_label.adjustSize()
            endpoint_label.show()
        self._set_graph_time_extent(GRAPH_WINDOW_S)
        self._set_graph_x_range(GRAPH_WINDOW_S)
        self.graph_item.vb.sigResized.connect(self._resize_duty_view)
        self.graph_item.vb.sigResized.connect(self._position_x_endpoint_labels)
        self.temperature_curve = self.graph.plot(name="Temperature", pen=pg.mkPen("#f0715f", width=3))
        self.temperature_curve.setZValue(10)
        self.temperature_marker_items: list[pg.GraphicsObject] = []
        self._temperature_marker_points: list[tuple[float, float]] = []
        self._temperature_marker_connectors: list[pg.PlotDataItem] = []
        target_pen = pg.mkPen(
            (255, 209, 102, 160),
            width=3,
            style=QtCore.Qt.PenStyle.CustomDashLine,
        )
        target_pen.setDashPattern([4.0, 4.5])
        self.goal_curve = self.graph.plot(name="Target", pen=target_pen)
        self.goal_curve.setZValue(5)
        self.planned_curve = self.graph.plot(pen=target_pen)
        self.planned_curve.setZValue(4)
        self.hold_region = pg.LinearRegionItem(values=(30.0, 30.0 + DEFAULT_HOLD_DURATION_S),
                                                movable=False, brush=pg.mkBrush(232, 179, 61, 20),
                                                pen=pg.mkPen(None))
        self.graph_item.addItem(self.hold_region, ignoreBounds=True)
        self.hold_region.setZValue(-10)
        self.duty_curve = pg.PlotCurveItem(pen=pg.mkPen("#5aa9e6", width=3))
        self.duty_view.addItem(self.duty_curve)

        legend_row = QtWidgets.QHBoxLayout()
        legend_row.setContentsMargins(0, 0, 0, 0)
        legend_row.setSpacing(22)
        legend_specs = (
            ("temperature", "Temperature (°C)"),
            ("target", "Target"),
            ("output", "Heater output (%)"),
            ("hold", "Hold window"),
        )
        for marker_kind, label_text in legend_specs:
            legend_item = QtWidgets.QWidget()
            item_layout = QtWidgets.QHBoxLayout(legend_item)
            item_layout.setContentsMargins(0, 0, 0, 0)
            item_layout.setSpacing(8)
            marker = TargetLegendMarker(legend_item) if marker_kind == "target" else QtWidgets.QFrame(legend_item)
            if marker_kind == "temperature":
                marker.setFixedSize(22, 3)
                marker.setStyleSheet("background: #f0715f; border-radius: 2px;")
            elif marker_kind == "target":
                pass
            elif marker_kind == "output":
                marker.setFixedSize(22, 3)
                marker.setStyleSheet("background: #5aa9e6; border-radius: 2px;")
            else:
                marker.setFixedSize(18, 12)
                marker.setStyleSheet("background: rgba(232, 179, 61, 20); border-radius: 2px;")
            label = QtWidgets.QLabel(label_text, legend_item)
            label.setStyleSheet("color: #c3c8cf; font-size: 10pt; font-weight: 400;")
            item_layout.addWidget(marker, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
            item_layout.addWidget(label)
            legend_row.addWidget(legend_item)
        legend_row.addStretch(1)
        graph_column.addLayout(legend_row)
        graph_column.addWidget(self.graph, 1)
        plot_panel = QtWidgets.QFrame()
        plot_panel.setObjectName("plotPanel")
        plot_panel_layout = QtWidgets.QVBoxLayout(plot_panel)
        plot_panel_layout.setContentsMargins(20, 18, 20, 18)
        plot_panel_layout.setSpacing(12)
        plot_panel_layout.addLayout(graph_column)
        layout.addWidget(plot_panel, 1)
        self.graph_item.vb.sigRangeChanged.connect(self._fit_visible_temperature_annotations)
        self.graph_item.vb.sigResized.connect(self._fit_visible_temperature_annotations)

        side = QtWidgets.QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(8)
        side_widget = QtWidgets.QWidget()
        side_widget.setFixedWidth(320)
        side_widget.setLayout(side)
        layout.addWidget(side_widget)
        metrics_box = QtWidgets.QGroupBox()
        metrics_box.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        metrics = QtWidgets.QVBoxLayout(metrics_box)
        metrics.setContentsMargins(12, 12, 12, 12)
        metrics.setSpacing(8)
        metrics_top = QtWidgets.QGridLayout()
        metrics_top.setContentsMargins(0, 0, 0, 0)
        metrics_top.setHorizontalSpacing(12)
        metric_columns = []
        for title in ("Temperature", "Heater output"):
            column = QtWidgets.QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(4)
            title_label = QtWidgets.QLabel(title)
            title_label.setStyleSheet("color: #9aa1ab; font-size: 10pt; font-weight: 400;")
            column.addWidget(title_label)
            metric_columns.append(column)
        self.temperature_label = QtWidgets.QLabel("--.- °C")
        self.temperature_label.setStyleSheet("color: #f39486; font-family: IBM Plex Mono; font-size: 25.5pt; font-weight: 600;")
        self.output_label = QtWidgets.QLabel("-- %")
        self.output_label.setStyleSheet("color: #7fb8ea; font-family: IBM Plex Mono; font-size: 25.5pt; font-weight: 600;")
        metric_columns[0].addWidget(self.temperature_label)
        metric_columns[1].addWidget(self.output_label)
        self.target_status_label = QtWidgets.QLabel(f"target {DEFAULT_TARGET_TEMPERATURE_C:.1f} °C")
        self.target_status_label.setStyleSheet("color: #9aa1ab; font-family: IBM Plex Mono; font-size: 10pt;")
        self.duty_cycle_label = QtWidgets.QLabel("duty cycle")
        self.duty_cycle_label.setStyleSheet("color: #9aa1ab; font-family: IBM Plex Mono; font-size: 10pt;")
        metric_columns[0].addWidget(self.target_status_label)
        metric_columns[1].addWidget(self.duty_cycle_label)
        metrics_top.addLayout(metric_columns[0], 0, 0)
        metrics_top.addLayout(metric_columns[1], 0, 1)
        metrics.addLayout(metrics_top)

        metrics_divider = QtWidgets.QFrame()
        metrics_divider.setObjectName("metricsDivider")
        metrics_divider.setFixedHeight(1)
        metrics_divider.setStyleSheet("background: #2a2f37;")
        metrics.addWidget(metrics_divider)
        hold_row = QtWidgets.QHBoxLayout()
        hold_label = QtWidgets.QLabel("Hold remaining")
        hold_label.setStyleSheet("color: #b4bac3; font-family: IBM Plex Sans; font-size: 10.5pt; font-weight: 400;")
        self.hold_remaining_label = QtWidgets.QLabel("--:--")
        self.hold_remaining_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.hold_remaining_label.setStyleSheet("color: #e8b33d; font-family: IBM Plex Mono; font-size: 16.5pt; font-weight: 600;")
        hold_row.addWidget(hold_label)
        hold_row.addStretch(1)
        hold_row.addWidget(self.hold_remaining_label)
        metrics.addLayout(hold_row)

        bottom_row = QtWidgets.QHBoxLayout()
        self.elapsed_label = QtWidgets.QLabel(
            "Elapsed <span style='font-family: IBM Plex Mono; color: #c3c8cf;'>00:00.0</span>"
        )
        self.elapsed_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.elapsed_label.setStyleSheet(
            "color: #9aa1ab; font-family: IBM Plex Sans; font-size: 10pt; font-weight: 400;"
        )
        self.door_label = QtWidgets.QLabel("Door: Unknown")
        self.door_label.setStyleSheet(
            "color: #6fbf98; font-family: IBM Plex Sans; font-size: 10pt; font-weight: 400;"
        )
        bottom_row.addWidget(self.elapsed_label)
        bottom_row.addStretch(1)
        bottom_row.addWidget(self.door_label)
        metrics.addLayout(bottom_row)
        side.addWidget(metrics_box)

        status_box = QtWidgets.QGroupBox()
        status_box.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        status = QtWidgets.QVBoxLayout(status_box)
        status.setContentsMargins(12, 12, 12, 12)
        status.setSpacing(8)
        stage_title = QtWidgets.QLabel("Stage")
        stage_title.setStyleSheet("color: #9aa1ab; font-size: 10pt; font-weight: 400;")
        status.addWidget(stage_title)
        self.progress = StageProgress()
        status.addWidget(self.progress)
        side.addWidget(status_box)

        controls_box = QtWidgets.QGroupBox()
        controls = QtWidgets.QVBoxLayout(controls_box)
        controls.setContentsMargins(12, 12, 12, 12)
        controls.setSpacing(8)
        process_title = QtWidgets.QLabel("Process")
        process_title.setStyleSheet("color: #9aa1ab; font-size: 10pt; font-weight: 400;")
        controls.addWidget(process_title)

        self.target_input = self._spin(config.MIN_SETPOINT, config.MAX_SETPOINT, DEFAULT_TARGET_TEMPERATURE_C, decimals=1)
        self.target_input.setSingleStep(0.1)
        self.target_input.setFixedHeight(40)
        self.hold_input = DurationSpinBox()
        self.hold_input.setRange(1.0, MAX_DURATION_S)
        self.hold_input.setDecimals(0)
        self.hold_input.setSingleStep(1.0)
        self.hold_input.setValue(DEFAULT_HOLD_DURATION_S)
        self.hold_input.setFixedHeight(40)

        def add_process_field(label_text: str, control: QtWidgets.QWidget) -> None:
            field = QtWidgets.QHBoxLayout()
            field.setContentsMargins(0, 0, 0, 0)
            field.setSpacing(12)
            label = QtWidgets.QLabel(label_text)
            label.setStyleSheet("color: #b4bac3; font-size: 10.5pt;")
            control.setFixedWidth(120)
            field.addWidget(label)
            field.addStretch(1)
            field.addWidget(control)
            controls.addLayout(field)

        add_process_field("Target (°C)", self.target_input)
        add_process_field("Hold (m:ss)", self.hold_input)

        pid_section = QtWidgets.QFrame()
        pid_section.setObjectName("pidSection")
        pid_section.setStyleSheet("QFrame#pidSection { border-top: 1px solid #2a2f37; }")
        pid_section_layout = QtWidgets.QVBoxLayout(pid_section)
        pid_section_layout.setContentsMargins(0, 6, 0, 0)
        pid_section_layout.setSpacing(6)
        self.pid_toggle = QtWidgets.QToolButton()
        self.pid_toggle.setText("Advanced · PID tuning")
        self.pid_toggle.setCheckable(True)
        self.pid_toggle.setChecked(False)
        self.pid_toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.pid_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.pid_toggle.setMinimumHeight(32)
        self.pid_toggle.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 0; color: #c3c8cf; text-align: left; font-size: 10.5pt; }"
            "QToolButton:hover { color: #e4e7eb; background: transparent; }"
        )
        pid_section_layout.addWidget(self.pid_toggle)

        self.kp_input = self._spin(0.0, 10.0, DEFAULT_KP, decimals=2, step=0.01)
        self.ki_input = self._spin(0.0, 10.0, DEFAULT_KI, decimals=3, step=0.001)
        self.kd_input = self._spin(0.0, 10.0, DEFAULT_KD, decimals=2, step=0.01)
        for value_input in (self.kp_input, self.ki_input, self.kd_input):
            value_input.setFixedSize(120, 40)
        self.kp_enable = self._pid_checkbox("Kp", "Enable proportional action")
        self.ki_enable = self._pid_checkbox("Ki", "Enable integral action")
        self.kd_enable = self._pid_checkbox("Kd", "Enable derivative action")
        self.pid_enable_inputs = (
            (self.kp_enable, self.kp_input),
            (self.ki_enable, self.ki_input),
            (self.kd_enable, self.kd_input),
        )
        for checkbox, value_input in self.pid_enable_inputs:
            checkbox.toggled.connect(value_input.setEnabled)
            value_input.setStyleSheet(
                "QDoubleSpinBox:disabled { color: #9aa1ab; background-color: #15181c; border-color: #343a43; }"
            )
        self.pid_controls = QtWidgets.QWidget()
        pid_controls_layout = QtWidgets.QVBoxLayout(self.pid_controls)
        pid_controls_layout.setContentsMargins(0, 0, 0, 0)
        pid_controls_layout.setSpacing(6)
        pid_controls_layout.addWidget(self._pid_control_row(self.kp_enable, self.kp_input))
        pid_controls_layout.addWidget(self._pid_control_row(self.ki_enable, self.ki_input))
        pid_controls_layout.addWidget(self._pid_control_row(self.kd_enable, self.kd_input))
        self.restore_pid_button = QtWidgets.QPushButton("Restore defaults")
        self.restore_pid_button.setStyleSheet(
            "QPushButton { background: transparent; border: none; padding: 0 4px; color: #7fb8ea; "
            "text-align: left; font-family: 'IBM Plex Sans'; font-size: 10.5pt; font-weight: 500; }"
            "QPushButton:hover { background: transparent; color: #a9d0f2; }"
        )
        self.restore_pid_button.clicked.connect(self.restore_pid_defaults)
        pid_controls_layout.addWidget(self.restore_pid_button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        self.pid_controls.setVisible(False)
        pid_section_layout.addWidget(self.pid_controls)
        self.pid_toggle.toggled.connect(self.pid_controls.setVisible)
        self.pid_toggle.toggled.connect(
            lambda expanded: self.pid_toggle.setArrowType(
                QtCore.Qt.ArrowType.DownArrow if expanded else QtCore.Qt.ArrowType.RightArrow
            )
        )
        controls.addWidget(pid_section)
        side.addWidget(controls_box)
        side.addStretch(1)

        self.target_input.valueChanged.connect(self._update_profile_preview)
        self.hold_input.valueChanged.connect(self._update_profile_preview)
        self._update_profile_preview()

    def _update_profile_preview(self, *_args) -> None:
        """Keep the idle target line flat across the visible time axis."""
        target = self.target_input.value()
        hold_s = self.hold_input.value()
        warmup_s = 30.0
        goal_x, _goal_y = self.goal_curve.getData()
        if goal_x is None or len(goal_x) == 0:
            x_min, x_max = self.graph_item.vb.viewRange()[0]
            self.planned_curve.setData([x_min, x_max], [target, target])
        else:
            self.planned_curve.setData([], [])
        self.hold_region.setRegion((warmup_s, warmup_s + hold_s))
        self.target_status_label.setText(f"target {target:.1f} °C")

    @staticmethod
    def _spin(minimum: float, maximum: float, value: float, suffix: str = "", decimals: int = 3, step: float | None = None) -> QtWidgets.QDoubleSpinBox:
        control = QtWidgets.QDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setDecimals(decimals)
        control.setValue(value)
        control.setSingleStep(step if step is not None else (1.0 if decimals == 0 else 0.01))
        control.setSuffix(suffix)
        return control

    @staticmethod
    def _pid_checkbox(label: str, tooltip: str) -> QtWidgets.QCheckBox:
        checkbox = PidTermCheckBox(label)
        checkbox.setChecked(True)
        checkbox.setToolTip(tooltip)
        return checkbox

    @staticmethod
    def _pid_control_row(checkbox: QtWidgets.QCheckBox, value_input: QtWidgets.QDoubleSpinBox) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(checkbox)
        layout.addStretch(1)
        layout.addWidget(value_input)
        return row

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
            fill=pg.mkBrush("#1c1f24"),
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
            "#7fb8ea",
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
        x_max = math.ceil(max(GRAPH_WINDOW_S, end_s) / 20.0) * 20.0
        self.graph_item.vb.setLimits(xMin=0, xMax=x_max, minXRange=5, maxXRange=x_max)
        self.duty_view.setLimits(xMin=0, xMax=x_max, minXRange=5, maxXRange=x_max)
        self._set_time_axis_ticks(x_max)

    def _set_graph_x_range(self, x_max: float) -> None:
        """Keep the graph border aligned with zero and the final grid line."""
        x_max = math.ceil(max(GRAPH_WINDOW_S, x_max) / 20.0) * 20.0
        self.graph_item.setXRange(0, x_max, padding=0)

    def _set_time_axis_ticks(self, x_max: float) -> None:
        """Keep zero and the current right edge labeled on the time axis."""
        last_regular_tick = int(x_max // 10) * 10
        ticks = [(value, str(value)) for value in range(10, last_regular_tick, 10)]
        self.graph_item.getAxis("bottom").setTicks([ticks])
        self.x_axis_min_label.setText("0")
        self.x_axis_max_label.setText(f"{x_max:g}")
        self.x_axis_min_label.adjustSize()
        self.x_axis_max_label.adjustSize()
        self._position_x_endpoint_labels()

    def _position_x_endpoint_labels(self, *_args) -> None:
        """Place endpoint text at the plot borders, clear of AxisItem clipping."""
        view_rect = self.graph_item.vb.sceneBoundingRect()
        axis_rect = self.graph_item.getAxis("bottom").sceneBoundingRect()
        label_y = self.graph.mapFromScene(QtCore.QPointF(view_rect.center().x(), axis_rect.center().y())).y()
        left_x = self.graph.mapFromScene(QtCore.QPointF(view_rect.left(), view_rect.center().y())).x()
        right_x = self.graph.mapFromScene(QtCore.QPointF(view_rect.right(), view_rect.center().y())).x()
        # Keep the endpoint labels inside the viewport; centering them on its
        # edge clips half of each label. Their inner edges still meet the plot
        # boundaries, which remain aligned with the first and last grid lines.
        self.x_axis_min_label.move(left_x, label_y - self.x_axis_min_label.height() // 2)
        self.x_axis_max_label.move(right_x - self.x_axis_max_label.width(),
                                   label_y - self.x_axis_max_label.height() // 2)
        self.x_axis_min_label.raise_()
        self.x_axis_max_label.raise_()

    def start_process(self) -> None:
        self._clear_temperature_markers()

    def stop_process(self) -> None:
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
                          reason="Process reached its planned end.", color="#6fbf98"),
        "Manually stopped": dict(outcome="Stopped by operator", duration=104.8, target=45.0,
                                 peak=(46.1, 81.0), low=(42.7, 37.0),
                                 reason="Operator requested a stop during the holding stage.", color="#e8b33d"),
        "Forced stop": dict(outcome="Emergency stop", duration=62.3, target=45.0,
                            peak=(43.8, 58.0), low=(31.4, 12.0),
                            reason="Emergency stop was activated; heater output is off.", color="#b8423a"),
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
        "QFrame#runSummaryPreviewCard { background: #2a3139; border: 1px solid #454c57; border-radius: 6px; }"
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
        window._set_graph_x_range(max(GRAPH_WINDOW_S, end_s))
        x_min, x_max = window.graph_item.vb.viewRange()[0]
        window.goal_curve.setData([x_min, x_max], [example["target"], example["target"]])

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
        note.setStyleSheet("color: #c3c8cf;")
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
