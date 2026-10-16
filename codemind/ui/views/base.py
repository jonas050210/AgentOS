"""Shared view scaffolding: header, scroll body, refresh contract."""

from __future__ import annotations

from typing import Any, Callable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from codemind.ui import theme as T


class View(QWidget):
    """Base class for the seven top-level views.

    Subclasses build their widgets in :meth:`build` and implement :meth:`refresh`, which reads
    live state from the application context. ``refresh`` must stay cheap — it is called on a
    timer and on relevant bus events, never on a background thread.
    """

    title = "View"
    subtitle = ""

    def __init__(self, app: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.body = QWidget()
        self.body.setStyleSheet("background: transparent;")
        self.layout_root = QVBoxLayout(self.body)
        self.layout_root.setContentsMargins(22, 18, 22, 26)
        self.layout_root.setSpacing(16)
        self.scroll.setWidget(self.body)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = self._build_header()
        outer.addWidget(self.header)
        outer.addWidget(self.scroll, 1)
        self.build()
        self.layout_root.addStretch(1)

    # -- header -----------------------------------------------------------
    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setStyleSheet(f"background: rgba(0,0,0,0.18); border-bottom: 1px solid {T.HAIRLINE};")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(22, 12, 22, 12)
        layout.setSpacing(12)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(1)
        self.title_label = QLabel(self.title)
        self.title_label.setFont(T.font(18, QFont.DemiBold))
        self.title_label.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self.subtitle_label = QLabel(self.subtitle)
        self.subtitle_label.setFont(T.font(11))
        self.subtitle_label.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        layout.addLayout(titles)
        layout.addStretch(1)
        self.header_actions = QHBoxLayout()
        self.header_actions.setContentsMargins(0, 0, 0, 0)
        self.header_actions.setSpacing(8)
        layout.addLayout(self.header_actions)
        return header

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        from PySide6.QtGui import QLinearGradient, QPainter

        painter = QPainter(self)
        top, bottom = T.page_gradient()
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, T.color(top))
        gradient.setColorAt(1.0, T.color(bottom))
        painter.fillRect(self.rect(), gradient)
        painter.end()

    def repaint_theme(self) -> None:
        """Re-apply colours after the appearance changed, without rebuilding the view."""
        self.header.setStyleSheet(f"background: rgba(0,0,0,0.18); border-bottom: 1px solid {T.HAIRLINE};")
        self.title_label.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self.subtitle_label.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        hook = getattr(self, "restyle", None)
        if callable(hook):
            try:
                hook()
            except Exception:  # pragma: no cover - a restyle must never break the window
                pass
        for widget in self.findChildren(QWidget):
            widget.update()

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)

    # -- helpers ----------------------------------------------------------
    def add(self, widget: QWidget, stretch: int = 0) -> None:
        self.layout_root.insertWidget(self.layout_root.count() - 1, widget, stretch)

    def add_layout(self, layout) -> None:
        self.layout_root.insertLayout(self.layout_root.count() - 1, layout)

    def build(self) -> None:
        """Create the view's widgets. Must call :meth:`add` for each root widget."""

    def refresh(self) -> None:
        """Update the view from live application state."""

    def safe_refresh(self) -> list[str]:
        """Refresh, returning the list of sections that failed.

        A view is assembled from many independent sections; one bad value must not blank the
        whole screen. Failures are reported (and logged) rather than swallowed.
        """
        failures: list[str] = []
        try:
            self.refresh()
        except Exception as exc:  # pragma: no cover - defensive
            import traceback

            from codemind.logging_setup import get_logger

            get_logger("ui").warning(
                "view refresh failed",
                cm={"view": type(self).__name__, "error": f"{type(exc).__name__}: {exc}", "where": traceback.format_exc().splitlines()[-3]},
            )
            failures.append(f"{type(self).__name__}: {exc}")
        return failures

    def on_event(self, topic: str, payload: dict[str, Any]) -> None:
        """React to a bus event. Keep it cheap; heavy work belongs in refresh()."""


class SectionHeader(QWidget):
    """Small labelled section used to group cards inside a view."""

    def __init__(self, text: str, detail: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 6, 2, 0)
        layout.setSpacing(10)
        label = QLabel(text.upper())
        label.setFont(T.font(9, QFont.DemiBold, spacing=1.0))
        label.setStyleSheet(f"color: {T.TEXT_FAINT}; background: transparent;")
        layout.addWidget(label)
        if detail:
            info = QLabel(detail)
            info.setFont(T.font(10))
            info.setStyleSheet(f"color: {T.color(T.TEXT_FAINT, 0.85).name()}; background: transparent;")
            layout.addWidget(info)
        layout.addStretch(1)
