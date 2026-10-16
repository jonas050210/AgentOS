"""Compact data table and metric widgets."""

from __future__ import annotations

import time
from typing import Any, Callable, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from codemind.ui import theme as T


class DataTable(QTableWidget):
    """Read-only table with the app's typography, used for datasets, runs, history, repos."""

    def __init__(self, columns: Sequence[str], *, stretch_column: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(0, len(columns), parent)
        self.setHorizontalHeaderLabels([str(c) for c in columns])
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAlternatingRowColors(False)
        self.verticalHeader().setDefaultSectionSize(30)
        header = self.horizontalHeader()
        header.setFont(T.font(10, QFont.DemiBold))
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        if 0 <= stretch_column < len(columns):
            header.setSectionResizeMode(stretch_column, QHeaderView.Stretch)
        header.setHighlightSections(False)
        self.setStyleSheet(
            f"QTableWidget {{ background: transparent; border: none; color: {T.TEXT};"
            f" gridline-color: transparent; selection-background-color: rgba(110,168,254,0.16); }}"
            f"QTableWidget::item {{ padding: 4px 8px; border: none; }}"
            f"QHeaderView::section {{ background: transparent; color: {T.TEXT_FAINT};"
            f" border: none; border-bottom: 1px solid {T.HAIRLINE}; padding: 6px 8px; }}"
            f"QTableWidget::item:selected {{ color: {T.TEXT}; }}"
        )
        self._mono_columns: set[int] = set()

    def set_mono_columns(self, columns: Sequence[int]) -> None:
        self._mono_columns = set(columns)

    def set_rows(self, rows: Sequence[Sequence[Any]], *, colors: Sequence[Sequence[str | None]] | None = None) -> None:
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                text = value if isinstance(value, str) else _format(value)
                item = QTableWidgetItem(text)
                item.setFont(T.font(11, mono=c in self._mono_columns))
                if colors and r < len(colors) and c < len(colors[r]) and colors[r][c]:
                    item.setForeground(T.color(colors[r][c]))
                else:
                    item.setForeground(T.color(T.TEXT))
                if c > 0:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                self.setItem(r, c, item)

    def current_row_data(self) -> list[str]:
        row = self.currentRow()
        if row < 0:
            return []
        return [self.item(row, c).text() if self.item(row, c) else "" for c in range(self.columnCount())]


def _format(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if value != value:
            return "—"
        return f"{value:,.4f}".rstrip("0").rstrip(".") if abs(value) < 1000 else f"{value:,.0f}"
    if isinstance(value, int):
        if value > 1_600_000_000:  # looks like a unix timestamp
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(value))
        return f"{value:,}"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value[:4]) + ("…" if len(value) > 4 else "")
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in list(value.items())[:3])
    return str(value)


class LineChart(QWidget):
    """Multi-series line chart (loss curves, resource history) drawn with real values only."""

    def __init__(self, *, height: int = 150, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series: dict[str, tuple[list[float], str]] = {}
        self._x_labels: list[str] = []
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_series(self, series: dict[str, tuple[Sequence[float], str]], *, x_labels: Sequence[str] = ()) -> None:
        self._series = {name: (list(values), color) for name, (values, color) in series.items()}
        self._x_labels = list(x_labels)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(34, 10, -10, -22)
        painter.setPen(QPen(T.color(T.TEXT_FAINT)))
        painter.setFont(T.font(9))
        if not any(values for values, _ in self._series.values()):
            painter.drawText(QRectF(self.rect()), Qt.AlignCenter, "no data yet")
            painter.end()
            return
        all_values = [v for values, _ in self._series.values() for v in values]
        low, high = min(all_values), max(all_values)
        if high - low < 1e-9:
            high = low + 1.0
        pad = (high - low) * 0.12
        low, high = low - pad, high + pad
        # gridlines + y labels
        for index in range(5):
            fraction = index / 4
            y = rect.bottom() - fraction * rect.height()
            painter.setPen(QPen(T.color(T.HAIRLINE, 0.7)))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QPen(T.color(T.TEXT_FAINT)))
            painter.drawText(QRectF(0, y - 7, 30, 14), Qt.AlignRight | Qt.AlignVCenter, f"{low + fraction * (high - low):.3g}")
        for name, (values, color_value) in self._series.items():
            if len(values) < 2:
                continue
            step = rect.width() / (len(values) - 1)
            points = [QPointF(rect.left() + i * step, rect.bottom() - ((v - low) / (high - low)) * rect.height()) for i, v in enumerate(values)]
            if len(self._series) == 1:
                area = QLinearGradient(rect.topLeft(), rect.bottomLeft())
                area.setColorAt(0.0, T.color(color_value, 0.28))
                area.setColorAt(1.0, T.color(color_value, 0.0))
                path = QBrush(area)
                from PySide6.QtGui import QPainterPath

                shape = QPainterPath()
                shape.moveTo(points[0].x(), rect.bottom())
                for point in points:
                    shape.lineTo(point)
                shape.lineTo(points[-1].x(), rect.bottom())
                shape.closeSubpath()
                painter.fillPath(shape, path)
            painter.setPen(QPen(T.color(color_value), 1.7))
            painter.drawPolyline(points)
            # legend
        legend_x = rect.left()
        for name, (values, color_value) in self._series.items():
            painter.setPen(QPen(T.color(color_value), 2))
            painter.drawLine(QPointF(legend_x, self.height() - 10), QPointF(legend_x + 12, self.height() - 10))
            painter.setPen(QPen(T.color(T.TEXT_SOFT)))
            painter.drawText(QRectF(legend_x + 16, self.height() - 18, 120, 16), Qt.AlignLeft | Qt.AlignVCenter, name)
            legend_x += 18 + painter.fontMetrics().horizontalAdvance(name) + 22
        painter.end()
