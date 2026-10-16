"""Log, transcript and timeline widgets."""

from __future__ import annotations

import html
import time
from typing import Any, Iterable, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen, QTextCursor, QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from codemind.ui import theme as T
from codemind.ui.widgets.glass import EmptyState, Pill, clear_layout


class LogView(QPlainTextEdit):
    """Monospace, read-only, auto-scrolling output pane with an ANSI-ish colouring pass."""

    def __init__(self, parent: QWidget | None = None, *, max_lines: int = 4000) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(T.font(11, mono=True))
        self.setStyleSheet(
            f"QPlainTextEdit {{ background: rgba(0,0,0,0.28); color: {T.TEXT}; border: 1px solid {T.HAIRLINE};"
            f" border-radius: 10px; padding: 8px; selection-background-color: rgba(110,168,254,0.35); }}"
        )
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.maximumBlockCount = max_lines
        self.setMaximumBlockCount(max_lines)
        self._lines = 0
        self.set_empty_text("")

    def set_empty_text(self, text: str) -> None:
        """Explain an empty pane instead of showing a blank box.

        A read-only QPlainTextEdit shows its placeholder while the document is empty, which is
        exactly the state a freshly generated dashboard is in.
        """
        palette = self.palette()
        palette.setColor(QPalette.PlaceholderText, T.color(T.TEXT_FAINT))
        self.setPalette(palette)
        self.setPlaceholderText(text)

    def append_line(self, text: str, *, color: str = "") -> None:
        if color:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.End)
            fmt = cursor.charFormat()
            fmt.setForeground(T.color(color))
            cursor.setCharFormat(fmt)
            cursor.insertText(text + "\n")
            fmt.setForeground(T.color(T.TEXT))
            cursor.setCharFormat(fmt)
        else:
            self.appendPlainText(text)
        self._lines += 1

    def append_many(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.append_line(line)

    def stream(self, line: str, stream: str = "stdout") -> None:
        color = T.DANGER if stream == "stderr" else ""
        self.append_line(line, color=color)

    def clear_text(self) -> None:
        self.clear()
        self._lines = 0


class TimelineStep(QWidget):
    """One row of a task plan: status dot, title, tool, timing, expandable evidence."""

    def __init__(self, step: Any, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TimelineStep")
        self._step = step
        self._expanded = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 5, 2, 5)
        layout.setSpacing(10)

        status = getattr(step, "status", "pending")
        color = T.STATUS_COLORS.get(status, T.TEXT_SOFT)
        icon = {
            "done": "✓",
            "failed": "✕",
            "running": "▶",
            "pending": "○",
            "skipped": "–",
            "needs_model": "◇",
            "waiting_approval": "⏳",
        }.get(status, "○")
        self.dot = QLabel(icon)
        self.dot.setFixedWidth(16)
        self.dot.setFont(T.font(12, QFont.Bold))
        self.dot.setStyleSheet(f"color: {color}; background: transparent;")
        layout.addWidget(self.dot, 0, Qt.AlignTop)

        middle = QVBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        self.title = QLabel(f"{getattr(step, 'idx', 0) + 1}. {getattr(step, 'title', '')}")
        self.title.setFont(T.font(12, QFont.Medium))
        self.title.setStyleSheet(f"color: {T.TEXT if status != 'pending' else T.TEXT_SOFT}; background: transparent;")
        self.title.setWordWrap(True)
        title_row.addWidget(self.title, 1)
        if getattr(step, "tool", ""):
            tool_pill = Pill(getattr(step, "tool"), color_value=T.ACCENT)
            title_row.addWidget(tool_pill, 0, Qt.AlignTop)
        origin = getattr(step, "origin", "plan")
        if origin.startswith("repair"):
            title_row.addWidget(Pill("repair", color_value=T.WARNING), 0, Qt.AlignTop)
        middle.addLayout(title_row)
        self.detail = QLabel(getattr(step, "error", "") or getattr(step, "detail", "") or "")
        self.detail.setFont(T.font(10))
        self.detail.setStyleSheet(f"color: {T.DANGER if getattr(step, 'error', '') else T.TEXT_SOFT}; background: transparent;")
        self.detail.setWordWrap(True)
        self.detail.setVisible(bool(self.detail.text()))
        middle.addWidget(self.detail)
        self.meta = QLabel("")
        self.meta.setFont(T.font(9))
        self.meta.setStyleSheet(f"color: {T.TEXT_FAINT}; background: transparent;")
        started, finished = getattr(step, "started_at", None), getattr(step, "finished_at", None)
        if started:
            span = (finished - started) if finished else None
            self.meta.setText(f"{time.strftime('%H:%M:%S', time.localtime(started))}" + (f" · {span:.2f}s" if span else " · running"))
        middle.addWidget(self.meta)
        layout.addLayout(middle, 1)


class Timeline(QWidget):
    """Vertical plan/step timeline with a connecting rail."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    def set_steps(self, steps: Sequence[Any]) -> None:
        clear_layout(self._layout)
        if not steps:
            self._layout.addWidget(EmptyState("No plan yet", "Submit a task and its steps will appear here as the agent works.", icon="◇"))
            return
        for step in steps:
            self._layout.addWidget(TimelineStep(step))
        self._layout.addStretch(1)


class ChatBubble(QWidget):
    """A transcript entry: the user's request, the agent's reasoning, or a tool result."""

    def __init__(self, role: str, text: str, *, meta: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(3)
        palette = {
            "user": (T.ACCENT, "You"),
            "agent": (T.VIOLET, "CodeMind"),
            "tool": (T.INFO, "Tool"),
            "error": (T.DANGER, "Problem"),
            "system": (T.TEXT_SOFT, "System"),
        }
        accent, label = palette.get(role, palette["system"])
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        name = QLabel(label if not meta else f"{label} · {meta}")
        name.setFont(T.font(9, QFont.DemiBold, spacing=0.6))
        name.setStyleSheet(f"color: {accent}; background: transparent;")
        head.addWidget(name)
        head.addStretch(1)
        layout.addLayout(head)
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setFont(T.font(11, mono=role == "tool"))
        bubble.setStyleSheet(
            f"color: {T.TEXT if role != 'system' else T.TEXT_SOFT};"
            f"background: rgba(255,255,255,0.035); border: 1px solid {T.HAIRLINE};"
            f"border-left: 2px solid {accent}; border-radius: 8px; padding: 8px 10px;"
        )
        layout.addWidget(bubble)


class Transcript(QScrollArea):
    """Scrolling conversation/transcript area."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; } QScrollBar:vertical { background: transparent; width: 8px; }")
        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self._inner)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(2)
        self._layout.addStretch(1)
        self.setWidget(self._inner)

    def add(self, role: str, text: str, *, meta: str = "") -> None:
        bubble = ChatBubble(role, text, meta=meta)
        self._layout.insertWidget(self._layout.count() - 1, bubble)
        self._scroll_to_end()

    def _scroll_to_end(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear_transcript(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
