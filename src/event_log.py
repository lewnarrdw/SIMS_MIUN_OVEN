"""Run event log: formatting helpers and the colour-coded event log dialog."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from PySide6 import QtCore, QtGui, QtWidgets

from application import FAULT_DESCRIPTIONS

LEVELS = ("error", "warning", "success", "info")
# Text colour, row tint (None keeps the table background) and label per level.
LEVEL_STYLES = {
    "error": ("#ff8a80", "#3a1a1d", "ERROR"),
    "warning": ("#f4c542", "#332c16", "WARNING"),
    "success": ("#6fbf98", None, "OK"),
    "info": ("#9aa1ab", None, "Info"),
}
# Info is most of the log, so its message stays plain light grey.
INFO_MESSAGE_COLOR = "#c9ced6"
# Kept short so green always means something went right or recovered.
SUCCESS_EVENT_TYPES = frozenset({"run_completed", "fault_cleared", "estop_released"})
COLUMNS = ("Time", "Run time", "Level", "Event")
REFRESH_INTERVAL_MS = 500
# Prefixes the application puts in front of a fault description.
FAULT_MESSAGE_PREFIXES = ("Run aborted: ", "Safety fault: ", "Fault cleared: ")
LONGEST_FIXED_MESSAGES = (
    "Start rejected: Emergency stop is active; release it before using the heater or door",
    "Heater on rejected: Safety fault is active; resolve it before starting or heating",
    "Start rejected: Wait for the cool-down to finish before starting a new run",
    "Run restarted after previous run ended: emergency stop (E-Stop)",
)


def event_severity(event: dict) -> str | None:
    """Return an event's severity; older run files only mark faults and E-Stops by type."""
    severity = event.get("severity")
    if severity is None:
        severity = {"run_faulted": "error", "emergency_stop": "error"}.get(event.get("type"))
    return severity


def event_level(event: dict) -> str:
    """Return error, warning, success or info for display.

    Success is display-only (derived from the event type, so older run files get it too)
    and never raises the attention badge, which uses event_severity().
    """
    severity = event_severity(event)
    if severity in ("error", "warning"):
        return severity
    return "success" if event.get("type") in SUCCESS_EVENT_TYPES else "info"


def format_clock_time(event: dict) -> str:
    """Wall-clock time of day, or an empty string for events from older run files."""
    try:
        return datetime.fromisoformat(event["time"]).strftime("%H:%M:%S")
    except (KeyError, TypeError, ValueError):
        return ""


def format_run_time(event: dict) -> str:
    """Time since the run started, or an empty string for events outside a run."""
    elapsed = event.get("run_elapsed_s")
    if elapsed is None and "time" not in event:
        # Older run files only stored the time since the run started.
        elapsed = event.get("elapsed_s")
    if elapsed is None:
        return ""
    minutes, seconds = divmod(max(0.0, float(elapsed)), 60.0)
    return f"+{int(minutes):02d}:{seconds:04.1f}"


def event_message(event: dict) -> str:
    message = event.get("description", "")
    changes = event.get("changes", {})
    if changes:
        values = ", ".join(f"{key}: {change['from']} -> {change['to']}" for key, change in changes.items())
        message += f" ({values})"
    return message


def possible_messages() -> list[str]:
    """Every fault message the application can log, plus the longest fixed messages."""
    messages = list(LONGEST_FIXED_MESSAGES)
    for description in FAULT_DESCRIPTIONS.values():
        messages.append(FAULT_MESSAGE_PREFIXES[0] + description.lower())
        messages.extend(prefix + description for prefix in FAULT_MESSAGE_PREFIXES[1:])
    return messages


def ordered_events(events: Iterable[dict]) -> list[dict]:
    """Chronological order; events without a wall-clock time keep their recorded order."""
    events = list(events)
    if all("time" in event for event in events):
        events.sort(key=lambda event: event["time"])
    return events


