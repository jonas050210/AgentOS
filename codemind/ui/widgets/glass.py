"""Reusable glass widgets: the visual vocabulary every view is built from."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QLayout,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from codemind.ui import theme as T


class ElidedLabel(QLabel):
    """One-line label that shortens itself with an ellipsis instead of wrapping or squashing."""

    def __init__(self, text: str = "", *, parent: QWidget | None = None, color_value: str = T.TEXT_SOFT, size: int = 11, weight: int = QFont.Normal) -> None:
        super().__init__(text, parent)
        self._full = text
        self.setFont(T.font(size, weight))
        self.setStyleSheet(f"color: {color_value}; background: transparent;")
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(48)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        self._full = text or ""
        super().setText(self._full)
        self.setToolTip(self._full)
        self.update()

    def full_text(self) -> str:
        return self._full

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        metrics = self.fontMetrics()
        available = max(0, self.width())
        text = metrics.elidedText(self._full, Qt.ElideRight, available)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.rect(), int(self.alignment()), text)
        painter.end()


class GlassPanel(QWidget):
    """A translucent card. Children are laid out inside automatically."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        radius: int = T.RADIUS,
        padding: int = T.PAD,
        spacing: int = T.GAP,
        shadow: bool = True,
        glow: str | None = None,
        direction: str = "v",
    ) -> None:
        super().__init__(parent)
        self._radius = radius
        self._shadow = shadow
        self._glow = glow
        self.setAttribute(Qt.WA_StyledBackground, False)
        layout_cls = QHBoxLayout if direction == "h" else QVBoxLayout
        self.body = layout_cls(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(spacing)
        # a panel is exactly as big as what is inside it: the page scrolls, nothing is clipped
        self.body.setSizeConstraint(QLayout.SetMinimumSize)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        if self._shadow:
            T.paint_shadow(painter, rect.translated(0, 1), radius=self._radius, strength=0.75)
        T.paint_glass(painter, rect, radius=self._radius, glow=self._glow)
        painter.end()


class Card(GlassPanel):
    """Panel with an optional title row and an action area."""

    def __init__(self, title: str = "", subtitle: str = "", *, direction: str = "v", padding: int = T.PAD, glow: str | None = None) -> None:
        super().__init__(None, radius=T.RADIUS, padding=padding, shadow=True, glow=glow, direction=direction)
        self.setObjectName("Card")
        self._header = QVBoxLayout()
        self._header.setContentsMargins(0, 0, 0, 0)
        self._header.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setFont(T.font(13, QFont.DemiBold))
        self.title_label.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self.subtitle_label = ElidedLabel(subtitle, size=11)
        self.subtitle_label.setVisible(bool(subtitle))
        self.title_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._header.addWidget(self.title_label)
        self._header.addWidget(self.subtitle_label)
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.addLayout(self._header, 1)
        head_row.addStretch(0)
        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(6)
        head_row.addLayout(self.actions)
        if title or subtitle:
            self.body.addLayout(head_row)
        self.content = QVBoxLayout()
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(T.GAP)
        self.body.addLayout(self.content)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))
        self.subtitle_label.setToolTip(text)

    def clear_content(self) -> None:
        while self.content.count():
            item = self.content.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)


