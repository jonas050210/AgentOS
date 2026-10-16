"""Memory & Models: what the agent remembers, what it has learned about your projects, and the
local models available on this machine.

Memory (retrieved knowledge) and the trained adapter (learned behaviour) are shown separately
because they are different systems with different guarantees.
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from codemind.memory.memory import MEMORY_KINDS
from codemind.ui import theme as T
from codemind.ui.views.base import SectionHeader, View
from codemind.ui.widgets.glass import ActionButton, Card, EmptyState, KeyValue, Pill, StatTile, hline
from codemind.ui.widgets.logview import LogView
from codemind.ui.widgets.table import DataTable
from codemind.utils.files import human_bytes
from codemind.utils.textutil import shorten


class MemoryView(View):
    title = "Memory & Models"
    subtitle = "Persistent memory across sessions, indexed project knowledge, and the local models on this machine"

    def build(self) -> None:
        # --- tiles ---------------------------------------------------------
        tiles = QHBoxLayout()
        tiles.setSpacing(T.GAP)
        self.tile_memories = StatTile("Memories", "0", sub="", accent=T.ACCENT, icon="✧")
        self.tile_quality = StatTile("Average quality", "—", sub="", accent=T.SUCCESS, icon="◆")
        self.tile_projects = StatTile("Projects indexed", "0", sub="", accent=T.VIOLET, icon="▤")
        self.tile_symbols = StatTile("Indexed symbols", "0", sub="", accent=T.INFO, icon="◇")
        self.tile_models = StatTile("Local models", "0", sub="", accent=T.WARNING, icon="▥")
        for tile in (self.tile_memories, self.tile_quality, self.tile_projects, self.tile_symbols, self.tile_models):
            tiles.addWidget(tile, 1)
        holder = QWidget()
        holder.setLayout(tiles)
        holder.setStyleSheet("background: transparent;")
        self.add(holder)

        # --- memory store --------------------------------------------------
        mem_card = Card("Memory", "Durable knowledge: architecture, preferences, verified solutions, past mistakes and task summaries")
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.memory_query = QLineEdit()
        self.memory_query.setPlaceholderText("search memories — e.g. \"why did the migration fail\"")
        self.memory_query.setFont(T.font(11))
        self.memory_query.setStyleSheet(self._input_style())
        self.memory_query.textChanged.connect(lambda _text: self._refresh_memories())
        self.memory_kind = QComboBox()
        self.memory_kind.addItem("all kinds", "")
        for kind in MEMORY_KINDS:
            self.memory_kind.addItem(kind.replace("_", " "), kind)
        self.memory_kind.setStyleSheet(self._combo_style())
        self.memory_kind.currentIndexChanged.connect(lambda _i: self._refresh_memories())
        controls.addWidget(self.memory_query, 1)
        controls.addWidget(self.memory_kind)
        controls.addWidget(ActionButton("Forget selected", tone="danger", on_click=self._forget_selected))
        controls.addWidget(ActionButton("Pin/unpin", on_click=self._pin_selected))
        controls.addWidget(ActionButton("Export JSONL…", on_click=self._export_memories))
        controls.addWidget(ActionButton("Prune low quality", tone="warning", on_click=self._prune_memories))
        mem_card.content.addLayout(controls)
        self.memory_table = DataTable(["Kind", "Title", "Quality", "Uses", "Updated", "Project"], stretch_column=1)
        self.memory_table.setMinimumHeight(200)
        self.memory_table.itemSelectionChanged.connect(self._select_memory)
        mem_card.content.addWidget(self.memory_table)
        self.memory_body = LogView(max_lines=400)
        self.memory_body.setMinimumHeight(120)
        self.memory_body.set_empty_text("Select a memory above to read its full text, source and evidence.")
        mem_card.content.addWidget(self.memory_body)
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.new_kind = QComboBox()
        self.new_kind.addItems(list(MEMORY_KINDS))
        self.new_kind.setStyleSheet(self._combo_style())
        self.new_title = QLineEdit()
        self.new_title.setPlaceholderText("title — what should be remembered")
        self.new_title.setStyleSheet(self._input_style())
        self.new_quality = QDoubleSpinBox()
        self.new_quality.setMinimumWidth(78)
        self.new_quality.setRange(0.0, 1.0)
        self.new_quality.setSingleStep(0.05)
        self.new_quality.setValue(0.8)
        self.new_quality.setFont(T.font(11, mono=True))
        self.new_quality.setStyleSheet(self._input_style())
        add_row.addWidget(self.new_kind)
        add_row.addWidget(self.new_title, 1)
        add_row.addWidget(QLabel("quality"))
        add_row.addWidget(self.new_quality)
        add_row.addWidget(ActionButton("Remember", tone="accent", on_click=self._remember))
        mem_card.content.addLayout(add_row)
        self.new_body = QPlainTextEdit()
        self.new_body.setFixedHeight(70)
        self.new_body.setPlaceholderText("details, evidence, the exact command or file this came from (optional but much more useful)")
        self.new_body.setFont(T.font(11))
        self.new_body.setStyleSheet(
            f"QPlainTextEdit {{ background: rgba(0,0,0,0.30); color: {T.TEXT}; border: 1px solid {T.HAIRLINE}; border-radius: 8px; padding: 6px; }}"
        )
        mem_card.content.addWidget(self.new_body)
        self.add(mem_card)

        # --- project knowledge ---------------------------------------------
        kn_card = Card("Project knowledge", "Indexed structure the agent uses to find the right files before it edits anything")
        self.projects_table = DataTable(["Project", "Language", "Files", "Symbols", "Test suites", "Last indexed", "Path"], stretch_column=0)
        self.projects_table.set_mono_columns([6])
        self.projects_table.setMinimumHeight(150)
        self.projects_table.itemSelectionChanged.connect(self._select_project)
        kn_card.content.addWidget(self.projects_table)
        kn_buttons = QHBoxLayout()
        kn_buttons.setSpacing(8)
        kn_buttons.addWidget(ActionButton("Index a folder…", tone="accent", on_click=self._register_project))
        kn_buttons.addWidget(ActionButton("Re-index selected", on_click=self._reindex_project))
        kn_buttons.addWidget(ActionButton("Drop selected", tone="danger", on_click=self._drop_project))
        kn_buttons.addStretch(1)
        self.knowledge_hint = QLabel("")
        self.knowledge_hint.setFont(T.font(10))
        self.knowledge_hint.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        kn_buttons.addWidget(self.knowledge_hint)
        kn_card.content.addLayout(kn_buttons)
        self.outline_table = DataTable(["Path", "Symbol", "Kind", "Line"], stretch_column=0)
        self.outline_table.set_mono_columns([0])
        self.outline_table.setMinimumHeight(160)
        kn_card.content.addWidget(self.outline_table)
        self.add(kn_card)

        # --- models --------------------------------------------------------
        models_card = Card("Local models", "Free, locally runnable models only — nothing is sent to a cloud service")
        self.backend_body = QVBoxLayout()
        self.backend_body.setSpacing(5)
        models_card.content.addLayout(self.backend_body)
        self.model_table = DataTable(["Model", "Size", "Fits VRAM", "Installed", "Quantisation", "Notes"], stretch_column=0)
        self.model_table.setMinimumHeight(200)
        models_card.content.addWidget(self.model_table)
        model_buttons = QHBoxLayout()
        models_buttons_row = model_buttons
        self.model_choice = QComboBox()
        self.model_choice.setMinimumWidth(260)
        self.model_choice.setStyleSheet(self._combo_style())
        models_buttons_row.addWidget(self.model_choice, 1)
        models_buttons_row.addWidget(ActionButton("Use this model", tone="accent", on_click=self._select_model))
        models_buttons_row.addWidget(ActionButton("Download", on_click=self._download_model))
        models_buttons_row.addWidget(ActionButton("Refresh catalog", on_click=self._refresh_catalog))
        models_buttons_row.addWidget(ActionButton("Start llama.cpp server", on_click=self._toggle_server))
        models_card.content.addLayout(models_buttons_row)
        self.models_log = LogView(max_lines=400)
        self.models_log.setMinimumHeight(110)
        models_card.content.addWidget(self.models_log)
        self.add(models_card)

        self._memory_entries: list[Any] = []
        self._projects: list[dict[str, Any]] = []

    # -- styles -----------------------------------------------------------
    @staticmethod
    def _input_style() -> str:
        return (
            f"QLineEdit, QDoubleSpinBox {{ background: rgba(0,0,0,0.32); color: {T.TEXT}; border: 1px solid {T.HAIRLINE};"
            f" border-radius: 8px; padding: 4px 8px; }} QLineEdit:focus {{ border-color: {T.ACCENT}; }}"
        )

    @staticmethod
    def _combo_style() -> str:
        return (
            f"QComboBox {{ background: rgba(0,0,0,0.32); color: {T.TEXT}; border: 1px solid {T.HAIRLINE};"
            f" border-radius: 8px; padding: 4px 8px; }}"
            f"QComboBox QAbstractItemView {{ background: {T.SURFACE}; color: {T.TEXT}; }}"
        )

    # -- memory actions ---------------------------------------------------
    def _refresh_memories(self) -> None:
        kind = self.memory_kind.currentData() or ""
        query = self.memory_query.text().strip()
        entries = self.app.memory.search(query, kinds=[kind] if kind else None, limit=200)
        self._memory_entries = entries
        rows = []
        colors = []
        for entry in entries:
            rows.append(
                [
                    entry.kind,
                    shorten(entry.title or entry.body, 70),
                    f"{entry.quality:.2f}",
                    entry.use_count,
                    time.strftime("%m-%d %H:%M", time.localtime(entry.updated_at or time.time())),
                    entry.project_name or "global",
                ]
            )
            colors.append([None, None, T.SUCCESS if entry.quality >= 0.8 else (T.WARNING if entry.quality < 0.5 else None), None, None, None])
        self.memory_table.set_rows(rows, colors=colors)

    def _select_memory(self) -> None:
        row = self.memory_table.currentRow()
        if row < 0 or row >= len(self._memory_entries):
            return
        entry = self._memory_entries[row]
        self.memory_body.clear_text()
        self.memory_body.append_line(f"# {entry.title}\nkind: {entry.kind}   quality: {entry.quality:.2f}   uses: {entry.use_count}   tags: {', '.join(entry.tags) or '—'}")
        self.memory_body.append_line("")
        self.memory_body.append_line(entry.body)
        if entry.evidence:
            self.memory_body.append_line("")
            self.memory_body.append_line(f"evidence: {entry.evidence}")

    def _remember(self) -> None:
        title = self.new_title.text().strip()
        body = self.new_body.toPlainText().strip()
        if not title and not body:
            self.knowledge_hint.setText("a title or a body is required")
            return
        kind = self.new_kind.currentText()
        memory_id = self.app.memory.remember(kind, title, body, quality=self.new_quality.value(), source="user")
        if memory_id:
            self.new_title.clear()
            self.new_body.clear()
            self.knowledge_hint.setText(f"saved memory #{memory_id}")
            self._refresh_memories()
        else:
            self.knowledge_hint.setText("the memory was rejected (empty content)")

    def _forget_selected(self) -> None:
        row = self.memory_table.currentRow()
        if 0 <= row < len(self._memory_entries):
            entry = self._memory_entries[row]
            self.app.memory.forget(entry.id)
            self.knowledge_hint.setText(f"forgot memory #{entry.id}")
            self._refresh_memories()

    def _pin_selected(self) -> None:
        row = self.memory_table.currentRow()
        if 0 <= row < len(self._memory_entries):
            entry = self._memory_entries[row]
            self.app.memory.pin(entry.id, not entry.pinned)
            self.knowledge_hint.setText(f"memory #{entry.id} pinned={not entry.pinned}")
            self._refresh_memories()

    def _export_memories(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        target, _ = QFileDialog.getSaveFileName(self, "Export memories", str(self.app.data_root / "memories.jsonl"), "JSONL (*.jsonl)")
        if not target:
            return
        try:
            count = self.app.memory.export_jsonl(target)
            self.knowledge_hint.setText(f"exported {count} memories")
        except Exception as exc:
            self.knowledge_hint.setText(f"export failed: {exc}")

    def _prune_memories(self) -> None:
        limit = self.app.config.memory.max_entries
        removed = self.app.memory.prune(limit)
        self.knowledge_hint.setText(
            f"pruned {removed} memories; {limit} most valuable entries kept (pinned entries are never removed)"
        )
        self.refresh()

    # -- knowledge actions ------------------------------------------------
    def _register_project(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(self, "Choose a project folder", str(self.app.data_root))
        if not folder:
            return
        try:
            project_id = self.app.knowledge.register_project(folder)
            analysis = self.app.knowledge.project(project_id)
            self.knowledge_hint.setText(f"indexing {folder} …")
            from PySide6.QtCore import QTimer

            def finish() -> None:
                try:
                    result = self.app.knowledge.index_project(project_id)
                    self.knowledge_hint.setText(
                        f"indexed {result.get('inserted', 0)} files, {result.get('symbols', 0)} symbols, {result.get('suites', 0)} test suites"
                    )
                except Exception as exc:
                    self.knowledge_hint.setText(f"index failed: {exc}")
                self.refresh()

            QTimer.singleShot(50, finish)
        except Exception as exc:
            self.knowledge_hint.setText(f"failed: {exc}")

    def _reindex_project(self) -> None:
        row = self.projects_table.currentRow()
        if row < 0 or row >= len(self._projects):
            return
        project = self._projects[row]
        try:
            result = self.app.knowledge.index_project(project["id"])
            self.knowledge_hint.setText(f"{project['name']}: +{result.get('inserted', 0)} files, {result.get('symbols', 0)} symbols")
        except Exception as exc:
            self.knowledge_hint.setText(f"failed: {exc}")
        self.refresh()

    def _drop_project(self) -> None:
        row = self.projects_table.currentRow()
        if row < 0 or row >= len(self._projects):
            return
        project = self._projects[row]
        self.app.knowledge.drop_project(project["id"])
        self.knowledge_hint.setText(f"dropped {project['name']} from the knowledge index")
        self.refresh()

    def _select_project(self) -> None:
        row = self.projects_table.currentRow()
        if row < 0 or row >= len(self._projects):
            return
        project = self._projects[row]
        outline = self.app.knowledge.outline(project["id"], limit=400)
        self.outline_table.set_rows([[item["path"], item.get("symbol") or "", item["kind"], item.get("line") or ""] for item in outline])
        self.knowledge_hint.setText(f"{len(outline)} symbols shown for {project['name']}")

    # -- model actions ----------------------------------------------------
    def _select_model(self) -> None:
        model_id = self.model_choice.currentData()
        if not model_id:
            return
        try:
            result = self.app.models.select_model(str(model_id))
            self.models_log.append_line(f"selected {model_id}: {shorten(str(result), 200)}")
        except Exception as exc:
            self.models_log.stream(f"could not select {model_id}: {exc}", "stderr")
        self.refresh()

    def _download_model(self) -> None:
        model_id = self.model_choice.currentData()
        if not model_id:
            return
        try:
            result = self.app.models.download(str(model_id))
            self.models_log.append_line(f"download started: {shorten(str(result), 240)}")
        except Exception as exc:
            self.models_log.stream(f"download failed: {exc}", "stderr")
        self.refresh()

    def _refresh_catalog(self) -> None:
        try:
            catalog = self.app.models.catalog(probe=True)
            self.models_log.append_line(f"catalog refreshed: {len(catalog)} entries")
        except Exception as exc:
            self.models_log.stream(f"catalog refresh failed: {exc}", "stderr")
        self.refresh()

    def _toggle_server(self) -> None:
        try:
            info = self.app.models.server_info()
            if info.get("running"):
                self.app.models.stop_llama_server()
                self.models_log.append_line("llama.cpp server stopped")
            else:
                result = self.app.models.start_llama_server()
                self.models_log.append_line(f"llama.cpp server: {shorten(str(result), 240)}")
        except Exception as exc:
            self.models_log.stream(f"server control failed: {exc}", "stderr")
        self.refresh()

    # -- refresh ----------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_stats()
        if not self._memory_entries:
            self._refresh_memories()
        self._refresh_projects()
        self._refresh_models()

    def _refresh_stats(self) -> None:
        memory = self.app.memory.stats()
        knowledge = self.app.knowledge.stats()
        self.tile_memories.set_value(str(memory["total"]), sub=f"{memory.get('usable', 0)} above the quality gate")
        self.tile_quality.set_value(f"{memory['avg_quality']:.2f}", sub=f"gate {memory.get('min_quality')}", accent=T.SUCCESS if memory["avg_quality"] >= 0.7 else T.WARNING)
        self.tile_projects.set_value(str(len(self.app.knowledge.projects(limit=200))), sub=f"{knowledge.get('suites', 0)} test suites known")
        self.tile_symbols.set_value(f"{knowledge.get('symbols', 0):,}", sub=f"{knowledge.get('files', 0)} files indexed")
        catalog = self.app.models.catalog(probe=False)
        installed = [entry for entry in catalog if entry.installed]
        self.tile_models.set_value(str(len(installed)), sub=f"{len(catalog)} known in the catalog")

    def _refresh_projects(self) -> None:
        projects = self.app.knowledge.projects(limit=100)
        self._projects = projects
        rows = []
        for project in projects:
            stats = self.app.db.query_one(
                "SELECT COUNT(*) AS files FROM knowledge WHERE project_id = ? AND kind = 'file'", (project["id"],)
            )
            symbols = self.app.db.scalar(
                "SELECT COUNT(*) FROM knowledge WHERE project_id = ? AND kind NOT IN ('file','suite')", (project["id"],), default=0
            )
            suites = len(self.app.knowledge.test_suites(project["id"]))
            rows.append(
                [
                    project.get("name", ""),
                    project.get("primary_language") or project.get("language") or "—",
                    int((stats or {"files": 0})["files"]),
                    symbols,
                    suites,
                    time.strftime("%m-%d %H:%M", time.localtime(project.get("last_indexed") or project.get("last_seen") or time.time())),
                    project.get("path", ""),
                ]
            )
        self.projects_table.set_rows(rows)

    def _refresh_models(self) -> None:
        backend = self.app.models.active_backend_info()
        while self.backend_body.count():
            item = self.backend_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self.backend_body.addWidget(KeyValue("Active backend", f"{backend['name']} — {backend['reason']}", value_color=T.SUCCESS if backend["name"] != "heuristic" else T.WARNING))
        report = self.app.models.compatibility_report()
        self.backend_body.addWidget(KeyValue("Hardware", str(report.get("hardware", ""))))
        self.backend_body.addWidget(
            KeyValue("Recommended model", str(report.get("recommended_model", "—")), value_color=T.ACCENT)
        )
        plan = report.get("training_plan") or {}
        if plan:
            self.backend_body.addWidget(
                KeyValue("Training plan", f"{plan.get('method', '—')} — {shorten(str(plan.get('reason') or plan.get('summary') or ''), 150)}")
            )
        stack = report.get("ml_stack") or {}
        ready = [name for name, value in stack.items() if value]
        self.backend_body.addWidget(
            KeyValue(
                "ML stack",
                ", ".join(f"{k}={v}" for k, v in stack.items() if v) or "no torch/transformers/peft/bitsandbytes installed",
                mono=True,
                value_color=T.SUCCESS if ready else T.WARNING,
            )
        )
        server = self.app.models.server_info() or {}
        self.backend_body.addWidget(
            KeyValue(
                "llama.cpp server",
                f"running on port {server.get('port')}" if server.get("running") else "not running",
                value_color=T.SUCCESS if server.get("running") else T.TEXT_SOFT,
            )
        )
        catalog = self.app.models.catalog(probe=True)
        rows = []
        colors = []
        for entry in catalog:
            fits = "yes" if entry.fits_vram else ("RAM only" if entry.fits_ram else ("unknown" if entry.fits_vram is None else "no"))
            rows.append(
                [
                    entry.name,
                    human_bytes(entry.size_bytes or 0) if entry.size_bytes else "—",
                    fits,
                    "yes" if entry.installed else "no",
                    entry.quantization or "—",
                    shorten(entry.fit_note or entry.notes, 70),
                ]
            )
            colors.append([None, None, T.SUCCESS if entry.fits_vram else (T.WARNING if entry.fits_ram or entry.fits_vram is None else T.DANGER), None, None, None])
        self.model_table.set_rows(rows, colors=colors)
        current = self.model_choice.currentData()
        self.model_choice.blockSignals(True)
        self.model_choice.clear()
        for entry in catalog:
            label = entry.name + (" (installed)" if entry.installed else "")
            self.model_choice.addItem(label, entry.name)
        if current:
            index = self.model_choice.findData(current)
            if index >= 0:
                self.model_choice.setCurrentIndex(index)
        self.model_choice.blockSignals(False)