class EventLogDialog(QtWidgets.QDialog):
    """Colour-coded event table that follows new events while it is open."""

    def __init__(self, events_provider: Callable[[], list[dict]], live: bool,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._events_provider = events_provider
        self._rows: list[tuple] | None = None
        self.setWindowTitle("Run events")
        layout = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        # On a screen narrower than the longest message, wrap instead of cutting it off.
        self.table.setWordWrap(True)
        self.table.setTextElideMode(QtCore.Qt.TextElideMode.ElideNone)
        self.table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setDefaultAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        header.sectionResized.connect(lambda *_: self._fit_rows())
        self.table.setStyleSheet(
            "QTableWidget { background: #1c1f24; border: 1px solid #262a31; border-radius: 8px;"
            " color: #c9ced6; font-size: 10pt; }"
            "QTableWidget::item { padding: 3px 6px; }"
            "QTableWidget::item:selected { background: #2e3540; }"
            "QHeaderView::section { background: #1f2329; color: #9aa1ab; border: none;"
            " border-bottom: 1px solid #343a43; padding: 5px 6px; font-weight: 600; }"
        )
        layout.addWidget(self.table)
        self.empty_label = QtWidgets.QLabel("No events recorded for this run.", self)
        self.empty_label.setStyleSheet("color: #9aa1ab;")
        layout.addWidget(self.empty_label)
        buttons = QtWidgets.QDialogButtonBox(parent=self)
        # Plain text: the standard Close button underlines a C that has no shortcut.
        buttons.addButton("Close", QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()
        self._fit_to_messages()
        self.scroll_to_latest()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        if live:
            self._timer.start()

    def _fit_to_messages(self) -> None:
        """Size the Event column for the longest possible message, within the screen."""
        metrics = QtGui.QFontMetrics(self.table.font())
        bold = QtGui.QFont(self.table.font())
        bold.setBold(True)
        bold_metrics = QtGui.QFontMetrics(bold)
        padding = 28
        widths = [
            metrics.horizontalAdvance("00:00:00") + padding,
            metrics.horizontalAdvance("+000:00.0") + padding,
            bold_metrics.horizontalAdvance("WARNING") + padding,
        ]
        messages = possible_messages() + [row[3] for row in self._rows or ()]
        widths.append(max(metrics.horizontalAdvance(message) for message in messages) + padding)
        for column, width in enumerate(widths[:-1]):
            self.table.setColumnWidth(column, width)
        content_width = sum(widths) + self.table.verticalScrollBar().sizeHint().width() + 48
        screen = (self.parentWidget().screen() if self.parentWidget() else QtGui.QGuiApplication.primaryScreen())
        available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1280, 800)
        self.resize(min(content_width, int(available.width() * 0.95)),
                    min(480, int(available.height() * 0.9)))

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._fit_rows)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        QtCore.QTimer.singleShot(0, self._fit_rows)

    def _fit_rows(self) -> None:
        """Recompute wrapped row heights for the current column widths, keeping the view on the latest event."""
        follow = self._is_at_bottom()
        self.table.resizeRowsToContents()
        if follow:
            self.scroll_to_latest()

    def _is_at_bottom(self) -> bool:
        bar = self.table.verticalScrollBar()
        return bar.value() >= bar.maximum() - 2

    def scroll_to_latest(self) -> None:
        self.table.scrollToBottom()

    def refresh(self) -> None:
        rows = [(format_clock_time(event), format_run_time(event), event_level(event), event_message(event))
                for event in ordered_events(self._events_provider())]
        if rows == self._rows:
            return
        follow = not self._rows or self._is_at_bottom()
        self._rows = rows
        self.table.setRowCount(len(rows))
        bold = QtGui.QFont(self.table.font())
        bold.setBold(True)
        for row, (clock, run_time, level, message) in enumerate(rows):
            color, tint, label = LEVEL_STYLES[level]
            for column, text in enumerate((clock, run_time, label, message)):
                item = QtWidgets.QTableWidgetItem(text)
                if column < 2:
                    item.setForeground(QtGui.QColor("#9aa1ab"))
                elif column == 3 and level == "info":
                    item.setForeground(QtGui.QColor(INFO_MESSAGE_COLOR))
                else:
                    item.setForeground(QtGui.QColor(color))
                if tint:
                    item.setBackground(QtGui.QColor(tint))
                if column == 2 and level != "info":
                    item.setFont(bold)
                self.table.setItem(row, column, item)
        self.table.resizeRowsToContents()
        self.table.setHidden(not rows)
        self.empty_label.setHidden(bool(rows))
        if follow:
            self.scroll_to_latest()