class Pill(QLabel):
    """Small status chip: ``● running``, ``2 approvals``, ``+0.62``."""

    def __init__(self, text: str = "", *, color_value: str = T.TEXT_SOFT, filled: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFont(T.font(10, QFont.DemiBold, spacing=0.3))
        self._color = color_value
        self._filled = filled
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._refresh()

    def set_state(self, text: str, color_value: str | None = None) -> None:
        self.setText(text)
        if color_value:
            self._color = color_value
        self._refresh()

    def _refresh(self) -> None:
        base = QColor(self._color) if isinstance(self._color, QColor) else T.color(self._color)
        self._color = base.name()
        background = T.color(base.name(), 0.22 if self._filled else 0.12).name(QColor.HexArgb)
        border = T.color(base.name(), 0.45 if self._filled else 0.28).name(QColor.HexArgb)
        self.setStyleSheet(
            f"color: {base.name()};"
            f"background: {background}; border: 1px solid {border};"
            f"border-radius: 9px; padding: 3px 9px;"
        )


class StatusDot(QWidget):
    """A small animated presence dot with an optional label."""

    def __init__(self, label: str = "", *, color_value: str = T.TEXT_FAINT, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color_value
        self._label = label
        self._phase = 0.0
        self.setFixedHeight(20)
        self._timer = self.startTimer(80)

    def set_state(self, label: str, color_value: str) -> None:
        self._label = label
        self._color = color_value
        self.update()

    def timerEvent(self, event) -> None:  # noqa: N802
        self._phase = (self._phase + 0.08) % 1.0
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        width = 18 + (len(self._label) * 7 if self._label else 0)
        return QSize(width, 20)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        base = T.color(self._color)
        pulse = 0.5 + 0.5 * abs(1 - 2 * self._phase)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(base.red(), base.green(), base.blue(), int(60 * pulse)))
        painter.drawEllipse(QRectF(2, 5, 12, 12))
        painter.setBrush(base)
        painter.drawEllipse(QRectF(6, 9, 5, 5))
        if self._label:
            painter.setPen(QPen(T.color(T.TEXT_SOFT)))
            painter.setFont(T.font(11))
            painter.drawText(QRectF(18, 0, self.width() - 18, 20), Qt.AlignVCenter | Qt.AlignLeft, self._label)
        painter.end()


class StatTile(GlassPanel):
    """Headline number with a label, an optional sub-line and an optional trend."""

    def __init__(self, label: str, value: str = "—", *, sub: str = "", accent: str = T.ACCENT, icon: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=13, spacing=3, glow=None)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(84)
        self._accent = accent
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        self.label = QLabel(label.upper())
        self.label.setFont(T.font(9, QFont.DemiBold, spacing=0.8))
        self.label.setStyleSheet(f"color: {T.TEXT_FAINT}; background: transparent;")
        top.addWidget(self.label)
        top.addStretch(1)
        if icon:
            self.icon = QLabel(icon)
            self.icon.setFont(T.font(11))
            self.icon.setStyleSheet(f"color: {T.color(accent, 0.85).name()}; background: transparent;")
            top.addWidget(self.icon)
        self.value = QLabel(value)
        self.value.setFont(T.font(21, QFont.DemiBold))
        self.value.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self.sub = ElidedLabel(sub, size=10)
        self.sub.setVisible(bool(sub))
        self.body.addLayout(top)
        self.body.addWidget(self.value)
        self.body.addWidget(self.sub)

    def set_value(self, value: str, *, sub: str | None = None, accent: str | None = None) -> None:
        self.value.setText(value)
        if sub is not None:
            self.sub.setText(sub)
            self.sub.setVisible(bool(sub))
        if accent:
            self._accent = accent
            self.update()


class Sparkline(QWidget):
    """Tiny line chart for a live series (CPU, RAM, loss)."""

    def __init__(self, *, color_value: str = T.ACCENT, height: int = 42, fill: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self._color = color_value
        self._fill = fill
        self._ceiling: float | None = None
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_values(self, values: Sequence[float], *, ceiling: float | None = None) -> None:
        self._values = [float(v) for v in values][-240:]
        self._ceiling = ceiling
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 3, -1, -3)
        if self._ceiling:
            values = [min(self._ceiling, v) / self._ceiling for v in self._values]
        else:
            values = self._values
        if not values:
            painter.setPen(QPen(T.color(T.TEXT_FAINT)))
            painter.setFont(T.font(10))
            painter.drawText(rect, Qt.AlignCenter, "waiting for samples")
            painter.end()
            return
        points = T.spark_points(values, rect.width(), rect.height())
        shifted = [QPointF(p.x() + rect.left(), p.y() + rect.top()) for p in points]
        if self._fill and len(shifted) > 1:
            area = QPainterPath()
            area.moveTo(shifted[0].x(), rect.bottom())
            for point in shifted:
                area.lineTo(point)
            area.lineTo(shifted[-1].x(), rect.bottom())
            area.closeSubpath()
            gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            gradient.setColorAt(0.0, T.color(self._color, 0.34))
            gradient.setColorAt(1.0, T.color(self._color, 0.0))
            painter.fillPath(area, QBrush(gradient))
        pen = QPen(T.color(self._color), 1.6)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        if len(shifted) == 1:
            painter.drawEllipse(shifted[0], 2.0, 2.0)
        else:
            painter.drawPolyline(shifted)
        painter.end()


class BarMeter(QWidget):
    """Horizontal meter used for percentages and progress with a caption."""

    def __init__(self, caption: str = "", *, color_value: str = T.ACCENT, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._caption = caption
        self._color = color_value
        self._value = 0.0
        self._text = ""
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, value: float, *, text: str | None = None, color_value: str | None = None) -> None:
        self._value = max(0.0, min(1.0, value))
        if text is not None:
            self._text = text
        if color_value:
            self._color = color_value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(T.color(T.TEXT_SOFT)))
        painter.setFont(T.font(11))
        painter.drawText(QRectF(0, 0, self.width(), 15), Qt.AlignLeft | Qt.AlignVCenter, self._caption)
        right = self._text or f"{self._value * 100:.0f}%"
        painter.setPen(QPen(T.color(T.TEXT)))
        painter.drawText(QRectF(0, 0, self.width(), 15), Qt.AlignRight | Qt.AlignVCenter, right)
        T.paint_progress(painter, QRectF(0, 19, self.width(), 6), self._value, color_value=self._color)
        painter.end()


