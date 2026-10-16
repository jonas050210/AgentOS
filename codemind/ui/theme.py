"""Visual language for the CodeMind desktop app.

A single place for colours, type, spacing and the glass-painting helpers, so every view looks
like part of the same product. The look is dark and translucent: layered surfaces, fine hairline
borders, restrained gradients, soft light from the top-left, generous spacing and one accent
colour used sparingly.

Nothing here knows about data; views pull colours and helpers from this module only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

# --------------------------------------------------------------------------- palette
BG_DEEP = "#080A0F"
BG = "#0C0E14"
BG_RAISED = "#12151D"
SURFACE = "#161A23"

TEXT = "#F3F5F9"
TEXT_SOFT = "rgba(243, 245, 249, 0.66)"
TEXT_FAINT = "rgba(243, 245, 249, 0.42)"

ACCENT = "#6EA8FE"
ACCENT_SOFT = "rgba(110, 168, 254, 0.16)"
ACCENT_GLOW = "rgba(110, 168, 254, 0.55)"
SUCCESS = "#4ADE80"
WARNING = "#FBBF5B"
DANGER = "#FF6B6B"
INFO = "#7BD3EA"
VIOLET = "#B39DFF"

HAIRLINE = "rgba(255, 255, 255, 0.085)"
HAIRLINE_STRONG = "rgba(255, 255, 255, 0.14)"
GLASS_TOP = "rgba(255, 255, 255, 0.065)"
GLASS_BOTTOM = "rgba(255, 255, 255, 0.02)"
SHADOW = "rgba(0, 0, 0, 0.45)"

STATUS_COLORS = {
    "queued": TEXT_SOFT,
    "planning": INFO,
    "running": ACCENT,
    "waiting_approval": WARNING,
    "paused": WARNING,
    "cancelling": WARNING,
    "verifying": VIOLET,
    "completed": SUCCESS,
    "failed": DANGER,
    "blocked": WARNING,
    "cancelled": TEXT_FAINT,
    "interrupted": WARNING,
    "starting": ACCENT,
    "active": SUCCESS,
}

# --------------------------------------------------------------------------- metrics
RADIUS = 16
RADIUS_SMALL = 10
PAD = 16
GAP = 12

FONT_CANDIDATES = ("Inter", "SF Pro Text", "Ubuntu", "Cantarell", "Segoe UI", "Noto Sans", "DejaVu Sans")
MONO_CANDIDATES = ("JetBrains Mono", "SF Mono", "Ubuntu Mono", "Cascadia Mono", "DejaVu Sans Mono", "Menlo", "Consolas")


@dataclass(frozen=True)
class Fonts:
    ui: str
    mono: str


FONT_SEARCH_DIRS = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "~/.fonts",
    "~/.local/share/fonts",
)

_LOADED_FONTS = False


def ensure_fonts() -> None:
    """Make sure Qt can see system fonts even when fontconfig has no cache.

    Some minimal Linux installs (and containers) ship font files without a fontconfig database,
    which would render every label as an empty box. Registering the files directly with Qt fixes
    that without touching the system.
    """
    global _LOADED_FONTS
    if _LOADED_FONTS:
        return
    _LOADED_FONTS = True
    families = set(QFontDatabase.families())
    if families & set(FONT_CANDIDATES) or families & set(MONO_CANDIDATES):
        return
    from pathlib import Path

    added = 0
    for directory in FONT_SEARCH_DIRS:
        root = Path(directory).expanduser()
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.tt[fc]"))[:40]:
            if QFontDatabase.addApplicationFont(str(path)) != -1:
                added += 1
        for path in sorted(root.rglob("*.otf"))[:20]:
            if QFontDatabase.addApplicationFont(str(path)) != -1:
                added += 1
        if added:
            break
    if added:
        QFontDatabase.families()


def _pick(candidates: tuple[str, ...], fallback: str) -> str:
    ensure_fonts()
    families = set(QFontDatabase.families())
    for name in candidates:
        if name in families:
            return name
    lowered = {name.lower(): name for name in families}
    for name in candidates:
        if name.lower() in lowered:
            return lowered[name.lower()]
    if families:
        preferred = sorted(family for family in families if "mono" in family.lower()) if candidates is MONO_CANDIDATES else []
        if preferred:
            return preferred[0]
        if fallback.lower() in lowered:
            return lowered[fallback.lower()]
    return fallback


def fonts() -> Fonts:
    return Fonts(ui=_pick(FONT_CANDIDATES, "Sans Serif"), mono=_pick(MONO_CANDIDATES, "Monospace"))


_FONTS: Fonts | None = None


def font(size: int = 13, weight: int = QFont.Normal, *, mono: bool = False, spacing: float = 0.0) -> QFont:
    global _FONTS
    if _FONTS is None:
        _FONTS = fonts()
    family = _FONTS.mono if mono else _FONTS.ui
    f = QFont(family, size, weight)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    f.setStyleStrategy(QFont.PreferAntialias)
    return f


def color(value: str, alpha: float = 1.0) -> QColor:
    """Parse ``#rrggbb`` or ``rgba(r, g, b, a)`` (alpha scaled by ``alpha``)."""
    text = (value or "").strip()
    if text.startswith("rgba"):
        inner = text[text.index("(") + 1 : text.rindex(")")]
        parts = [p.strip() for p in inner.split(",")]
        r, g, b = (int(float(p)) for p in parts[:3])
        a = float(parts[3]) if len(parts) > 3 else 1.0
        return QColor(r, g, b, max(0, min(255, int(a * 255 * alpha))))
    c = QColor(text)
    if alpha != 1.0:
        c.setAlphaF(max(0.0, min(1.0, c.alphaF() * alpha)))
    return c


