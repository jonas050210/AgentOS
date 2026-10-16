"""Coding Agent: hand over a task, watch it plan, execute, diagnose and verify."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from codemind.events import Topics
from codemind.ui import theme as T
from codemind.ui.views.base import SectionHeader, View
from codemind.ui.widgets.glass import (
    ActionButton,
    Card,
    EmptyState,
    KeyValue,
    Pill,
    StatTile,
    StatusDot,
    hline,
)
from codemind.ui.widgets.logview import LogView, Timeline, Transcript
from codemind.ui.widgets.table import DataTable
from codemind.utils.files import human_duration
from codemind.utils.textutil import shorten


class AgentView(View):
    title = "Coding Agent"
    subtitle = "Tasks run in isolated workspaces; every change is diffed, tested and verified before it is reported"

    def build(self) -> None:
        self.current_task_id = ""
        # --- composer ------------------------------------------------------
        composer = Card("", padding=14)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Describe the task — e.g. \"the parser drops repeated keys, find out why and fix it with a test\"")
        self.prompt.setFixedHeight(74)
        self.prompt.setFont(T.font(12))
        self.prompt.setStyleSheet(
            f"QPlainTextEdit {{ background: rgba(0,0,0,0.30); color: {T.TEXT}; border: 1px solid {T.HAIRLINE};"
            f" border-radius: 10px; padding: 8px; }}"
            f"QPlainTextEdit:focus {{ border-color: {T.ACCENT}; }}"
        )
        self.project = QComboBox()
        self.project.setEditable(True)
        self.project.setMinimumWidth(280)
        self.project.setFont(T.font(11, mono=True))
        self.project.setStyleSheet(self._combo_style())
        self.project.setToolTip("Project directory the task works on. New tasks get their own clone unless in-place editing is configured.")
        self.mode = QComboBox()
        self.mode.setFont(T.font(11))
        self.mode.setStyleSheet(self._combo_style())
        for label, value in (("Assisted", "assisted"), ("Autonomous", "autonomous"), ("Plan only", "plan_only")):
            self.mode.addItem(label, value)
        self.mode.setToolTip("Assisted asks before anything irreversible. Autonomous only asks for destructive or credential-exposing actions.")

        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self.prompt)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(QLabel("Project"))
        controls.addWidget(self.project, 1)
        controls.addWidget(QLabel("Mode"))
        controls.addWidget(self.mode)
        controls.addWidget(ActionButton("Queue task", tone="accent", on_click=self.submit))
        controls.addWidget(ActionButton("Cancel task", tone="danger", on_click=self.cancel_current))
        controls.addWidget(ActionButton("Resume", on_click=self.resume_selected))
        controls.addWidget(ActionButton("Retry", on_click=self.retry_selected))
        left.addLayout(controls)
        row.addLayout(left, 1)
        composer.content.addLayout(row)
        self.add(composer)

        # --- tiles ---------------------------------------------------------
        tiles = QHBoxLayout()
        tiles.setSpacing(T.GAP)
        self.tile_state = StatTile("Agent", "idle", sub="no task running", accent=T.ACCENT, icon="◆")
        self.tile_task = StatTile("Current task", "—", sub="", accent=T.VIOLET, icon="◇")
        self.tile_tools = StatTile("Tool calls", "0", sub="", accent=T.INFO, icon="⚙")
        self.tile_time = StatTile("Elapsed", "0s", sub="", accent=T.WARNING, icon="◷")
        for tile in (self.tile_state, self.tile_task, self.tile_tools, self.tile_time):
            tiles.addWidget(tile, 1)
        holder = QWidget()
        holder.setLayout(tiles)
        holder.setStyleSheet("background: transparent;")
        self.add(holder)

        # --- main split ----------------------------------------------------
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setStyleSheet(f"QSplitter::handle {{ background: {T.HAIRLINE}; width: 1px; }}")

        plan_card = Card("Plan", "Decided before execution, updated when a repair is needed")
        self.timeline = Timeline()
        plan_scroll = QWidget()
        plan_scroll.setStyleSheet("background: transparent;")
        plan_layout = QVBoxLayout(plan_scroll)
        plan_layout.setContentsMargins(0, 0, 0, 0)
        plan_layout.addWidget(self.timeline)
        plan_card.content.addWidget(plan_scroll)
        self.plan_header = QHBoxLayout()
        plan_card.content.addLayout(self.plan_header)
        split.addWidget(plan_card)

        right = QSplitter(Qt.Vertical)
        right.setChildrenCollapsible(False)
        transcript_card = Card("Reasoning", "What the agent understood, decided and tried — verbatim")
        self.transcript = Transcript()
        self.transcript.setMinimumHeight(150)
        transcript_card.content.addWidget(self.transcript)
        right.addWidget(transcript_card)

        output_card = Card("Output", "Live terminal output from the sandbox")
        self.output = LogView()
        self.output.setMinimumHeight(120)
        output_card.content.addWidget(self.output)
        row2 = QHBoxLayout()
        self.output_filter = QLineEdit()
        self.output_filter.setPlaceholderText("filter output…")
        self.output_filter.setFont(T.font(10, mono=True))
        self.output_filter.setStyleSheet(self._input_style())
        row2.addWidget(self.output_filter, 1)
        row2.addWidget(ActionButton("Clear", on_click=self.output.clear_text))
        output_card.content.addLayout(row2)
        right.addWidget(output_card)

        diff_card = Card("Changes", "Every file the agent wrote, with the real diff")
        self.diff_table = DataTable(["File", "Added", "Removed", "At"], stretch_column=0)
        self.diff_table.set_mono_columns([0])
        self.diff_table.setMinimumHeight(110)
        self.diff_table.itemSelectionChanged.connect(self._show_selected_diff)
        diff_card.content.addWidget(self.diff_table)
        self.diff_text = LogView(max_lines=1200)
        self.diff_text.setMinimumHeight(90)
        diff_card.content.addWidget(self.diff_text)
        right.addWidget(diff_card)

        verification_card = Card("Verification", "A task is only reported complete when these checks pass on real evidence")
        self.verification_body = QVBoxLayout()
        self.verification_body.setSpacing(5)
        verification_card.content.addLayout(self.verification_body)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.summary_label.setFont(T.font(11))
        self.summary_label.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        verification_card.content.addWidget(self.summary_label)
        right.addWidget(verification_card)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 4)
        split.setSizes([460, 780])
        right.setSizes([300, 300, 260, 200])
        holder_split = QWidget()
        holder_split.setStyleSheet("background: transparent;")
        holder_layout = QVBoxLayout(holder_split)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addWidget(split)
        self.add(holder_split)

        # --- approvals -----------------------------------------------------
        self.approval_card = Card("Approvals", "Only destructive, credential-exposing or irreversible external actions wait here")
        self.approval_body = QVBoxLayout()
        self.approval_body.setSpacing(6)
        self.approval_card.content.addLayout(self.approval_body)
        self.add(self.approval_card)

        # --- history -------------------------------------------------------
        self.history_card = Card("Task history", "Everything CodeMind has run, newest first")
        self.history_table = DataTable(["When", "Task", "Project", "Status", "Tools", "Taken"], stretch_column=1)
        self.history_table.set_mono_columns([2])
        self.history_table.setMinimumHeight(200)
        self.history_table.itemSelectionChanged.connect(self._select_history_row)
        self.history_card.content.addWidget(self.history_table)
        buttons = QHBoxLayout()
        for label, callback in (
            ("Open selected", self._open_selected),
            ("Retry selected", self.retry_selected),
            ("Cancel selected", self.cancel_selected),
            ("Delete selected", self._delete_selected),
        ):
            buttons.addWidget(ActionButton(label, on_click=callback))
        buttons.addStretch(1)
        self.approval_label = QLabel("")
        self.approval_label.setFont(T.font(10))
        self.approval_label.setStyleSheet(f"color: {T.WARNING}; background: transparent;")
        buttons.addWidget(self.approval_label)
        self.history_card.content.addLayout(buttons)
        self.add(self.history_card)

        self._selection: str = ""

    # -- styles -----------------------------------------------------------
    @staticmethod
    def _combo_style() -> str:
        return (
            f"QComboBox {{ background: rgba(0,0,0,0.32); color: {T.TEXT}; border: 1px solid {T.HAIRLINE};"
            f" border-radius: 8px; padding: 4px 8px; }}"
            f"QComboBox QAbstractItemView {{ background: {T.SURFACE}; color: {T.TEXT}; selection-background-color: rgba(110,168,254,0.25); }}"
        )

    @staticmethod
    def _input_style() -> str:
        return (
            f"QLineEdit {{ background: rgba(0,0,0,0.32); color: {T.TEXT}; border: 1px solid {T.HAIRLINE};"
            f" border-radius: 8px; padding: 4px 8px; }} QLineEdit:focus {{ border-color: {T.ACCENT}; }}"
        )

    # -- actions ----------------------------------------------------------
    def submit(self) -> None:
        prompt = self.prompt.toPlainText().strip()
        if not prompt:
            self.transcript.add("system", "Write what you want done first — an empty task cannot be planned.")
            return
        project = self.project.currentText().strip()
        mode = self.mode.currentData()
        try:
            task = self.app.submit_task(prompt, project_path=project, mode=mode or "assisted", title=shorten(prompt, 70))
        except Exception as exc:
            self.transcript.add("error", f"Could not queue the task: {exc}")
            return
        self.current_task_id = task.id
        self.prompt.clear()
        self.transcript.clear_transcript()
        self.transcript.add("user", prompt, meta=time.strftime("%H:%M:%S"))
        self.transcript.add("agent", f"Queued as task {task.id}. Planning starts as soon as a worker is free.")
        self.output.clear_text()
        self.diff_text.clear_text()
        self.refresh()

    def cancel_current(self) -> None:
        self._cancel(self.current_task_id or self._selection)

    def cancel_selected(self) -> None:
        self._cancel(self._selection)

    def _cancel(self, task_id: str) -> None:
        if not task_id:
            return
        try:
            ok = self.app.runner.cancel(task_id)
            self.transcript.add("system", f"Cancel requested for {task_id}: {'accepted' if ok else 'task was not running'}")
        except Exception as exc:
            self.transcript.add("error", f"Cancel failed: {exc}")

    def resume_selected(self) -> None:
        task_id = self._selection or self.current_task_id
        if not task_id:
            return
        try:
            ok = self.app.runner.resume(task_id)
            self.transcript.add("system", f"Resuming {task_id}: {'re-queued' if ok else 'this task cannot be resumed from its current status'}")
            self.current_task_id = task_id
        except Exception as exc:
            self.transcript.add("error", f"Resume failed: {exc}")

    def retry_selected(self) -> None:
        task_id = self._selection
        if not task_id:
            return
        try:
            ok = self.app.runner.retry(task_id)
            self.transcript.clear_transcript()
            if ok:
                self.transcript.add("system", f"Retrying {task_id} from a clean plan. Previous steps and diffs stay in history.")
                self.current_task_id = task_id
            else:
                self.transcript.add("error", f"Task {task_id} could not be retried.")
        except Exception as exc:
            self.transcript.add("error", f"Retry failed: {exc}")

    def _open_selected(self) -> None:
        if self._selection:
            self.current_task_id = self._selection
            self.refresh()

    def _delete_selected(self) -> None:
        if not self._selection:
            return
        try:
            self.app.tasks.delete(self._selection)
            self.transcript.add("system", f"Deleted task {self._selection} from history.")
            if self._selection == self.current_task_id:
                self.current_task_id = ""
            self._selection = ""
        except Exception as exc:
            self.transcript.add("error", f"Delete failed: {exc}")

    def _select_history_row(self) -> None:
        data = self.history_table.current_row_data()
        if not data:
            return
        task_id = data[0]
        self._selection = task_id

    # -- events -----------------------------------------------------------
    def on_event(self, topic: str, payload: dict[str, Any]) -> None:
        task_id = payload.get("task_id") or payload.get("id") or ""
        if topic == Topics.TASK_CREATED and task_id:
            self.transcript.add("agent", f"Task {task_id} queued.")
            return
        if not task_id or task_id != self.current_task_id:
            return
        if topic == Topics.TASK_PLAN:
            steps = payload.get("steps") or []
            self.transcript.add("agent", f"Plan ready: {len(steps)} step(s). " + "; ".join(shorten(str(step.get('title', '')), 40) for step in steps[:4]))
        elif topic == Topics.TASK_STEP:
            step = payload.get("step") or payload
            status = step.get("status")
            if status == "running":
                self.transcript.add("tool", f"{step.get('title')} → {step.get('tool') or '—'}\n{_format_args(step.get('arguments'))}", meta=str(step.get("idx", "")))
            elif status in ("failed", "done") and step.get("result"):
                summary = (step.get("result") or {}).get("summary") or ""
                role = "error" if status == "failed" else "tool"
                self.transcript.add(role, f"{step.get('title')}: {shorten(summary, 600)}")
        elif topic in (Topics.TOOL_CALL,):
            name = payload.get("tool") or payload.get("name") or ""
            ok = payload.get("ok")
            self.output.append_line(f"[{'ok ' if ok else 'err'}] {name} {_format_args(payload.get('arguments'))}")
        elif topic == Topics.SANDBOX_EXEC and payload.get("command"):
            stream = payload.get("stream")
            line = payload.get("line")
            if line:
                self.output.stream(str(line), "stderr" if stream == "stderr" else "stdout")
        elif topic == Topics.TASK_DIFF:
            path = payload.get("path")
            if path:
                self.diff_text.append_line(f"--- {path}\n{payload.get('diff', '')}")
        elif topic == Topics.TASK_LOG:
            message = payload.get("message") or ""
            kind = payload.get("kind") or "log"
            if kind in ("diagnosis", "repair", "lesson", "needs_model", "limit", "repeat"):
                self.transcript.add("error" if kind in ("needs_model", "limit", "repeat") else "agent", f"[{kind}] {message}")
        elif topic == Topics.TASK_FINISHED:
            status = payload.get("status")
            summary = payload.get("summary") or ""
            self.transcript.add("agent" if status == "completed" else "error", f"Finished: {status}\n{shorten(summary, 1200)}")

    # -- refresh ----------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_projects()
        self._refresh_tiles()
        task_id = self.current_task_id
        if task_id:
            task = self.app.tasks.get(task_id)
            if task is not None:
                self.timeline.set_steps(task.steps)
                self._refresh_verification(task)
                self._refresh_diffs(task_id)
        else:
            if self.timeline._layout.count() == 0:
                self.timeline.set_steps([])
        self._refresh_history()
        self._refresh_approvals()

    def _refresh_projects(self) -> None:
        projects = self.app.knowledge.projects(limit=40)
        current = self.project.currentText()
        known = [project.get("path", "") for project in projects]
        extra = [path for path in (self.app.config.storage.projects_root, "/tmp/sampleproj") if path and path not in known]
        self.project.blockSignals(True)
        self.project.clear()
        for path in known + extra:
            if path:
                self.project.addItem(path)
        if current:
            self.project.setEditText(current)
        self.project.blockSignals(False)

    def _refresh_tiles(self) -> None:
        status = self.app.runner.status()
        active = status.active
        if active:
            self.tile_state.set_value(f"{len(active)} running", sub=f"{status.queued} queued · {status.workers} workers", accent=T.SUCCESS)
        elif status.queued:
            self.tile_state.set_value("queued", sub=f"{status.queued} waiting for a worker", accent=T.ACCENT)
        else:
            self.tile_state.set_value("idle", sub="ready for a task", accent=T.TEXT_SOFT)
        task = self.app.tasks.get(self.current_task_id) if self.current_task_id else None
        if task is None:
            self.tile_task.set_value("—", sub="select a task from history")
            self.tile_tools.set_value("0", sub="")
            self.tile_time.set_value("0s", sub="")
            return
        self.tile_task.set_value(task.status, sub=shorten(task.title or task.prompt, 40), accent=T.STATUS_COLORS.get(task.status, T.TEXT))
        self.tile_tools.set_value(str(task.tool_calls), sub=f"budget {self.app.config.security.max_tool_calls_per_task}")
        started = task.started_at or task.created_at
        self.tile_time.set_value(human_duration((task.finished_at or time.time()) - started if started else 0), sub=task.mode)

    def _refresh_verification(self, task) -> None:
        while self.verification_body.count():
            item = self.verification_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        verify = task.verify or {}
        if verify.get("note"):
            self.verification_body.addWidget(KeyValue("Result", verify["note"], value_color=T.SUCCESS if verify.get("verified") else T.WARNING))
        for check in verify.get("checks") or []:
            self.verification_body.addWidget(
                KeyValue(
                    check.get("name", "check"),
                    shorten(check.get("detail", ""), 160),
                    value_color=T.SUCCESS if check.get("ok") else T.DANGER,
                )
            )
        baseline = verify.get("baseline") or {}
        if baseline:
            tests = baseline.get("tests") or {}
            self.verification_body.addWidget(
                KeyValue("Baseline", f"{baseline.get('command', '—')} · {tests.get('summary', 'no parseable result')}", mono=True)
            )
        self.summary_label.setText(task.summary or "")
        self.summary_label.setVisible(bool(task.summary))

    def _refresh_diffs(self, task_id: str) -> None:
        diffs = self.app.tasks.diffs(task_id)
        if not diffs:
            self.diff_table.set_rows([])
            return
        rows = []
        for entry in diffs:
            rows.append([entry.get("path", ""), entry.get("added", 0), entry.get("removed", 0), time.strftime("%H:%M:%S", time.localtime(entry.get("ts") or time.time()))])
        self.diff_table.set_rows(rows)
        self._diffs = diffs

    def _show_selected_diff(self) -> None:
        row = self.diff_table.currentRow()
        diffs = getattr(self, "_diffs", [])
        if row < 0 or row >= len(diffs):
            return
        self.diff_text.clear_text()
        self.diff_text.append_line(diffs[row].get("diff", "(no diff recorded)"))

    def _refresh_approvals(self) -> None:
        pending = self.app.approvals.pending()
        self.approval_label.setText(f"{len(pending)} approval(s) waiting" if pending else "")
        self.approval_card.setVisible(bool(pending))
        if not pending:
            return
        while self.approval_body.count():
            item = self.approval_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        for request in pending[:6]:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            risk = getattr(request, "risk", "") or "unknown"
            layout.addWidget(Pill(str(risk), color_value=T.DANGER if str(risk) in ("destructive", "credential") else T.WARNING))
            summary = getattr(request, "detail", "") or f"{request.tool} {_format_args(getattr(request, 'arguments', {}))}"
            text = QLabel(shorten(summary, 110))
            text.setFont(T.font(11))
            text.setWordWrap(True)
            text.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
            layout.addWidget(text, 1)
            reason = QLabel(shorten(str(getattr(request, "reason", "") or ""), 60))
            reason.setFont(T.font(10))
            reason.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
            layout.addWidget(reason)
            request_id = getattr(request, "id", "")
            layout.addWidget(ActionButton("Approve", tone="success", on_click=lambda rid=request_id: self._resolve(rid, True)))
            layout.addWidget(ActionButton("Deny", tone="danger", on_click=lambda rid=request_id: self._resolve(rid, False)))
            holder = QWidget()
            holder.setLayout(layout)
            holder.setStyleSheet("background: transparent;")
            self.approval_body.addWidget(holder)

    def _resolve(self, request_id: str, approved: bool) -> None:
        try:
            self.app.approvals.resolve(request_id, approved, reason="decided in the Coding Agent view" if approved else "denied in the Coding Agent view")
            self.transcript.add("system", f"Approval {request_id[:8]} {'granted' if approved else 'denied'}.")
        except Exception as exc:
            self.transcript.add("error", f"could not resolve the approval: {exc}")
        self._refresh_approvals()

    def _refresh_history(self) -> None:
        tasks = self.app.tasks.list(limit=40)
        rows = []
        colors = []
        for task in tasks:
            started = task.started_at or task.created_at
            taken = human_duration((task.finished_at - started) if task.finished_at else (time.time() - started))
            rows.append(
                [
                    task.id,
                    shorten(task.title or task.prompt, 60),
                    task.project_path or "—",
                    task.status,
                    task.tool_calls,
                    taken,
                ]
            )
            colors.append([None, None, None, T.STATUS_COLORS.get(task.status, T.TEXT_SOFT), None, None])
        self.history_table.set_rows(rows, colors=colors)


def _format_args(arguments: Any) -> str:
    if not isinstance(arguments, dict):
        return ""
    parts = []
    for key, value in arguments.items():
        text = shorten(str(value), 60)
        parts.append(f"{key}={text}")
    return " ".join(parts)
