"""The main window: sidebar navigation, the seven views, and the global status bar.

The window owns everything that is global rather than per-view: the appearance settings, the
command palette, the tray icon, the splash, the keyboard shortcuts, the geometry memory and the
status bar. Views stay unaware of each other and of the shell.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from codemind.events import Topics
from codemind.ui import anim
from codemind.ui import theme as T
from codemind.ui.bridge import EventBridge
from codemind.ui.command_palette import CommandPalette, build_commands
from codemind.ui.widgets.glass import Pill, StatusDot
from codemind.ui.views.agent import AgentView
from codemind.ui.views.github import GithubView
from codemind.ui.views.memory import MemoryView
from codemind.ui.views.overview import OverviewView
from codemind.ui.views.sandbox import SandboxView
from codemind.ui.views.settings import SettingsView
from codemind.ui.views.training import TrainingView
from codemind.utils.files import human_duration
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

VIEW_KEYS = ("overview", "agent", "training", "sandbox", "memory", "github", "settings")

ICON_PATH = Path(__file__).resolve().parents[2] / "assets" / "codemind.png"

SHORTCUTS = (
    ("Ctrl+K", "Command palette — every action in one input"),
    ("Ctrl+1 … Ctrl+7", "Jump to a view"),
    ("F5 / Ctrl+R", "Refresh the current view"),
    ("Ctrl+E", "Export the change passport of the last task"),
    ("Ctrl+Shift+Z", "Roll back the files the last task changed"),
    ("Ctrl+T", "Focus the task prompt in the Coding Agent"),
    ("Ctrl+A", "Run an autonomy session now"),
    ("F1", "This list"),
    ("Ctrl+Q", "Quit CodeMind"),
)


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


class Splash(QWidget):
    """Brief, frameless glass splash while the database and hardware probe finish."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.SplashScreen | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(420, 210)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 22)
        layout.setSpacing(6)
        self.mark = QLabel("◆")
        self.mark.setFont(T.font(30, QFont.Bold))
        self.mark.setStyleSheet(f"color: {T.ACCENT}; background: transparent;")
        name = QLabel("CodeMind")
        name.setFont(T.font(19, QFont.DemiBold))
        name.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self.line = QLabel("starting the agent…")
        self.line.setFont(T.font(11))
        self.line.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        self.detail = QLabel("")
        self.detail.setFont(T.font(10))
        self.detail.setStyleSheet(f"color: {T.TEXT_FAINT}; background: transparent;")
        for widget in (self.mark, name, self.line, self.detail):
            widget.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            layout.addWidget(widget)
        layout.addStretch(1)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        from PySide6.QtCore import QRectF

        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        T.paint_shadow(painter, rect, radius=T.RADIUS, strength=1.0)
        T.paint_glass(painter, rect, radius=T.RADIUS, glow=T.ACCENT)
        painter.end()

    def finish(self) -> None:
        if not anim.enabled():
            self.close()
            return
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        animation = anim.Tween(1.0, 0.0, lambda value: effect.setOpacity(value), ms=260, parent=self, on_done=self.close)
        self._fade = animation