# --------------------------------------------------------------------------- painting
def glass_path(rect: QRectF, radius: int = RADIUS) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def paint_glass(painter: QPainter, rect: QRectF, *, radius: int = RADIUS, tint: str | None = None, glow: str | None = None) -> None:
    """Paint one translucent pane: soft vertical light, hairline border, optional inner glow."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    path = glass_path(rect, radius)

    # body: gentle top-to-bottom light falloff
    body = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    body.setColorAt(0.0, color(GLASS_TOP if tint is None else tint, 1.35))
    body.setColorAt(0.55, color(GLASS_BOTTOM if tint is None else tint, 0.85))
    body.setColorAt(1.0, color(GLASS_BOTTOM if tint is None else tint, 0.55))
    painter.fillPath(path, QBrush(body))

    # top-left sheen (the "glass" reflection)
    sheen = QRadialGradient(QPointF(rect.left() + rect.width() * 0.24, rect.top() + rect.height() * 0.06), max(rect.width(), rect.height()) * 0.75)
    sheen.setColorAt(0.0, QColor(255, 255, 255, 15 if glow is None else 26))
    sheen.setColorAt(0.55, QColor(255, 255, 255, 5))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillPath(path, QBrush(sheen))

    if glow:
        accent_glow = QRadialGradient(QPointF(rect.left() + rect.width() * 0.08, rect.top() + rect.height() * 0.9), max(rect.width(), rect.height()) * 0.6)
        accent_glow.setColorAt(0.0, color(glow, 0.30))
        accent_glow.setColorAt(1.0, color(glow, 0.0))
        painter.fillPath(path, QBrush(accent_glow))

    # hairline border: brighter at the top, fades towards the bottom
    border = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    border.setColorAt(0.0, color(HAIRLINE_STRONG))
    border.setColorAt(1.0, color(HAIRLINE, 0.6))
    pen = QPen(QBrush(border), 1.0)
    painter.setPen(pen)
    painter.drawPath(glass_path(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius))
    painter.restore()


def paint_shadow(painter: QPainter, rect: QRectF, *, radius: int = RADIUS, strength: float = 0.5) -> None:
    """A soft outer shadow drawn as a few concentric strokes (cheap, no pixmap buffers)."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    for index in range(6, 0, -1):
        alpha = strength * (index / 6.0) ** 2 * 0.16
        pen = QPen(QColor(0, 0, 0, int(alpha * 255)))
        pen.setWidthF(index * 1.6)
        painter.setPen(pen)
        painter.drawRoundedRect(rect.adjusted(index * 0.8, index * 0.9, -index * 0.8, -index * 0.7), radius + index, radius + index)
    painter.restore()


def paint_progress(painter: QPainter, rect: QRectF, fraction: float, *, color_value: str = ACCENT, track: str = "rgba(255,255,255,0.08)") -> None:
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    radius = rect.height() / 2
    painter.setPen(Qt.NoPen)
    painter.setBrush(color(track))
    painter.drawRoundedRect(rect, radius, radius)
    fraction = max(0.0, min(1.0, fraction))
    if fraction > 0:
        filled = QRectF(rect.left(), rect.top(), max(rect.height(), rect.width() * fraction), rect.height())
        gradient = QLinearGradient(filled.topLeft(), filled.topRight())
        gradient.setColorAt(0.0, color(color_value, 0.85))
        gradient.setColorAt(1.0, color(color_value))
        painter.setBrush(QBrush(gradient))
        painter.drawRoundedRect(filled, radius, radius)
    painter.restore()


def level_color(value: float, *, inverted: bool = False) -> str:
    """Traffic-light colour for a 0..1 or percentage metric."""
    critical = value >= 85 if value > 1.5 else value >= 0.85
    warning = value >= 70 if value > 1.5 else value >= 0.7
    if inverted:
        critical, warning = warning, critical
    if critical:
        return DANGER
    if warning:
        return WARNING
    return SUCCESS


def spark_points(values: list[float], width: float, height: float) -> list[QPointF]:
    if not values:
        return []
    if len(values) == 1:
        return [QPointF(0, height / 2), QPointF(width, height / 2)]
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    step = width / (len(values) - 1)
    return [QPointF(index * step, height - ((value - low) / span) * height) for index, value in enumerate(values)]


def human_rate(value: float, unit: str = "/s") -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k{unit}"
    if value >= 10:
        return f"{value:.0f}{unit}"
    return f"{value:.1f}{unit}"


def clamp_text(text: str, limit: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def ease_in_out(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))
