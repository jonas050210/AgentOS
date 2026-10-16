"""The main window: sidebar navigation, the seven views, and the global status bar."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from codemind.events import Topics
from codemind.ui import theme as T
from codemind.ui.bridge import EventBridge
from codemind.ui.views.agent import AgentView
from codemind.ui.views.github import GithubView
from codemind.ui.views.memory import MemoryView
from codemind.ui.views.overview import OverviewView
from codemind.ui.views.sandbox import SandboxView
from codemind.ui.views.settings import SettingsView
from codemind.ui.views.training import TrainingView
from codemind.ui.widgets.glass import Pill, StatusDot
from codemind.utils.files import human_bytes, human_duration
from codemind.utils.textutil import shorten

VIEWS = (
    ("Overview", "◈", OverviewView),
    ("Coding Agent", "◆", AgentView),
    ("Training", "◇", TrainingView),
    ("Sandbox", "⬡", SandboxView),
    ("Memory & Models", "✧", MemoryView),
    ("GitHub", "⌥", GithubView),
    ("Settings", "⚙", SettingsView),
)


ICON_PATH = Path(__file__).resolve().parents[2] / "assets" / "codemind.png"


def app_icon() -> QIcon:
    """The window icon: the shipped asset when it exists, otherwise the same mark drawn live."""
    if ICON_PATH.exists():
        icon = QIcon(str(ICON_PATH))
        if not icon.isNull():
            return icon
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    from PySide6.QtCore import QRectF

    T.paint_glass(painter, QRectF(10, 10, 108, 108), radius=26, glow=T.ACCENT)
    painter.setPen(QColor(T.ACCENT))
    painter.setFont(QFont("Sans Serif", 54, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "◆")
    painter.end()
    return QIcon(pixmap)


class SidebarButton(QPushButton):
    def __init__(self, title: str, glyph: str, parent: QWidget | None = None) -> None:
        # a single "&" is a Qt mnemonic marker and would be swallowed ("Memory & Models" → "Memory  Models")
        super().__init__(f" {glyph}   {title.replace('&', '&&')}", parent)
        self.title = title
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(T.font(12, QFont.Medium))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(38)
        self._refresh()

    def label(self) -> str:
        """The visible name, without the escaping Qt text needs."""
        return self.title

    def _refresh(self) -> None:
        self.setStyleSheet(
            f"QPushButton {{ text-align: left; padding: 7px 12px; border-radius: 10px; color: {T.TEXT_SOFT};"
            f" background: transparent; border: 1px solid transparent; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.05); color: {T.TEXT}; }}"
            f"QPushButton:checked {{ background: {T.color(T.ACCENT, 0.16).name(QColor.HexArgb)};"
            f" color: {T.TEXT}; border: 1px solid {T.color(T.ACCENT, 0.35).name(QColor.HexArgb)}; }}"
        )


class MainWindow(QMainWindow):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self.setWindowTitle("CodeMind — autonomous coding assistant")
        self.setWindowIcon(app_icon())
        self.resize(1480, 940)
        self.setMinimumSize(1120, 720)
        self.bridge = EventBridge(parent=self)
        self.bridge.event.connect(self._on_event)

        central = QWidget()
        central.setStyleSheet(f"background: {T.BG};")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.views: dict[str, Any] = {}
        for title, _glyph, view_cls in VIEWS:
            view = view_cls(app)
            self.views[title] = view
            self.stack.addWidget(view)
        self.buttons[0].setChecked(True)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1500)
        self._refresh_timer.timeout.connect(self.refresh_current)
        self._refresh_timer.start()
        self.refresh_current()
        self.statusBar().showMessage("CodeMind is starting background services…")
        self.statusBar().setStyleSheet(f"QStatusBar {{ color: {T.TEXT_SOFT}; background: rgba(0,0,0,0.35); }}")

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self._refresh_status_bar)
        self._status_timer.start()

    # -- chrome -----------------------------------------------------------
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(244)
        sidebar.setStyleSheet(f"background: rgba(0,0,0,0.28); border-right: 1px solid {T.HAIRLINE};")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(8)

        brand = QWidget()
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 6)
        brand_layout.setSpacing(10)
        mark = QLabel("◆")
        mark.setFont(T.font(20, QFont.Bold))
        mark.setStyleSheet(f"color: {T.ACCENT}; background: transparent;")
        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        name = QLabel("CodeMind")
        name.setFont(T.font(15, QFont.DemiBold))
        name.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self.brand_sub = QLabel("booting…")
        self.brand_sub.setFont(T.font(9))
        self.brand_sub.setStyleSheet(f"color: {T.TEXT_FAINT}; background: transparent;")
        text_box.addWidget(name)
        text_box.addWidget(self.brand_sub)
        brand_layout.addWidget(mark)
        brand_layout.addLayout(text_box)
        brand_layout.addStretch(1)
        layout.addWidget(brand)

        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {T.color(T.HAIRLINE, 0.8).name(QColor.HexArgb)};")
        layout.addWidget(rule)

        self.buttons: list[SidebarButton] = []
        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, (title, glyph, _cls) in enumerate(VIEWS):
            button = SidebarButton(title, glyph)
            button.setChecked(index == 0)
            button.clicked.connect(lambda _checked=False, i=index: self.show_view(i))
            group.addButton(button, index)
            layout.addWidget(button)
            self.buttons.append(button)
        layout.addStretch(1)

        self.sidebar_status = QWidget()
        status_layout = QVBoxLayout(self.sidebar_status)
        status_layout.setContentsMargins(4, 8, 4, 0)
        status_layout.setSpacing(6)
        self.dot = StatusDot("idle")
        self.sidebar_agent = QLabel("agent: idle")
        self.sidebar_model = QLabel("model: probing")
        self.sidebar_training = QLabel("training: idle")
        self.sidebar_resources = QLabel("cpu — · ram —")
        for label in (self.sidebar_agent, self.sidebar_model, self.sidebar_training, self.sidebar_resources):
            label.setFont(T.font(10))
            label.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        status_layout.addWidget(self.dot)
        for label in (self.sidebar_agent, self.sidebar_model, self.sidebar_training, self.sidebar_resources):
            status_layout.addWidget(label)
        layout.addWidget(self.sidebar_status)

        self.sidebar_footer = QLabel("")
        self.sidebar_footer.setFont(T.font(9))
        self.sidebar_footer.setWordWrap(True)
        self.sidebar_footer.setStyleSheet(f"color: {T.TEXT_FAINT}; background: transparent;")
        layout.addWidget(self.sidebar_footer)
        return sidebar

    # -- navigation -------------------------------------------------------
    def show_view(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.buttons[index].setChecked(True)
        view = self.stack.currentWidget()
        failures = view.safe_refresh()
        if failures:
            self.statusBar().showMessage(f"a section could not refresh: {failures[0]}")

    def refresh_current(self) -> None:
        view = self.stack.currentWidget()
        if view is None:
            return
        failures = view.safe_refresh()
        if failures:
            self.statusBar().showMessage(f"a section could not refresh: {failures[0]}")
        self._refresh_status_bar()

    # -- status -----------------------------------------------------------
    def _refresh_status_bar(self) -> None:
        try:
            snapshot = self.app.overview_snapshot()
        except Exception as exc:
            self.statusBar().showMessage(f"state unavailable: {exc}")
            return
        resources = snapshot["resources"]
        runner = snapshot["agent"]["runner"]
        backend = snapshot["agent"]["backend"]
        counts = snapshot["agent"]["counts"]
        self.brand_sub.setText(f"data root: {shorten(str(self.app.data_root), 28)}")
        self.sidebar_agent.setText(
            f"agent: {len(runner['active'])} running" if runner["active"] else f"agent: {counts.get('total', 0)} tasks recorded"
        )
        self.sidebar_model.setText(f"model: {backend['name']}")
        training = snapshot["training"]["active"]
        self.sidebar_training.setText(
            f"training: {training[0]['progress'].get('percent', 0):.0f}%" if training else "training: idle"
        )
        self.sidebar_resources.setText(
            f"cpu {resources.get('cpu_percent', 0):.0f}% · ram {(resources.get('ram_used_mb') or 0) / 1024:.1f} GB"
        )
        usb = snapshot["storage"].get("usb_active")
        self.sidebar_footer.setText(
            f"uptime {human_duration(snapshot['app']['uptime'])}\n"
            f"storage {'USB · ' + str(usb) if usb else 'internal'}\n"
            f"governor: {(snapshot.get('governor') or {}).get('mode', '—')}"
        )
        state = "idle"
        colour = T.TEXT_SOFT
        if not snapshot["hardware_ready"]:
            state, colour = "probing hardware", T.ACCENT
        elif runner["active"]:
            state, colour = f"{len(runner['active'])} task(s) running", T.SUCCESS
        elif runner["queued"]:
            state, colour = f"{runner['queued']} queued", T.ACCENT
        self.dot.set_state(state, colour)

    # -- events -----------------------------------------------------------
    def _on_event(self, topic: str, payload: dict[str, Any]) -> None:
        current = self.stack.currentWidget()
        handler = getattr(current, "on_event", None)
        if callable(handler):
            try:
                handler(topic, payload)
            except Exception:
                pass
        if topic in (Topics.TASK_FINISHED, Topics.TRAINING_FINISHED, Topics.APPROVAL_REQUESTED, Topics.SETTINGS_CHANGED):
            # the cheap views can refresh immediately; heavy ones wait for the timer
            if current is self.views["Overview"]:
                current.safe_refresh()
            if topic == Topics.TASK_FINISHED:
                status = payload.get("status", "")
                self.statusBar().showMessage(
                    f"task {payload.get('task_id', '')[:8]} {status}: {shorten(str(payload.get('summary') or ''), 120)}"
                )
            elif topic == Topics.APPROVAL_REQUESTED:
                self.statusBar().showMessage(f"approval needed: {shorten(str(payload.get('summary') or ''), 140)}")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._refresh_timer.stop()
        self._status_timer.stop()
        self.bridge.stop()
        event.accept()
