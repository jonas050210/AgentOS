"""Training: headless runs, datasets, evaluation, promotion and the real hardware budget."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from codemind.events import Topics
from codemind.ui import theme as T
from codemind.ui.views.base import SectionHeader, View
from codemind.ui.widgets.glass import (
    ActionButton,
    BarMeter,
    Card,
    EmptyState,
    KeyValue,
    Pill,
    Sparkline,
    StatTile,
    hline,
)
from codemind.ui.widgets.logview import LogView
from codemind.ui.widgets.table import DataTable, LineChart
from codemind.utils.files import human_bytes, human_duration
from codemind.utils.textutil import shorten


class TrainingView(View):
    title = "Training"
    subtitle = "Fine-tuning runs headlessly in the background; this window only monitors and controls them"

    def build(self) -> None:
        self.current_run = ""
        # --- environment ---------------------------------------------------
        env_card = Card("What this machine can do", "Availability is probed from the real Python environment and GPU, never assumed")
        self.env_grid = QGridLayout()
        self.env_grid.setHorizontalSpacing(T.GAP)
        self.env_grid.setVerticalSpacing(T.GAP)
        env_card.content.addLayout(self.env_grid)
        self.env_note = QLabel("")
        self.env_note.setWordWrap(True)
        self.env_note.setFont(T.font(10))
        self.env_note.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        env_card.content.addWidget(self.env_note)
        self.methods_layout = QVBoxLayout()
        self.methods_layout.setSpacing(6)
        env_card.content.addLayout(self.methods_layout)
        self.add(env_card)

        # --- start a run ---------------------------------------------------
        start_card = Card("Start a run", "Everything runs locally; nothing is uploaded")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.run_name = QLineEdit()
        self.run_name.setPlaceholderText("name (optional)")
        self.run_method = QComboBox()
        self.run_dataset = QComboBox()
        self.run_base = QLineEdit()
        self.run_base.setPlaceholderText("base model path or HF id (LoRA/QLoRA only)")
        self.run_epochs = QSpinBox()
        self.run_epochs.setRange(1, 50)
        self.run_epochs.setValue(max(1, min(50, int(self.app.config.training.epochs or 1))))
        self.run_epochs.setMinimumWidth(84)
        # the value shown must be the value that will be used: it starts from the stored setting
        # and is bounded by the same limits the config validates against
        self.run_lr = QDoubleSpinBox()
        self.run_lr.setRange(1e-7, 0.01)
        self.run_lr.setDecimals(7)
        self.run_lr.setSingleStep(1e-5)
        self.run_lr.setValue(float(self.app.config.training.learning_rate))
        self.run_lr.setMinimumWidth(126)
        self.run_lr.setToolTip("Learning rate used for a fresh run; leave at the stored setting to use your defaults.")
        for widget in (self.run_name, self.run_base):
            widget.setStyleSheet(self._input_style())
            widget.setFont(T.font(11, mono=True))
        for widget in (self.run_method, self.run_dataset):
            widget.setStyleSheet(self._style_combo())
            widget.setFont(T.font(11))
        for widget in (self.run_epochs, self.run_lr):
            widget.setFont(T.font(11, mono=True))
            widget.setStyleSheet(self._input_style())
        labels = ["Name", "Method", "Dataset", "Base model", "Epochs", "Learning rate"]
        widgets = [self.run_name, self.run_method, self.run_dataset, self.run_base, self.run_epochs, self.run_lr]
        for index, (label, widget) in enumerate(zip(labels, widgets)):
            caption = QLabel(label)
            caption.setFont(T.font(10, QFont.DemiBold))
            caption.setStyleSheet(f"color: {T.TEXT_FAINT}; background: transparent;")
            form.addWidget(caption, 0, index)
            form.addWidget(widget, 1, index)
            # the text fields take the spare width; the spin boxes keep the room they need
            form.setColumnStretch(index, 0 if isinstance(widget, (QSpinBox, QDoubleSpinBox)) else 1)
        start_card.content.addLayout(form)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for label, tone, callback in (
            ("Start headless run", "accent", self.start_run),
            ("Cancel", "danger", lambda: self._control("cancel")),
            ("Pause", "warning", lambda: self._control("pause")),
            ("Resume", "neutral", lambda: self._control("resume")),
            ("Evaluate", "neutral", lambda: self._control("evaluate")),
            ("Promote", "success", self._promote),
            ("Reject", "danger", self._reject),
        ):
            buttons.addWidget(ActionButton(label, tone=tone, on_click=callback))
        buttons.addStretch(1)
        self.start_hint = QLabel("")
        self.start_hint.setFont(T.font(10))
        self.start_hint.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        buttons.addWidget(self.start_hint)
        start_card.content.addLayout(buttons)
        self.add(start_card)

        # --- active run ----------------------------------------------------
        self.run_card = Card("Active run", "Live progress, throughput and resource use")
        tiles = QHBoxLayout()
        tiles.setSpacing(T.GAP)
        self.run_tiles = [
            StatTile("Phase", "—", sub="", accent=T.ACCENT, icon="◆"),
            StatTile("Step", "0", sub="", accent=T.VIOLET, icon="◇"),
            StatTile("Loss", "—", sub="", accent=T.WARNING, icon="◷"),
            StatTile("Throughput", "—", sub="", accent=T.INFO, icon="⇅"),
            StatTile("Elapsed", "—", sub="", accent=T.SUCCESS, icon="⏱"),
        ]
        for tile in self.run_tiles:
            tiles.addWidget(tile, 1)
        holder = QWidget()
        holder.setLayout(tiles)
        holder.setStyleSheet("background: transparent;")
        self.run_card.content.addWidget(holder)
        self.run_progress = BarMeter("Progress", color_value=T.ACCENT)
        self.run_card.content.addWidget(self.run_progress)
        self.loss_chart = LineChart(height=150)
        self.run_card.content.addWidget(self.loss_chart)
        self.resource_row = QHBoxLayout()
        self.resource_row.setSpacing(T.GAP)
        self.cpu_meter = BarMeter("CPU (this machine)", color_value=T.ACCENT)
        self.ram_meter = BarMeter("Memory", color_value=T.VIOLET)
        self.vram_meter = BarMeter("VRAM", color_value=T.SUCCESS)
        for meter in (self.cpu_meter, self.ram_meter, self.vram_meter):
            self.resource_row.addWidget(meter, 1)
        self.run_card.content.addLayout(self.resource_row)
        self.run_log = LogView(max_lines=800)
        self.run_log.setMinimumHeight(120)
        self.run_card.content.addWidget(self.run_log)
        self.add(self.run_card)

        # --- datasets ------------------------------------------------------
        self.datasets_card = Card("Datasets", "Curated instruction data: validated, split, de-duplicated, secrets redacted")
        self.dataset_table = DataTable(["Name", "Examples", "Rejected", "Size", "Updated", "Path"], stretch_column=0)
        self.dataset_table.set_mono_columns([5])
        self.dataset_table.setMinimumHeight(150)
        self.datasets_card.content.addWidget(self.dataset_table)
        dataset_buttons = QHBoxLayout()
        for label, callback in (
            ("Install starter set", self._install_seed),
            ("Import file…", self._import_dataset),
            ("Collect from finished tasks", self._collect_traces),
            ("Export…", self._export_dataset),
            ("Delete", self._delete_dataset),
        ):
            dataset_buttons.addWidget(ActionButton(label, on_click=callback))
        dataset_buttons.addStretch(1)
        self.dataset_hint = QLabel("")
        self.dataset_hint.setFont(T.font(10))
        self.dataset_hint.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        dataset_buttons.addWidget(self.dataset_hint)
        self.datasets_card.content.addLayout(dataset_buttons)
        self.add(self.datasets_card)

        # --- runs ----------------------------------------------------------
        self.runs_card = Card("Runs", "Every run is reproducible: config, dataset and artefacts are stored with it")
        self.runs_table = DataTable(["Run", "Name", "Method", "Status", "Loss", "Accuracy", "Steps", "Started"], stretch_column=1)
        self.runs_table.set_mono_columns([0])
        self.runs_table.setMinimumHeight(180)
        self.runs_table.itemSelectionChanged.connect(self._select_run)
        self.runs_card.content.addWidget(self.runs_table)
        self.add(self.runs_card)

        # --- evaluation ----------------------------------------------------
        self.eval_card = Card("Evaluation & promotion", "A candidate is promoted only when a held-out suite measures a real improvement")
        self.eval_table = DataTable(["When", "Run", "Suite", "Baseline", "Candidate", "Decision", "Reason"], stretch_column=6)
        self.eval_table.setMinimumHeight(140)
        self.eval_card.content.addWidget(self.eval_table)
        self.comparison_body = QVBoxLayout()
        self.comparison_body.setSpacing(5)
        self.eval_card.content.addLayout(self.comparison_body)
        self.add(self.eval_card)

        # --- versions ------------------------------------------------------
        self.versions_card = Card("Model versions", "The promoted adapter is loaded into the planner and changes tool choice")
        self.versions_table = DataTable(["Model", "Version", "Status", "Kind", "Accuracy", "Created", "Path"], stretch_column=6)
        self.versions_table.set_mono_columns([1, 6])
        self.versions_table.setMinimumHeight(120)
        self.versions_card.content.addWidget(self.versions_table)
        self.add(self.versions_card)

    # -- styles -----------------------------------------------------------
    @staticmethod
    def _input_style() -> str:
        return (
            f"QLineEdit, QSpinBox, QDoubleSpinBox {{ background: rgba(0,0,0,0.32); color: {T.TEXT};"
            f" border: 1px solid {T.HAIRLINE}; border-radius: 8px; padding: 4px 8px; }}"
        )

    @staticmethod
    def _style_combo() -> str:
        return (
            f"QComboBox {{ background: rgba(0,0,0,0.32); color: {T.TEXT}; border: 1px solid {T.HAIRLINE};"
            f" border-radius: 8px; padding: 4px 8px; }}"
            f"QComboBox QAbstractItemView {{ background: {T.SURFACE}; color: {T.TEXT}; }}"
        )

    # -- actions ----------------------------------------------------------
    def start_run(self) -> None:
        method = self.run_method.currentData() or "auto"
        dataset = self.run_dataset.currentData() or ""
        overrides: dict[str, Any] = {}
        default_epochs = max(1, min(50, int(self.app.config.training.epochs or 1)))
        default_lr = float(self.app.config.training.learning_rate)
        if self.run_epochs.value() != default_epochs:
            overrides["epochs"] = self.run_epochs.value()
        if abs(self.run_lr.value() - default_lr) > 1e-9:
            overrides["learning_rate"] = self.run_lr.value()
        try:
            run = self.app.training.start_run(
                name=self.run_name.text().strip(),
                method=method,
                dataset=dataset,
                base_model=self.run_base.text().strip(),
                overrides=overrides or None,
            )
        except Exception as exc:
            self.start_hint.setText(f"refused: {exc}")
            return
        self.current_run = run["id"]
        self.run_log.clear_text()
        self.start_hint.setText(f"started {run['id']} ({run['method']})")
        self.refresh()

    def _control(self, action: str) -> None:
        run_id = self._run_id()
        if not run_id:
            self.start_hint.setText("select a run first")
            return
        try:
            training = self.app.training
            if action == "cancel":
                ok = training.cancel_run(run_id)
                self.start_hint.setText("cancelled" if ok else "the run is not active")
            elif action == "pause":
                ok = training.cancel_run(run_id, pause=True)
                self.start_hint.setText("paused at the last checkpoint" if ok else "the run is not active")
            elif action == "resume":
                training.resume_run(run_id)
                self.start_hint.setText(f"resumed {run_id}")
            elif action == "evaluate":
                result = training.evaluate_run(run_id)
                self.start_hint.setText(f"evaluated: {result.get('decision', {}).get('reason', 'done')}")
        except Exception as exc:
            self.start_hint.setText(f"failed: {exc}")
        self.refresh()

    def _promote(self) -> None:
        run_id = self._run_id()
        if not run_id:
            return
        note, ok = QInputDialog.getText(self, "Promote run", "Note for the record (why this candidate wins):")
        if not ok:
            return
        try:
            result = self.app.training.promote(run_id, note=note)
            self.start_hint.setText(f"promoted {result['version']} to {result['status']}")
        except Exception as exc:
            self.start_hint.setText(f"refused: {exc}")
        self.refresh()

    def _reject(self) -> None:
        run_id = self._run_id()
        if not run_id:
            return
        reason, ok = QInputDialog.getText(self, "Reject run", "Why is this candidate not promoted?")
        if not ok:
            return
        try:
            self.app.training.reject(run_id, reason=reason)
            self.start_hint.setText("rejected")
        except Exception as exc:
            self.start_hint.setText(f"failed: {exc}")
        self.refresh()

    def _run_id(self) -> str:
        if self.current_run:
            return self.current_run
        row = self.runs_table.current_row_data()
        return row[0] if row else ""

    def _select_run(self) -> None:
        row = self.runs_table.current_row_data()
        if row:
            self.current_run = row[0]
            self.run_log.clear_text()
            for line in (self.app.training.engine.events(self.current_run, limit=120) or []):
                self.run_log.append_line(f"{time.strftime('%H:%M:%S', time.localtime(line.get('ts') or 0))} {line.get('kind')}: {shorten(str(line.get('payload')), 240)}")

    def _install_seed(self) -> None:
        try:
            result = self.app.training.install_seed_dataset()
            self.dataset_hint.setText(f"starter set: {result.get('examples')} train / {result.get('validation_examples')} validation")
        except Exception as exc:
            self.dataset_hint.setText(f"failed: {exc}")
        self.refresh()

    def _import_dataset(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        paths, _ = QFileDialog.getOpenFileNames(self, "Import training data", str(self.app.data_root), "JSONL/JSON/CSV (*.jsonl *.json *.csv)")
        if not paths:
            return
        name, ok = QInputDialog.getText(self, "Dataset name", "Store these files in a managed dataset named:", text="imported")
        if not ok or not name.strip():
            return
        try:
            result = self.app.training.import_dataset(name.strip(), paths)
            report = result.get("report", {})
            self.dataset_hint.setText(
                f"{result.get('examples')} train + {result.get('validation_examples')} validation "
                f"({report.get('valid', 0)} valid, {report.get('rejected', 0)} rejected, {report.get('duplicates', 0)} duplicate)"
            )
        except Exception as exc:
            self.dataset_hint.setText(f"failed: {exc}")
        self.refresh()

    def _export_dataset(self) -> None:
        name = self._dataset_name()
        if not name:
            return
        from PySide6.QtWidgets import QFileDialog

        target, _ = QFileDialog.getSaveFileName(self, "Export dataset", str(self.app.data_root / f"{name}.jsonl"), "JSONL (*.jsonl)")
        if not target:
            return
        try:
            result = self.app.training.export_dataset(name, target)
            self.dataset_hint.setText(f"exported {result.get('examples')} example(s) to {target}")
        except Exception as exc:
            self.dataset_hint.setText(f"failed: {exc}")

    def _delete_dataset(self) -> None:
        name = self._dataset_name()
        if not name:
            return
        try:
            self.app.training.delete_dataset(name)
            self.dataset_hint.setText(f"deleted dataset {name}")
        except Exception as exc:
            self.dataset_hint.setText(f"failed: {exc}")
        self.refresh()

    def _dataset_name(self) -> str:
        row = self.dataset_table.current_row_data()
        return row[0] if row else ""

    def _collect_traces(self) -> None:
        try:
            result = self.app.training.collect_from_tasks(limit=200)
            self.dataset_hint.setText(f"collected {result.get('accepted', 0)} example(s) from {result.get('tasks', 0)} finished task(s)")
        except Exception as exc:
            self.dataset_hint.setText(f"failed: {exc}")
        self.refresh()

    # -- events -----------------------------------------------------------
    def on_event(self, topic: str, payload: dict[str, Any]) -> None:
        if topic == Topics.TRAINING_PROGRESS and payload.get("run_id") == self.current_run:
            if payload.get("line"):
                self.run_log.append_line(str(payload["line"]))
            elif payload.get("event"):
                self.run_log.append_line(f"· {payload.get('event')} {shorten(str(payload), 200)}")

    # -- refresh ----------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_environment()
        self._refresh_datasets()
        self._refresh_runs()
        self._refresh_active()
        self._refresh_evaluations()
        self._refresh_versions()

    def _refresh_environment(self) -> None:
        env = self.app.training.environment()
        toolchain = env["toolchain"]
        hardware = env["hardware"]
        while self.env_grid.count():
            item = self.env_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        tiles = [
            StatTile("Tier", hardware.get("tier", "—"), sub=shorten(hardware.get("cpu_model", ""), 26), accent=T.ACCENT, icon="▮"),
            StatTile("Accelerator", hardware.get("gpu_summary", "no GPU"), sub=f"{hardware.get('ram_total_mb', 0) / 1024:.1f} GB RAM", accent=T.SUCCESS if hardware.get("gpus") else T.TEXT_FAINT, icon="▥"),
            StatTile("PyTorch", toolchain.get("torch") or "not installed", sub=f"transformers {toolchain.get('transformers') or '—'}", accent=T.VIOLET if toolchain.get("torch") else T.TEXT_FAINT, icon="⚙"),
            StatTile("PEFT", toolchain.get("peft") or "not installed", sub=f"bitsandbytes {toolchain.get('bitsandbytes') or '—'}", accent=T.VIOLET if toolchain.get("peft") else T.TEXT_FAINT, icon="⚙"),
            StatTile("Python", toolchain.get("python", "—"), sub=f"auto method: {env['auto_method']}", accent=T.INFO, icon="◆"),
        ]
        for index, tile in enumerate(tiles):
            self.env_grid.addWidget(tile, 0, index)
        plan = env.get("training_plan") or {}
        self.env_note.setText(
            f"Hardware tier '{hardware.get('tier')}' → recommended model {hardware.get('recommended_model', '—')}. "
            f"{plan.get('summary') or plan.get('reason') or ''} "
            f"Examples are only collected from verified tasks (curated: {env['curated_only']}, quality gate {env['quality_threshold']})."
        )
        while self.methods_layout.count():
            item = self.methods_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        for method in self.app.training.methods():
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            available = method["available"]
            layout.addWidget(Pill("available" if available else "unavailable", color_value=T.SUCCESS if available else T.TEXT_FAINT))
            name = QLabel(method["title"])
            name.setFont(T.font(11, QFont.DemiBold))
            name.setStyleSheet(f"color: {T.TEXT if available else T.TEXT_SOFT}; background: transparent;")
            name.setMinimumWidth(150)
            layout.addWidget(name)
            detail = QLabel(method.get("description", ""))
            detail.setFont(T.font(10))
            detail.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
            detail.setWordWrap(True)
            layout.addWidget(detail, 1)
            if not available and method.get("install"):
                hint = QLabel(method["install"])
                hint.setFont(T.font(10, mono=True))
                hint.setStyleSheet(f"color: {T.WARNING}; background: transparent;")
                hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
                layout.addWidget(hint)
            methods_holder = QWidget()
            methods_holder.setLayout(layout) if layout.parent() is None else None
            self.methods_layout.addWidget(row)
        # method + dataset choices
        current_method = self.run_method.currentData()
        self.run_method.blockSignals(True)
        self.run_method.clear()
        self.run_method.addItem("auto (recommended)", "auto")
        for method in self.app.training.methods():
            if method["available"]:
                self.run_method.addItem(method["title"], method["name"])
        if current_method:
            index = self.run_method.findData(current_method)
            if index >= 0:
                self.run_method.setCurrentIndex(index)
        self.run_method.blockSignals(False)
        stats = self.app.training.dataset_stats()
        items = stats.get("items") or []
        current_dataset = self.run_dataset.currentData()
        self.run_dataset.clear()
        for item in items:
            self.run_dataset.addItem(f"{item['name']} ({item.get('examples', 0)})", item["name"])
        if current_dataset:
            index = self.run_dataset.findData(current_dataset)
            if index >= 0:
                self.run_dataset.setCurrentIndex(index)

    def _refresh_datasets(self) -> None:
        stats = self.app.training.dataset_stats()
        self.dataset_table.set_rows(
            [
                [
                    item["name"],
                    item.get("examples", 0),
                    item.get("rejected", 0),
                    human_bytes(item.get("bytes") or 0),
                    time.strftime("%m-%d %H:%M", time.localtime(item.get("updated_at") or time.time())),
                    item.get("path", ""),
                ]
                for item in stats.get("items", [])
            ]
        )
        collector = stats.get("collector") or {}
        self.datasets_card.set_subtitle(
            f"Collected from tasks: {collector.get('examples', 0)} example(s) in {collector.get('dataset', '—')} · "
            f"quality gate {collector.get('quality_threshold', 0)} · verified only: {collector.get('curated_only')}"
        )

    def _refresh_runs(self) -> None:
        rows = []
        colors = []
        for run in self.app.training.runs(limit=40):
            metrics = run.get("metrics") or {}
            rows.append(
                [
                    run["id"],
                    run["name"],
                    run["method"],
                    run["status"],
                    metrics.get("final_loss", "—"),
                    metrics.get("accuracy", "—"),
                    metrics.get("steps", "—"),
                    time.strftime("%m-%d %H:%M", time.localtime(run.get("created_at") or time.time())),
                ]
            )
            colors.append([None, None, None, T.STATUS_COLORS.get(run["status"], T.TEXT_SOFT), None, None, None, None])
        self.runs_table.set_rows(rows, colors=colors)

    def _refresh_active(self) -> None:
        active = self.app.training.engine.active()
        run = None
        if active:
            run = active[0]
            self.current_run = run.id
        elif self.current_run:
            run = self.app.training.engine.get(self.current_run)
        if run is None:
            self.run_card.set_subtitle("No run selected — start one above, or pick one from the runs table")
        else:
            self.run_card.set_subtitle(f"{run.name} · {run.method} · {run.status} · dataset {run.dataset.split('/')[-1]}")
        progress = run.progress if run else {}
        metrics = run.metrics if run else {}
        phase = progress.get("phase") or run.status if run else "—"
        self.run_tiles[0].set_value(str(phase), sub=shorten(str(progress.get("message") or ""), 30))
        self.run_tiles[1].set_value(
            f"{progress.get('step', metrics.get('steps', 0))}",
            sub=f"of {progress.get('total_steps') or '—'} · epoch {progress.get('epoch', 0)}",
        )
        loss = progress.get("loss", metrics.get("final_loss"))
        self.run_tiles[2].set_value(f"{loss:.4f}" if isinstance(loss, (int, float)) else "—", sub=f"val loss {progress.get('val_loss', metrics.get('val_loss', '—'))}")
        self.run_tiles[3].set_value(
            f"{progress.get('throughput', 0):.0f}/s" if progress.get("throughput") else "—",
            sub=f"{metrics.get('trainable_params', '—')} trainable params",
        )
        sub = ""
        if run is not None and run.started_at:
            sub = time.strftime("started %H:%M:%S", time.localtime(run.started_at))
        self.run_tiles[4].set_value(human_duration(progress.get("elapsed") or 0), sub=sub)
        fraction = float(progress.get("percent") or 0) / 100.0
        self.run_progress.set_value(fraction, text=f"{fraction * 100:.1f}% · step {progress.get('step', 0)}")
        history = (metrics.get("history") or [])[-400:]
        self.loss_chart.set_series(
            {
                "train loss": ([item.get("loss", 0.0) for item in history], T.WARNING),
                "val loss": ([item.get("val_loss", item.get("loss", 0.0)) for item in history], T.ACCENT),
                "accuracy": ([item.get("accuracy", 0.0) for item in history], T.SUCCESS),
            }
        )
        resources = self.app.sampler.latest
        self.cpu_meter.set_value((resources.cpu_percent or 0) / 100.0, text=f"{resources.cpu_percent:.0f}%", color_value=T.level_color(resources.cpu_percent or 0, inverted=True))
        ram_fraction = (resources.ram_used_mb / resources.ram_total_mb) if resources.ram_total_mb else 0.0
        self.ram_meter.set_value(ram_fraction, text=f"{resources.ram_used_mb / 1024:.1f}/{resources.ram_total_mb / 1024:.1f} GB")
        if resources.vram_total_mb:
            vram_fraction = resources.vram_used_mb / resources.vram_total_mb
            self.vram_meter.set_value(vram_fraction, text=f"{resources.vram_used_mb / 1024:.1f}/{resources.vram_total_mb / 1024:.1f} GB", color_value=T.level_color(vram_fraction * 100))
        else:
            self.vram_meter.set_value(0.0, text="no CUDA GPU", color_value=T.TEXT_FAINT)

    def _refresh_evaluations(self) -> None:
        rows = []
        colors = []
        evaluations = self.app.training.evaluations(limit=40)
        for entry in evaluations:
            decision = entry.get("decision") or ""
            rows.append(
                [
                    time.strftime("%m-%d %H:%M", time.localtime(entry.get("created_at") or time.time())),
                    entry.get("run_id", ""),
                    entry.get("suite", ""),
                    f"{entry.get('baseline_score', 0):.4f}" if entry.get("baseline_score") is not None else "—",
                    f"{entry.get('score', 0):.4f}" if entry.get("score") is not None else "—",
                    decision,
                    shorten(entry.get("reason", ""), 80),
                ]
            )
            colors.append([None, None, None, None, None, T.SUCCESS if decision == "promote" else T.WARNING, None])
        self.eval_table.set_rows(rows, colors=colors)
        run_id = self._run_id()
        while self.comparison_body.count():
            item = self.comparison_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        comparison = self.app.training.comparison(run_id) if run_id else None
        if comparison:
            self.comparison_body.addWidget(hline())
            self.comparison_body.addWidget(KeyValue("Suite", comparison["suite"]))
            self.comparison_body.addWidget(
                KeyValue(
                    "Score",
                    f"baseline {comparison['baseline_score']:.4f} → candidate {comparison['candidate_score']:.4f}",
                    value_color=T.SUCCESS if (comparison.get("candidate_score") or 0) > (comparison.get("baseline_score") or 0) else T.WARNING,
                )
            )
            for metric in comparison.get("metrics", []):
                self.comparison_body.addWidget(
                    KeyValue(metric["metric"], f"{metric['baseline']} → {metric['candidate']} ({metric['delta']:+})", mono=True)
                )
            self.comparison_body.addWidget(KeyValue("Decision", shorten(comparison.get("reason", ""), 200)))

    def _refresh_versions(self) -> None:
        rows = []
        colors = []
        for version in self.app.training.versions():
            metrics = version.get("metrics") or {}
            rows.append(
                [
                    version.get("model_id", ""),
                    version.get("version", ""),
                    version.get("status", ""),
                    version.get("kind", ""),
                    metrics.get("accuracy", "—"),
                    time.strftime("%m-%d %H:%M", time.localtime(version.get("created_at") or time.time())),
                    version.get("path", ""),
                ]
            )
            colors.append([None, None, T.SUCCESS if version.get("status") == "active" else T.TEXT_SOFT, None, None, None, None])
        self.versions_table.set_rows(rows, colors=colors)
