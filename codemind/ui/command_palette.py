"""The command palette (Ctrl+K): one input for every action in the application.

The list is built from *real* capabilities — the seven views, the agent, training, storage and the
diagnostics entry points — so it can never drift into a decorative menu of things that do nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsOpacityEffect,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from codemind.ui import anim
from codemind.ui import theme as T
from codemind.ui.widgets.glass import KeyValue


@dataclass
class Command:
    """One entry: what it says, what it does, and how it is found."""

    title: str
    run: Callable[[], None]
    detail: str = ""
    glyph: str = "›"
    keywords: str = ""
    section: str = ""

    def haystack(self) -> str:
        return f"{self.title} {self.detail} {self.keywords} {self.section}".lower()


def _score(command: Command, query: str) -> int:
    """Cheap, predictable ranking: prefix beats word start beats substring beats subsequence."""
    if not query:
        return 1
    haystack = command.haystack()
    if haystack.startswith(query):
        return 1000
    if f" {query}" in haystack:
        return 700
    if query in haystack:
        return 400
    # subsequence match ("rv ag" -> "review agent"): keeps fuzzy typing usable
    position = 0
    for character in query:
        position = haystack.find(character, position) + 1
        if position == 0:
            return 0
    return 120


class CommandPalette(QDialog):
    """Frameless glass overlay with a filterable list of commands."""

    def __init__(self, commands: Sequence[Command], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self._all = list(commands)
        self._shown: list[Command] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        panel = _Panel()
        outer.addWidget(panel)

        self.query = QLineEdit()
        self.query.setPlaceholderText("Type a command…  (↑↓ choose, Enter run, Esc close)")
        self.query.setFont(T.font(13))
        self.query.setStyleSheet(
            f"QLineEdit {{ background: rgba(0,0,0,0.34); color: {T.TEXT}; border: 1px solid {T.HAIRLINE};"
            f" border-radius: 10px; padding: 10px 12px; selection-background-color: {T.ACCENT_SOFT}; }}"
        )
        self.query.textChanged.connect(lambda _text: self._populate())
        self.query.installEventFilter(self)
        panel.body.addWidget(self.query)

        self.results = QListWidget()
        self.results.setFont(T.font(12))
        self.results.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; color: {T.TEXT}; outline: none; }}"
            f"QListWidget::item {{ padding: 7px 9px; border-radius: 8px; }}"
            f"QListWidget::item:selected {{ background: {T.color(T.ACCENT, 0.20).name(QColor.HexArgb)}; color: {T.TEXT}; }}"
        )
        self.results.itemActivated.connect(lambda _item: self._run_selected())
        panel.body.addWidget(self.results)

        self.hint = QLabel("")
        self.hint.setFont(T.font(10))
        self.hint.setStyleSheet(f"color: {T.TEXT_FAINT}; background: transparent;")
        panel.body.addWidget(self.hint)

        self.resize(660, 420)
        self._populate()

    # -- behaviour --------------------------------------------------------
    def _populate(self) -> None:
        query = self.query.text().strip().lower()
        scored = [(command, _score(command, query)) for command in self._all]
        ranked = [pair for pair in scored if pair[1] > 0]
        ranked.sort(key=lambda pair: (-pair[1], pair[0].section, pair[0].title))
        self._shown = [command for command, _ in ranked[:80]]
        self.results.clear()
        for command in self._shown:
            row = QListWidgetItem(f"  {command.glyph}   {command.title}")
            if command.detail:
                row.setToolTip(command.detail)
            self.results.addItem(row)
        if self._shown:
            self.results.setCurrentRow(0)
        self.hint.setText(f"{len(self._shown)} of {len(self._all)} actions · Enter runs the highlighted action")

    def _run_selected(self) -> None:
        row = self.results.currentRow()
        if 0 <= row < len(self._shown):
            command = self._shown[row]
            self.accept()
            # run outside the dialog's lifetime so a command may open another window
            command.run()

    def open(self) -> None:
        self.query.setFocus()
        anim.fade_in(self, ms=140)
        self.exec()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt naming
        if obj is self.query and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                row = self.results.currentRow()
                step = 1 if key == Qt.Key_Down else -1
                row = max(0, min(len(self._shown) - 1, row + step))
                self.results.setCurrentRow(row)
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._run_selected()
            return
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


class _Panel(QWidget):
    """Glass surface behind the palette contents."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QVBoxLayout as _VBox

        self.body = _VBox(self)
        self.body.setContentsMargins(T.PAD + 4, T.PAD + 4, T.PAD + 4, T.PAD + 4)
        self.body.setSpacing(10)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        T.paint_shadow(painter, rect, radius=T.RADIUS, strength=1.0)
        T.paint_glass(painter, rect, radius=T.RADIUS, glow=T.ACCENT)
        painter.end()


