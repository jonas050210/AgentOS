#!/usr/bin/env python3
"""Render the CodeMind application icon into ``assets/``.

The mark is drawn with the same painting helpers the interface uses, so the icon and the window
chrome cannot drift apart. Run it with the headless Qt setup described in the README::

    LD_LIBRARY_PATH=... QT_QPA_PLATFORM=vnc ./.venv/bin/python tools/make_icon.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap, QPolygonF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from codemind.ui import theme as T  # noqa: E402


def render(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    scale = size / 512.0
    pad = 40 * scale
    radius = 120 * scale

    # a dark glass pane with the accent edge light
    painter.setBrush(QColor(16, 20, 28, 235))
    painter.setPen(QPen(QColor(255, 255, 255, 40), max(1.0, 3 * scale)))
    painter.drawRoundedRect(QRectF(pad, pad, size - 2 * pad, size - 2 * pad), radius, radius)

    glow = QLinearGradient(QPointF(0, pad), QPointF(size, size - pad))
    glow.setColorAt(0.0, QColor(T.ACCENT))
    glow.setColorAt(1.0, QColor(T.ACCENT_ALT if hasattr(T, "ACCENT_ALT") else T.ACCENT).darker(140))

    # the spark: a diamond with two facet lines, drawn as polygons so no font is required
    centre = QPointF(size / 2.0, size / 2.0)
    half_w = size * 0.20
    half_h = size * 0.30
    diamond = QPolygonF(
        [
            QPointF(centre.x(), centre.y() - half_h),
            QPointF(centre.x() + half_w, centre.y()),
            QPointF(centre.x(), centre.y() + half_h),
            QPointF(centre.x() - half_w, centre.y()),
        ]
    )
    painter.setPen(Qt.NoPen)
    painter.setBrush(glow)
    painter.drawPolygon(diamond)

    painter.setPen(QPen(QColor(255, 255, 255, 120), max(1.0, 4 * scale)))
    painter.drawLine(QPointF(centre.x() - half_w, centre.y()), QPointF(centre.x() + half_w, centre.y()))
    painter.drawLine(QPointF(centre.x(), centre.y() - half_h * 0.45), QPointF(centre.x(), centre.y() + half_h * 0.45))
    painter.end()
    return pixmap


def main() -> int:
    target = ROOT / "assets"
    target.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])  # noqa: F841 - needed for QPixmap
    for size in (512, 256, 128):
        name = "codemind.png" if size == 256 else f"codemind-{size}.png"
        path = target / name
        if not render(size).save(str(path), "PNG"):
            print(f"could not write {path}", file=sys.stderr)
            return 1
        print(f"{path.relative_to(ROOT)}  ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
