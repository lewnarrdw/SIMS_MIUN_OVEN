"""The run summary window, styled like the event log."""
from __future__ import annotations

import html
import re

from PySide6 import QtCore, QtGui, QtWidgets

from run_summary import Metric, summary_text

VALUE_COLORS = {"good": "#6fbf98", "warning": "#f4c542", "error": "#ff8a80", None: "#e4e7eb"}
EMPTY_TITLE = "No run summary yet"
EMPTY_TEXT = "A summary opens here when a run finishes.\nTo review an earlier run, use File › Import run."
GAIN_NAME = re.compile(r"\bK([pid])\b")


def gain_subscripts_html(text: str) -> str | None:
    """The text as rich text with Kp, Ki and Kd subscripted; None when it names no gain."""
    if not GAIN_NAME.search(text):
        return None
    return GAIN_NAME.sub(r"K<sub>\1</sub>", html.escape(text))


def plain_button(text: str) -> QtWidgets.QPushButton:
    """A button without a mnemonic: no underlined letter for a shortcut that is not there."""
    button = QtWidgets.QPushButton(text)
    button.setAutoDefault(False)
    return button


def summary_icon(size: int, color: str) -> QtGui.QPixmap:
    """The toolbar's bar chart glyph, drawn at size for the empty state."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24.0, size / 24.0)
    pen = QtGui.QPen(QtGui.QColor(color), 1.4)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawLine(QtCore.QPointF(4, 20), QtCore.QPointF(20, 20))
    for x, top in ((7, 13), (12, 6), (17, 10)):
        painter.drawRoundedRect(QtCore.QRectF(x - 1.5, top, 3, 17 - top), 1, 1)
    painter.end()
    return pixmap


class RunSummaryDialog(QtWidgets.QDialog):
    """Key metrics of one run."""

    def __init__(self, metrics: list[Metric], live: bool, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.metrics = list(metrics)
        self.setWindowTitle("Run summary" + (" (run in progress)" if live else ""))
        layout = QtWidgets.QVBoxLayout(self)
        self.pages = QtWidgets.QStackedWidget(self)
        self.table = self._build_table()
        self.empty_page = self._build_empty_page()
        self.pages.addWidget(self.table)
        self.pages.addWidget(self.empty_page)
        self.pages.setCurrentWidget(self.table if self.metrics else self.empty_page)
        layout.addWidget(self.pages)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.copy_button = plain_button("Copy")
        self.copy_button.setToolTip("Copy the summary as text, e.g. for a report")
        self.copy_button.clicked.connect(self.copy_to_clipboard)
        self.copy_button.setVisible(bool(self.metrics))
        self.close_button = plain_button("Close")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self._fit_size()

    def _build_table(self) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(0, 2, self)
        table.setHorizontalHeaderLabels(("Metric", "Value"))
        table.verticalHeader().hide()
        table.setShowGrid(False)
        table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setDefaultAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        table.setStyleSheet(
            "QTableWidget { background: #1c1f24; border: 1px solid #262a31; border-radius: 8px;"
            " color: #c9ced6; font-size: 10pt; }"
            "QTableWidget::item { padding: 3px 6px; }"
            "QHeaderView::section { background: #1f2329; color: #9aa1ab; border: none;"
            " border-bottom: 1px solid #343a43; padding: 5px 6px; font-weight: 600; }"
        )
        bold = QtGui.QFont(table.font())
        bold.setBold(True)
        section = None
        for metric in self.metrics:
            if metric.section != section:
                section = metric.section
                row = table.rowCount()
                table.insertRow(row)
                item = QtWidgets.QTableWidgetItem(section)
                item.setFont(bold)
                item.setForeground(QtGui.QColor("#9aa1ab"))
                item.setBackground(QtGui.QColor("#22262c"))
                table.setItem(row, 0, item)
                table.setSpan(row, 0, 1, 2)
            row = table.rowCount()
            table.insertRow(row)
            label = QtWidgets.QTableWidgetItem(metric.label)
            value = QtWidgets.QTableWidgetItem(metric.value)
            value.setForeground(QtGui.QColor(VALUE_COLORS.get(metric.level, VALUE_COLORS[None])))
            if metric.level:
                value.setFont(bold)
            for item in (label, value):
                item.setToolTip(metric.hint)
            table.setItem(row, 0, label)
            table.setItem(row, 1, value)
            # A table item cannot show subscripts, so gains get a rich-text label instead.
            rich = gain_subscripts_html(metric.value)
            if rich is not None:
                value.setText("")
                cell = QtWidgets.QLabel(rich)
                cell.setTextFormat(QtCore.Qt.TextFormat.RichText)
                cell.setToolTip(metric.hint)
                color = VALUE_COLORS.get(metric.level, VALUE_COLORS[None])
                cell.setStyleSheet(f"color: {color}; background: transparent; padding: 3px 6px 3px 0; font-size: 10pt;")
                table.setCellWidget(row, 1, cell)
        table.resizeColumnToContents(0)
        table.setColumnWidth(0, table.columnWidth(0) + 24)
        return table

    def _build_empty_page(self) -> QtWidgets.QWidget:
        """Icon, title and hint centred in a panel matching the table's frame."""
        page = QtWidgets.QFrame(self)
        page.setObjectName("summaryEmpty")
        page.setStyleSheet("QFrame#summaryEmpty { background: #1c1f24; border: 1px solid #262a31; border-radius: 8px; }")
        column = QtWidgets.QVBoxLayout(page)
        column.setContentsMargins(32, 32, 32, 32)
        column.setSpacing(10)
        column.addStretch(1)
        icon = QtWidgets.QLabel(page)
        icon.setPixmap(summary_icon(48, "#5c626b"))
        icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
        title = QtWidgets.QLabel(EMPTY_TITLE, page)
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
        title.setStyleSheet("color: #e4e7eb; font-size: 12pt; font-weight: 600; background: transparent;")
        text = QtWidgets.QLabel(EMPTY_TEXT, page)
        text.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
        text.setWordWrap(True)
        text.setStyleSheet("color: #9aa1ab; font-size: 10pt; background: transparent;")
        for widget in (icon, title, text):
            column.addWidget(widget)
        column.addStretch(1)
        self.empty_title, self.empty_text = title, text
        return page

    def _fit_size(self) -> None:
        screen = self.parentWidget().screen() if self.parentWidget() else QtGui.QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1280, 800)
        if not self.metrics:
            self.resize(460, 320)
            return
        metrics = QtGui.QFontMetrics(self.table.font())
        value_width = max(metrics.horizontalAdvance(metric.value) for metric in self.metrics)
        width = self.table.columnWidth(0) + value_width + 90
        self.resize(min(max(width, 460), int(available.width() * 0.95)), min(640, int(available.height() * 0.9)))

    def copy_to_clipboard(self) -> None:
        QtGui.QGuiApplication.clipboard().setText(summary_text(self.metrics))