class ActionButton(QToolButton):
    """Flat, glassy text button. ``tone`` picks the accent colour."""

    def __init__(self, text: str, *, tone: str = "neutral", on_click: Callable[[], None] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(T.font(11, QFont.DemiBold))
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._tone = tone
        self._apply_style()
        if on_click is not None:
            self.clicked.connect(lambda: on_click())

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self._apply_style()

    def _apply_style(self) -> None:
        palette = {
            "neutral": (T.TEXT, "rgba(255,255,255,0.06)", T.HAIRLINE),
            "accent": (T.ACCENT, "rgba(110,168,254,0.16)", "rgba(110,168,254,0.42)"),
            "danger": (T.DANGER, "rgba(255,107,107,0.14)", "rgba(255,107,107,0.40)"),
            "success": (T.SUCCESS, "rgba(74,222,128,0.14)", "rgba(74,222,128,0.38)"),
            "warning": (T.WARNING, "rgba(251,191,91,0.14)", "rgba(251,191,91,0.40)"),
        }
        fg, bg, border = palette.get(self._tone, palette["neutral"])
        self.setStyleSheet(
            f"QToolButton {{ color: {fg}; background: {bg}; border: 1px solid {border};"
            f" border-radius: 9px; padding: 5px 12px; }}"
            f"QToolButton:hover {{ background: {T.color(fg, 0.24).name(QColor.HexArgb)}; }}"
            f"QToolButton:pressed {{ background: {T.color(fg, 0.32).name(QColor.HexArgb)}; }}"
            f"QToolButton:disabled {{ color: {T.TEXT_FAINT}; background: rgba(255,255,255,0.03); border-color: {T.HAIRLINE}; }}"
        )


class KeyValue(QWidget):
    """Two-column key/value line used in detail panes."""

    def __init__(self, key: str, value: str = "—", *, mono: bool = False, value_color: str = T.TEXT, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.key = QLabel(key)
        self.key.setFont(T.font(11))
        self.key.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        self.key.setMinimumWidth(110)
        self.key.setMaximumWidth(190)
        self.value = QLabel(value)
        self.value.setFont(T.font(11, QFont.Normal, mono=mono))
        self.value.setStyleSheet(f"color: {value_color}; background: transparent;")
        self.value.setWordWrap(True)
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.key, 0, Qt.AlignTop)
        layout.addWidget(self.value, 1)
        self._value_color = value_color

    def set_value(self, value: str, *, color_value: str | None = None) -> None:
        self.value.setText(value)
        if color_value:
            self._value_color = color_value
            self.value.setStyleSheet(f"color: {color_value}; background: transparent;")


class EmptyState(QWidget):
    """Friendly, honest placeholder that says what will appear here and how to make it happen."""

    def __init__(self, title: str, detail: str = "", *, icon: str = "✦", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 22, 18, 22)
        layout.setSpacing(6)
        self.icon = QLabel(icon)
        self.icon.setFont(T.font(20))
        self.icon.setStyleSheet(f"color: {T.color(T.ACCENT, 0.75).name()}; background: transparent;")
        self.title = QLabel(title)
        self.title.setFont(T.font(12, QFont.DemiBold))
        self.title.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self.detail = QLabel(detail)
        self.detail.setFont(T.font(11))
        self.detail.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        self.detail.setWordWrap(True)
        for widget, align in ((self.icon, Qt.AlignLeft), (self.title, Qt.AlignLeft), (self.detail, Qt.AlignLeft)):
            widget.setAlignment(align)
            layout.addWidget(widget)
        layout.addStretch(1)


class FadeIn:
    """Small helper that fades a widget in when it becomes visible."""

    @staticmethod
    def apply(widget: QWidget, duration: int = 180) -> None:
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", widget)
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.start()
        widget._fade_animation = animation  # type: ignore[attr-defined]


def hline(parent: QWidget | None = None) -> QFrame:
    line = QFrame(parent)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {T.color(T.HAIRLINE).name(QColor.HexArgb)}; border: none;")
    return line


def section_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setFont(T.font(9, QFont.DemiBold, spacing=0.9))
    label.setStyleSheet(f"color: {T.TEXT_FAINT}; background: transparent;")
    return label


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
        else:
            child = item.layout()
            if child is not None:
                clear_layout(child)