def _hook(view, name: str):
    """A callable attribute of a view, or None — a missing hook must never break the palette."""
    hook = getattr(view, name, None)
    return hook if callable(hook) else None


def build_commands(window) -> list[Command]:
    """Every action the palette offers, built from the live application context."""
    commands: list[Command] = []
    app = getattr(window, "app", None)

    def add_view(index: int, title: str, glyph: str, detail: str) -> None:
        commands.append(
            Command(
                title=title,
                detail=detail,
                glyph=glyph,
                section="Views",
                keywords=f"goto open show {index + 1}",
                run=lambda i=index: window.show_view(i),
            )
        )

    for index, (title, glyph, _cls) in enumerate(getattr(window, "VIEWS", ()) or ()):
        add_view(index, f"Open {title}", glyph, f"Show the {title} view (Ctrl+{index + 1})")

    def refresh() -> None:
        window.refresh_current()

    commands.append(Command("Refresh this view", refresh, detail="Re-read live state (F5)", glyph="⟳", section="Application", keywords="reload update"))
    commands.append(Command("Keyboard shortcuts", window.show_shortcuts, detail="List every shortcut (F1)", glyph="☰", section="Application", keywords="keys help"))
    commands.append(Command("Open data folder", window.open_data_root, detail=str(getattr(app, "data_root", "")), glyph="⌸", section="Application", keywords="files storage folder"))
    commands.append(Command("Copy diagnostics", window.copy_diagnostics, detail="Hardware, backend, tools, policy — as text", glyph="◈", section="Application", keywords="doctor report clipboard"))
    commands.append(Command("Quit CodeMind", window.quit_application, detail="Stop the agent and close the window", glyph="⏻", section="Application", keywords="exit close"))

    agent_view = (getattr(window, "views", {}) or {}).get("Coding Agent")
    for title, hook, detail, glyph, keywords in (
        ("Focus the task prompt", "focus_prompt", "Type a new task for the agent", "✎", "new task ask"),
        ("Cancel the active task", "cancel_active", "Stop the running task", "■", "stop abort"),
        ("Export the change passport", "export_passport", "Write the evidence file of a task", "▤", "evidence report proof"),
        ("Roll back a task", "rollback_last", "Restore the files a task changed", "↺", "undo revert restore"),
    ):
        callback = _hook(agent_view, hook)
        if callback is not None:
            commands.append(Command(title, callback, detail=detail, glyph=glyph, section="Agent", keywords=keywords))

    overview = (getattr(window, "views", {}) or {}).get("Overview")
    for title, hook, detail, glyph, keywords in (
        ("Run an autonomy session now", "run_autonomy_now", "Ask the scheduler to look for work", "◉", "session maintenance"),
        ("Accept all pending proposals", "accept_all_proposals", "Turn every proposal into a task", "✔", "approve proposals"),
    ):
        callback = _hook(overview, hook)
        if callback is not None:
            commands.append(Command(title, callback, detail=detail, glyph=glyph, section="Autonomy", keywords=keywords))

    training = (getattr(window, "views", {}) or {}).get("Training")
    if training is not None:
        start = _hook(training, "start_run")
        if start is not None:
            commands.append(Command("Start a training run", start, detail="Headless, using the current dataset and method", glyph="▶", section="Training", keywords="train lora adapter"))

    sandbox = (getattr(window, "views", {}) or {}).get("Sandbox")
    for title, hook, detail, glyph, keywords in (
        ("New isolated workspace", "create_workspace", "Copy a project into a sandbox", "⬡", "isolate copy"),
        ("Stop every background process", "stop_all_jobs", "End all jobs started from the Sandbox view", "⊘", "kill jobs processes"),
    ):
        callback = _hook(sandbox, hook)
        if callback is not None:
            commands.append(Command(title, callback, detail=detail, glyph=glyph, section="Sandbox", keywords=keywords))

    if app is not None and hasattr(app, "hardware"):
        commands.append(Command("Re-probe the hardware", window.reprobe_hardware, detail="Detect CPU, RAM, GPU and disks again", glyph="⚙", section="System", keywords="gpu detect hw"))
    return commands