class MainWindow(QMainWindow):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self.VIEWS = VIEWS
        self.VIEW_TITLES = [title for title, _glyph, _cls in VIEWS]
        # appearance first: every widget below captures colours when it is built
        T.apply_appearance(getattr(app, "config", None))
        self.setWindowTitle("CodeMind — autonomous coding assistant")
        self.setWindowIcon(app_icon())
        self.resize(1480, 940)
        self.setMinimumSize(1120, 720)
        self._restore_geometry()
        self.bridge = EventBridge(parent=self)
        self.bridge.event.connect(self._on_event)
        self._last_notification: dict[str, float] = {}
        self._splash: Splash | None = None
        self._splash_shown = False
        self._tray: QSystemTrayIcon | None = None

        central = QWidget()
        central.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())
        self.stack = anim.FadeStack()
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.views: dict[str, Any] = {}
        for title, _glyph, view_cls in VIEWS:
            view = view_cls(app)
            self.views[title] = view
            self.stack.addWidget(view)

        self._build_status_bar()
        self._build_shortcuts()
        self._build_tray()

        start = self._start_view_index()
        self.stack.setCurrentIndex(start)
        self.buttons[start].setChecked(True)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1500)
        self._refresh_timer.timeout.connect(self.refresh_current)
        self._refresh_timer.start()
        self.refresh_current()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self._refresh_status_bar)
        self._status_timer.start()

    # -- appearance --------------------------------------------------------
    def _appearance(self):
        config = getattr(self.app, "config", None)
        return getattr(config, "app", config)

    def _start_view_index(self) -> int:
        wanted = str(getattr(self._appearance(), "start_view", "overview") or "overview").lower()
        try:
            return VIEW_KEYS.index(wanted)
        except ValueError:
            return 0

    def _restore_geometry(self) -> None:
        settings = self._appearance()
        if not getattr(settings, "restore_geometry", True):
            return
        rect = getattr(settings, "window", None) or {}
        try:
            width = int(rect.get("w") or 0)
            height = int(rect.get("h") or 0)
            if width >= 900 and height >= 600:
                self.resize(width, height)
            x, y = int(rect.get("x") or -1), int(rect.get("y") or -1)
            if x >= 0 and y >= 0:
                self.move(x, y)
        except (TypeError, ValueError):  # pragma: no cover - a corrupted settings file
            pass

    def _save_geometry(self) -> None:
        settings = self._appearance()
        if not getattr(settings, "restore_geometry", True):
            return
        try:
            geometry = self.geometry()
            self.app.update_settings(
                {"app.window": {"w": geometry.width(), "h": geometry.height(), "x": geometry.x(), "y": geometry.y()}}
            )
        except Exception:  # pragma: no cover - never block quitting
            pass

    def apply_appearance(self, *, rebuild: bool = False) -> None:
        """Adopt appearance settings that changed while the window is open."""
        before = T.appearance()
        current = T.apply_appearance(getattr(self.app, "config", None))
        if rebuild or before.accent != current.accent or before.mode != current.mode:
            self.rebuild_views()
            self._restyle_shell()
            return
        self._restyle_shell()
        for view in self.views.values():
            repaint = getattr(view, "repaint_theme", None)
            if callable(repaint):
                repaint()
            view.update()

    def rebuild_views(self) -> None:
        """Rebuild every view: accent colours are baked into style sheets at construction."""
        index = max(0, self.stack.currentIndex())
        for title, view in list(self.views.items()):
            self.stack.removeWidget(view)
            view.deleteLater()
        self.views.clear()
        for title, _glyph, view_cls in VIEWS:
            view = view_cls(self.app)
            self.views[title] = view
            self.stack.addWidget(view)
        index = max(0, min(index, len(VIEWS) - 1))
        self.stack.setCurrentIndex(index)
        self.buttons[index].setChecked(True)
        self.refresh_current()

    def _restyle_shell(self) -> None:
        for button in getattr(self, "buttons", []):
            button._refresh()
        if hasattr(self, "brand_sub"):
            self.sidebar.setStyleSheet(f"background: rgba(0,0,0,0.28); border-right: 1px solid {T.HAIRLINE};")
        if hasattr(self, "status_message"):
            self.statusBar().setStyleSheet(
                f"QStatusBar {{ color: {T.TEXT_SOFT}; background: rgba(0,0,0,0.35); }}"
            )
        self.update()

    # -- chrome -----------------------------------------------------------
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        self.sidebar = sidebar
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

        self.palette_button = QPushButton("  ☰   Search && commands        Ctrl K")
        self.palette_button.setCursor(Qt.PointingHandCursor)
        self.palette_button.setFont(T.font(10))
        self.palette_button.setMinimumHeight(30)
        self.palette_button.setStyleSheet(
            f"QPushButton {{ text-align: left; color: {T.TEXT_SOFT}; background: rgba(255,255,255,0.04);"
            f" border: 1px solid {T.HAIRLINE}; border-radius: 9px; padding: 4px 9px; }}"
            f"QPushButton:hover {{ color: {T.TEXT}; border-color: {T.color(T.ACCENT, 0.45).name(QColor.HexArgb)}; }}"
        )
        self.palette_button.clicked.connect(self.show_palette)
        layout.addWidget(self.palette_button)

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

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        bar.setStyleSheet(f"QStatusBar {{ color: {T.TEXT_SOFT}; background: rgba(0,0,0,0.35); }}")
        self.status_message = QLabel("starting background services…")
        self._status_is_boot = True
        self.status_message.setFont(T.font(10))
        self.status_message.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        bar.addWidget(self.status_message, 1)
        self.status_task = Pill("no task", color_value=T.TEXT_FAINT)
        self.status_governor = Pill("governor —", color_value=T.TEXT_FAINT)
        self.status_evidence = Pill("evidence —", color_value=T.TEXT_FAINT)
        bar.addPermanentWidget(self.status_task)
        bar.addPermanentWidget(self.status_evidence)
        bar.addPermanentWidget(self.status_governor)

    def _build_shortcuts(self) -> None:
        def bind(sequence: str, callback) -> None:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts = getattr(self, "_shortcuts", [])
            self._shortcuts.append(shortcut)

        bind("Ctrl+K", self.show_palette)
        bind("Ctrl+Shift+P", self.show_palette)
        bind("F5", self.refresh_current)
        bind("Ctrl+R", self.refresh_current)
        bind("F1", self.show_shortcuts)
        bind("Ctrl+Q", self.quit_application)
        for index in range(len(VIEWS)):
            bind(f"Ctrl+{index + 1}", lambda i=index: self.show_view(i))

    # -- tray --------------------------------------------------------------
    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(app_icon(), self)
        tray.setToolTip("CodeMind")
        menu = QMenu()
        open_action = menu.addAction("Open CodeMind")
        open_action.triggered.connect(self._restore_from_tray)
        session_action = menu.addAction("Run an autonomy session now")
        session_action.triggered.connect(self._run_autonomy_from_tray)
        menu.addSeparator()
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_application)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: self._restore_from_tray() if reason == QSystemTrayIcon.Trigger else None)
        tray.show()
        self._tray = tray

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _run_autonomy_from_tray(self) -> None:
        overview = self.views.get("Overview")
        if overview is not None and hasattr(overview, "run_autonomy_now"):
            overview.run_autonomy_now()

    def notify(self, title: str, message: str, *, tone: str = "accent", key: str = "", cooldown: float = 20.0) -> None:
        """Desktop notification (tray) plus a status line — never spam the same event twice."""
        if key:
            last = self._last_notification.get(key, 0.0)
            if time.time() - last < cooldown:
                return
            self._last_notification[key] = time.time()
        self.set_status(message)
        if self._tray is None:
            return
        icon = {
            "success": QSystemTrayIcon.Information,
            "danger": QSystemTrayIcon.Critical,
            "warning": QSystemTrayIcon.Warning,
        }.get(tone, QSystemTrayIcon.Information)
        try:
            self._tray.showMessage(title, message, icon, 6000)
        except Exception:  # pragma: no cover - desktop without a notification daemon
            pass

    def set_status(self, message: str) -> None:
        self.status_message.setText(message)

    # -- navigation -------------------------------------------------------
    def show_view(self, index: int) -> None:
        if not 0 <= index < len(self.views):
            return
        self.stack.setCurrentIndex(index)
        self.buttons[index].setChecked(True)
        view = self.stack.currentWidget()
        failures = view.safe_refresh() if view is not None else []
        if failures:
            self.set_status(f"a section could not refresh: {failures[0]}")

    def refresh_current(self) -> None:
        view = self.stack.currentWidget()
        if view is None:
            return
        failures = view.safe_refresh()
        if failures:
            self.set_status(f"a section could not refresh: {failures[0]}")
        self._refresh_status_bar()

    # -- palette & dialogs -------------------------------------------------
    def show_palette(self) -> None:
        palette = CommandPalette(build_commands(self), self)
        palette.open()

    def show_shortcuts(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard shortcuts")
        dialog.setStyleSheet(f"QDialog {{ background: {T.BG_RAISED}; }}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        title = QLabel("Keyboard shortcuts")
        title.setFont(T.font(14, QFont.DemiBold))
        title.setStyleSheet(f"color: {T.TEXT};")
        layout.addWidget(title)
        for keys, description in SHORTCUTS:
            row = QHBoxLayout()
            key_label = QLabel(keys)
            key_label.setFont(T.font(11, QFont.DemiBold, mono=True))
            key_label.setMinimumWidth(140)
            key_label.setStyleSheet(f"color: {T.ACCENT};")
            text = QLabel(description)
            text.setFont(T.font(11))
            text.setStyleSheet(f"color: {T.TEXT_SOFT};")
            row.addWidget(key_label)
            row.addWidget(text, 1)
            layout.addLayout(row)
        anim.fade_in(dialog)
        dialog.exec()

    def open_data_root(self) -> None:
        from PySide6.QtGui import QDesktopServices

        target = Path(str(self.app.data_root))
        opened = False
        try:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        except Exception:  # pragma: no cover - no desktop services
            opened = False
        self.set_status(
            f"data folder {target}" if opened else f"data folder: {target} (no file manager available here)"
        )

    def copy_diagnostics(self) -> None:
        snapshot = self.app.overview_snapshot()
        hardware = snapshot["hardware"]
        inference = snapshot["agent"]["backend"]
        lines = [
            f"CodeMind diagnostics — {time.strftime('%Y-%m-%d %H:%M')}",
            f"data root: {snapshot['app']['data_root']}",
            f"hardware: {hardware['cpu_model']} · {hardware['logical_cores']} threads · "
            f"{hardware['ram_total_mb'] / 1024:.1f} GB RAM · {hardware['gpu_summary']} (tier {hardware['tier']})",
            f"inference: {inference['name']} — {inference['reason']}",
            f"training: {snapshot['training']['stats'].get('runs', 0)} runs · "
            f"active {snapshot['training']['active_adapter'] or 'baseline'}",
            f"tasks: {snapshot['agent']['counts']}",
            f"tools: {len(snapshot['agent']['tools'])} registered",
        ]
        payload = "\n".join(lines)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(payload)
        self.set_status("diagnostics copied to the clipboard")
        self.notify("Diagnostics copied", "Hardware, backend, tasks and tools are on your clipboard.", key="copy-diag")

    def reprobe_hardware(self) -> None:
        rescan = getattr(self.app, "rescan_hardware", None)
        if callable(rescan):
            rescan()
            self.set_status("hardware probe restarted")
            self.notify("Probing hardware", "CPU, RAM, GPU and disks are being detected again.", key="reprobe")

    # -- status -----------------------------------------------------------
    def _refresh_status_bar(self) -> None:
        try:
            snapshot = self.app.overview_snapshot()
        except Exception as exc:
            self.set_status(f"state unavailable: {exc}")
            return
        resources = snapshot["resources"]
        runner = snapshot["agent"]["runner"]
        backend = snapshot["agent"]["backend"]
        counts = snapshot["agent"]["counts"]
        if getattr(self, "_status_is_boot", False):
            # the boot line is only true for the first second: replace it with the real state
            self._status_is_boot = False
            self.set_status(
                f"ready — {backend['name']} planner · {snapshot['agent'].get('tools', 0)} tools · "
                f"{counts.get('total', 0)} task(s) recorded · logs in {snapshot['app']['log_path']}"
            )
        self.brand_sub.setText(f"data root: {shorten(str(self.app.data_root), 26)}")
        active_task = snapshot["agent"].get("active_task") or {}
        active_count = int(runner.get("active_count") or 0)
        self.sidebar_agent.setText(
            f"agent: {active_count} running" if active_count else f"agent: {counts.get('total', 0)} tasks recorded"
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

        # permanent pills: what is doing work right now, and whether the results are proven
        if active_task:
            steps = (active_task.get("step_counts") or {}).get("total") or 0
            done = (active_task.get("step_counts") or {}).get("done") or 0
            progress = f" {done}/{steps}" if steps else ""
            self.status_task.set_state(
                f"{active_task.get('status_label') or active_task.get('status', 'running')}{progress}", T.color(T.ACCENT).name()
            )
        elif runner.get("queued"):
            self.status_task.set_state(f"{runner['queued']} queued", T.color(T.INFO).name())
        else:
            self.status_task.set_state("no task", T.color(T.TEXT_FAINT).name())
        quality = snapshot.get("quality") or {}
        if quality.get("completed"):
            rate = quality.get("verified_rate", 0.0) * 100
            tone = T.color(T.SUCCESS).name() if quality.get("unverified", 0) == 0 else T.color(T.WARNING).name()
            self.status_evidence.set_state(f"evidence {rate:.0f}%", tone)
        else:
            self.status_evidence.set_state("evidence —", T.color(T.TEXT_FAINT).name())
        governor = snapshot.get("governor") or {}
        self.status_governor.set_state(
            f"governor {governor.get('mode', '—')}", T.color(T.TEXT_SOFT).name()
        )

        state, colour = "idle", T.TEXT_SOFT
        if not snapshot["hardware_ready"]:
            state, colour = "probing hardware", T.ACCENT
        elif active_count:
            state, colour = f"{active_count} task(s) running", T.SUCCESS
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
        if topic == Topics.TASK_FINISHED:
            status = str(payload.get("status", ""))
            summary = shorten(str(payload.get("summary") or ""), 160)
            self.set_status(f"task {payload.get('task_id', '')[:8]} {status}: {summary}")
            tone = "success" if status == "completed" else "danger" if status in ("failed", "blocked") else "warning"
            self.notify(
                f"Task {status}",
                summary or f"task {payload.get('task_id', '')[:8]}",
                tone=tone,
                key=f"task-{payload.get('task_id', '')}",
            )
        elif topic == Topics.APPROVAL_REQUESTED:
            message = shorten(str(payload.get("summary") or ""), 160)
            self.set_status(f"approval needed: {message}")
            self.notify("Approval needed", message or "the agent is waiting for your decision", tone="warning", key="approval")
        elif topic == Topics.TRAINING_FINISHED:
            run_id = str(payload.get("run_id", ""))[:8]
            status = str(payload.get("status", ""))
            self.set_status(f"training {run_id} {status}")
            self.notify(
                f"Training {status}",
                f"run {run_id} finished — open Training for the numbers",
                tone="success" if status == "completed" else "warning",
                key=f"training-{run_id}",
            )
        elif topic == Topics.SETTINGS_CHANGED:
            changed = [str(item) for item in (payload.get("changed") or [])]
            if any(key.startswith("app.") for key in changed):
                self.apply_appearance()
            if any(key == "app.restore_geometry" for key in changed):
                self._restore_geometry()
        if topic in (Topics.TASK_FINISHED, Topics.TRAINING_FINISHED, Topics.APPROVAL_REQUESTED, Topics.SETTINGS_CHANGED):
            if current is self.views.get("Overview"):
                current.safe_refresh()

    # -- lifecycle ---------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        settings = self._appearance()
        if self._splash_shown or not getattr(settings, "show_splash", True) or not anim.enabled():
            return
        self._splash_shown = True
        splash = Splash()
        self._splash = splash
        screen = self.screen()
        if screen is not None:
            geometry = screen.availableGeometry()
            splash.move(geometry.center().x() - splash.width() // 2, geometry.center().y() - splash.height() // 2)
        snapshot_ready = getattr(self.app, "_hardware_ready", None)
        if snapshot_ready is not None and hasattr(snapshot_ready, "is_set"):
            try:
                splash.line.setText("probing hardware…" if not snapshot_ready.is_set() else "loading your projects…")
            except Exception:  # pragma: no cover
                pass
        splash.detail.setText(f"data root {shorten(str(self.app.data_root), 60)}")
        splash.show()
        anim.fade_in(splash, ms=220)
        QTimer.singleShot(900, splash.finish)
        QTimer.singleShot(1400, lambda: self._splash and self._splash.close())

    def quit_application(self) -> None:
        self._save_geometry()
        self.close()
        application = QApplication.instance()
        if application is not None:
            application.quit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        settings = self._appearance()
        if getattr(settings, "minimize_to_tray", False) and self._tray is not None:
            event.ignore()
            self.hide()
            self.notify("CodeMind keeps running", "The agent stays active in the tray.", key="tray-hide", cooldown=60)
            return
        self.stop_timers()
        self.bridge.stop()
        self._save_geometry()
        event.accept()

    def stop_timers(self) -> None:
        """Stop the refresh loops — used by tests and by the close path."""
        self._refresh_timer.stop()
        self._status_timer.stop()
        if self._tray is not None:
            self._tray.hide()
