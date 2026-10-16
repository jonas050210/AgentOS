"""Settings: every configurable value, generated from the real schema.

The form is built by walking :data:`codemind.config.SECTIONS`, so a new field in the dataclasses
appears here automatically with its label, help text, bounds and choices. Nothing is hard-coded
and nothing is decorative: changing a widget writes through :class:`ConfigStore`, which validates
and persists the value and notifies every subsystem.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any, Callable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from codemind.config import SECTIONS, Config
from codemind.ui import theme as T
from codemind.ui.views.base import SectionHeader, View
from codemind.ui.widgets.glass import ActionButton, Card, KeyValue, Pill, hline

SECTION_TITLES = {
    "app": ("Application", "Identity, and where CodeMind keeps its own data"),
    "models": ("Models", "Local inference backends, model selection and download behaviour"),
    "agent": ("Agent", "How the agent plans, verifies and how far it may go on its own"),
    "sandbox": ("Sandbox", "Isolation, limits and what commands are allowed"),
    "training": ("Training", "Headless fine-tuning and evaluation policy"),
    "resources": ("Resources", "How the governor shares the machine with your work"),
    "memory": ("Memory", "What the agent remembers and how it is scored"),
    "github": ("GitHub", "Repositories, credentials and pull requests"),
    "autonomy": ("Autonomy", "Scheduled self-directed work on your projects"),
    "security": ("Security", "Approvals, protected paths and secret handling"),
    "logging": ("Logging", "Verbosity, retention and console output"),
    "storage": ("Storage", "Where projects, datasets, checkpoints and logs live, including USB"),
}

PATH_KINDS = {"path", "dir"}
SECRET_HINTS = ("token", "secret", "password", "key")


class SettingsView(View):
    title = "Settings"
    subtitle = "Every value is validated, persisted to settings.json and applied immediately"

    def build(self) -> None:
        top = Card("", direction="h", padding=13)
        self.dirty_pill = Pill("saved", color_value=T.SUCCESS)
        self.path_label = QLabel(str(self.app.config_store.path))
        self.path_label.setFont(T.font(10, mono=True))
        self.path_label.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        top.body.addWidget(QLabel("Settings file"))
        top.body.addWidget(self.path_label)
        top.body.addStretch(1)
        top.body.addWidget(self.dirty_pill)
        for label, callback in (
            ("Reload from disk", self._reload),
            ("Open data folder", self._open_data_root),
            ("Reset all", self._reset_all),
        ):
            top.body.addWidget(ActionButton(label, on_click=callback))
        self.add(top)

        self.warning_card = Card("Configuration problems", "Settings that failed validation and were repaired")
        self.warning_body = QVBoxLayout()
        self.warning_card.content.addLayout(self.warning_body)
        self.warning_card.setVisible(False)
        self.add(self.warning_card)

        self.advanced_checks: list[QCheckBox] = []
        self.advanced_rows: list[QWidget] = []

        for section, cls in SECTIONS.items():
            title, detail = SECTION_TITLES.get(section, (section.title(), ""))
            rows: list[QWidget] = []
            card = Card(title, detail)
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(10)
            grid.setColumnStretch(0, 2)
            grid.setColumnStretch(1, 3)
            row_index = 0
            for field in dataclasses.fields(cls):
                info = field.metadata.get("title")
                if info is None:  # not a user-facing setting
                    continue
                widget = self._make_editor(section, field)
                if widget is None:
                    continue
                label_box = QWidget()
                label_layout = QVBoxLayout(label_box)
                label_layout.setContentsMargins(0, 0, 0, 0)
                label_layout.setSpacing(1)
                name = QLabel(field.metadata["title"])
                name.setFont(T.font(11, QFont.Medium))
                name.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
                label_layout.addWidget(name)
                help_text = field.metadata.get("help") or ""
                if field.metadata.get("unit"):
                    help_text = f"{help_text} (unit: {field.metadata['unit']})".strip()
                if help_text:
                    note = QLabel(help_text)
                    note.setFont(T.font(10))
                    note.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
                    note.setWordWrap(True)
                    label_layout.addWidget(note)
                grid.addWidget(label_box, row_index, 0)
                holder = QWidget()
                holder_layout = QHBoxLayout(holder)
                holder_layout.setContentsMargins(0, 0, 0, 0)
                holder_layout.setSpacing(6)
                holder_layout.addWidget(widget, 1)
                if field.metadata.get("kind") in PATH_KINDS:
                    holder_layout.addWidget(ActionButton("Browse…", on_click=self._browse_factory(section, field.name)))
                grid.addWidget(holder, row_index, 1)
                row_container = QWidget()
                row_container.setVisible(not field.metadata.get("advanced"))
                if field.metadata.get("advanced"):
                    self.advanced_rows.append(row_container)
                rows.append(row_container)
                row_index += 1
            card.content.addLayout(grid)
            self.add(card)

        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(2, 0, 2, 0)
        self.show_advanced = QCheckBox("Show advanced settings")
        self.show_advanced.setFont(T.font(11))
        self.show_advanced.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        self.show_advanced.toggled.connect(self._toggle_advanced)
        footer_layout.addWidget(self.show_advanced)
        footer_layout.addStretch(1)
        footer_layout.addWidget(ActionButton("Open log folder", on_click=self._open_logs))
        footer_layout.addWidget(ActionButton("Copy diagnostics", tone="neutral", on_click=self._copy_diagnostics))
        self.add(footer)

    # -- editors ----------------------------------------------------------
    def _make_editor(self, section: str, field: dataclasses.Field) -> QWidget | None:
        kind = field.metadata.get("kind", "str")
        key = f"{section}.{field.name}"
        value = self.app.config.get(key)
        if kind == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(value))
            widget.setStyleSheet("QCheckBox::indicator { width: 16px; height: 16px; }")
            widget.toggled.connect(lambda checked, k=key: self._apply({k: checked}))
            return widget
        if kind == "choice":
            widget = QComboBox()
            for choice in field.metadata.get("choices") or ():
                widget.addItem(str(choice), str(choice))
            if value is not None and str(value) in [widget.itemData(i) for i in range(widget.count())]:
                widget.setCurrentIndex([widget.itemData(i) for i in range(widget.count())].index(str(value)))
            widget.setStyleSheet(self._input_style())
            widget.currentIndexChanged.connect(lambda _i, k=key, w=widget: self._apply({k: w.currentData()}))
            return widget
        if kind == "list":
            widget = QLineEdit(", ".join(str(v) for v in (value or [])))
            widget.setFont(T.font(11, mono=True))
            widget.setStyleSheet(self._input_style())
            widget.editingFinished.connect(lambda k=key, w=widget: self._apply({k: [part.strip() for part in w.text().split(",") if part.strip()]}))
            return widget
        if kind == "int":
            widget = QSpinBox()
            widget.setRange(int(field.metadata.get("minimum") or 0), int(field.metadata.get("maximum") or 1_000_000))
            if field.metadata.get("step"):
                widget.setSingleStep(int(field.metadata["step"]))
            widget.setValue(int(value or 0))
            widget.setFont(T.font(11, mono=True))
            widget.setStyleSheet(self._input_style())
            widget.valueChanged.connect(lambda v, k=key: self._apply({k: int(v)}, debounce=True))
            return widget
        if kind == "float":
            widget = QDoubleSpinBox()
            widget.setDecimals(4)
            widget.setRange(float(field.metadata.get("minimum") or 0.0), float(field.metadata.get("maximum") or 1e9))
            widget.setSingleStep(float(field.metadata.get("step") or 0.1))
            widget.setValue(float(value or 0.0))
            widget.setFont(T.font(11, mono=True))
            widget.setStyleSheet(self._input_style())
            widget.valueChanged.connect(lambda v, k=key: self._apply({k: float(v)}, debounce=True))
            return widget
        # str / path / dir / text
        widget = QLineEdit("" if value is None else str(value))
        widget.setFont(T.font(11, mono=True))
        if any(hint in field.name for hint in SECRET_HINTS):
            widget.setEchoMode(QLineEdit.Password)
            widget.setPlaceholderText("read from the environment or the GitHub CLI if left empty")
        widget.setStyleSheet(self._input_style())
        widget.editingFinished.connect(lambda k=key, w=widget: self._apply({k: w.text()}))
        return widget

    @staticmethod
    def _input_style() -> str:
        return (
            f"QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: rgba(0,0,0,0.32); color: {T.TEXT};"
            f" border: 1px solid {T.HAIRLINE}; border-radius: 8px; padding: 4px 8px; }}"
            f"QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {T.ACCENT}; }}"
        )

    # -- actions ----------------------------------------------------------
    def _apply(self, values: dict[str, Any], *, debounce: bool = False) -> None:
        self._pending = getattr(self, "_pending", {})
        self._pending.update(values)
        if debounce:
            from PySide6.QtCore import QTimer

            timer = getattr(self, "_debounce", None)
            if timer is None:
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(self._flush)
                self._debounce = timer
            timer.start(350)
            return
        self._flush()

    def _flush(self) -> None:
        values = getattr(self, "_pending", {})
        self._pending = {}
        if not values:
            return
        try:
            changed = self.app.update_settings(values)
            self.dirty_pill.set_state(f"saved {len(changed)} change(s)" if changed else "no change", T.SUCCESS if changed else T.TEXT_SOFT)
        except Exception as exc:
            self.dirty_pill.set_state(f"rejected: {type(exc).__name__}", T.DANGER)
            self.app.tasks.db.insert("task_events", {"task_id": "settings", "ts": __import__("time").time(), "kind": "config.error", "message": str(exc)})

    def _browse_factory(self, section: str, name: str) -> Callable[[], None]:
        def browse() -> None:
            current = str(self.app.config.get(f"{section}.{name}") or os.path.expanduser("~"))
            chosen = QFileDialog.getExistingDirectory(self, "Choose a folder", current)
            if chosen:
                self._apply({f"{section}.{name}": chosen})

        return browse

    def _reload(self) -> None:
        self.app.config_store.load()
        self.app._apply_config(["*"])
        self.dirty_pill.set_state("reloaded from disk", T.ACCENT)
        self.refresh()

    def _reset_all(self) -> None:
        for section in SECTIONS:
            self.app.reset_section(section)
        self.dirty_pill.set_state("defaults restored", T.WARNING)

    def _toggle_advanced(self, visible: bool) -> None:
        for row in getattr(self, "advanced_rows", []):
            row.setVisible(visible)

    def _open_data_root(self) -> None:
        import subprocess

        subprocess.Popen(["xdg-open", str(self.app.data_root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _open_logs(self) -> None:
        import subprocess

        subprocess.Popen(["xdg-open", self.app.info.log_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _copy_diagnostics(self) -> None:
        from PySide6.QtWidgets import QApplication

        health = self.app.health()
        lines = [
            "CodeMind diagnostics",
            f"data root: {self.app.data_root}",
            f"settings:  {self.app.config_store.path}",
            f"database:  {self.app.layout.db_path}",
            f"logs:      {self.app.info.log_path}",
            f"hardware:  {self.app.hardware.summary_line()} (tier {self.app.hardware.tier()})",
            f"python:    {self.app.hardware.python}",
            "",
            "checks:",
        ]
        lines += [f"  [{'ok' if c['ok'] else '!!'}] {c['name']}: {c['detail']}" for c in health["checks"]]
        QApplication.clipboard().setText("\n".join(lines))
        self.dirty_pill.set_state("diagnostics copied", T.ACCENT)

    # -- refresh ----------------------------------------------------------
    def refresh(self) -> None:
        warnings = list(self.app.info.config_warnings)
        visible = bool(warnings)
        self.warning_card.setVisible(visible)
        while self.warning_body.count():
            item = self.warning_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        for warning in warnings:
            label = QLabel(warning)
            label.setWordWrap(True)
            label.setFont(T.font(11))
            label.setStyleSheet(f"color: {T.WARNING}; background: transparent;")
            self.warning_body.addWidget(label)
