"""Sandbox: workspaces, background processes, execution history and the real isolation level."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from codemind.events import Topics
from codemind.ui import theme as T
from codemind.ui.views.base import SectionHeader, View
from codemind.ui.widgets.glass import ActionButton, Card, EmptyState, KeyValue, Pill, StatTile, hline
from codemind.ui.widgets.logview import LogView
from codemind.ui.widgets.table import DataTable
from codemind.utils.files import human_bytes, human_duration
from codemind.utils.textutil import shorten


class SandboxView(View):
    title = "Sandbox"
    subtitle = "Every command runs in a workspace with limits, timeouts and a recorded result"

    def build(self) -> None:
        self.current_job = ""
        # --- isolation -----------------------------------------------------
        self.isolation_card = Card("Isolation level", "What the kernel on this machine actually allows — probed, not assumed")
        grid = QHBoxLayout()
        grid.setSpacing(T.GAP)
        self.tile_level = StatTile("Level", "—", sub="", accent=T.ACCENT, icon="⛨")
        self.tile_net = StatTile("Network", "—", sub="", accent=T.INFO, icon="⇅")
        self.tile_workspaces = StatTile("Workspaces", "0", sub="", accent=T.VIOLET, icon="▤")
        self.tile_space = StatTile("Workspace size", "0 B", sub="", accent=T.WARNING, icon="▦")
        self.tile_execs = StatTile("Commands run", "0", sub="", accent=T.SUCCESS, icon="◆")
        for tile in (self.tile_level, self.tile_net, self.tile_workspaces, self.tile_space, self.tile_execs):
            grid.addWidget(tile, 1)
        holder = QWidget()
        holder.setLayout(grid)
        holder.setStyleSheet("background: transparent;")
        self.isolation_card.content.addWidget(holder)
        self.isolation_body = QVBoxLayout()
        self.isolation_body.setSpacing(4)
        self.isolation_card.content.addLayout(self.isolation_body)
        self.policy_body = QVBoxLayout()
        self.policy_body.setSpacing(4)
        self.isolation_card.content.addLayout(self.policy_body)
        self.add(self.isolation_card)

        # --- run a command -------------------------------------------------
        run_card = Card("Run a command", "Runs inside a workspace with the configured timeout, memory limit and blocked patterns")
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.workspace_choice = QComboBox()
        self.workspace_choice.setMinimumWidth(320)
        self.workspace_choice.setFont(T.font(10, mono=True))
        self.workspace_choice.setStyleSheet(self._combo_style())
        self.command = QLineEdit()
        self.command.setPlaceholderText("python3 -m pytest -q   ·   npm run build   ·   python3 manage.py migrate")
        self.command.setFont(T.font(11, mono=True))
        self.command.setStyleSheet(self._input_style())
        self.command.returnPressed.connect(self.run_command)
        controls.addWidget(QLabel("Workspace"))
        controls.addWidget(self.workspace_choice, 1)
        controls.addWidget(self.command, 2)
        controls.addWidget(ActionButton("Run", tone="accent", on_click=self.run_command))
        run_card.content.addLayout(controls)
        self.command_output = LogView(max_lines=1200)
        self.command_output.setMinimumHeight(150)
        run_card.content.addWidget(self.command_output)
        meta = QHBoxLayout()
        self.command_meta = QLabel("")
        self.command_meta.setFont(T.font(10))
        self.command_meta.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        meta.addWidget(self.command_meta, 1)
        meta.addWidget(ActionButton("Clear", on_click=self.command_output.clear_text))
        meta.addWidget(ActionButton("Copy output", on_click=lambda: self._copy(self.command_output.toPlainText())))
        run_card.content.addLayout(meta)
        self.add(run_card)

        # --- background processes ------------------------------------------
        self.jobs_card = Card("Background processes", "Long-running commands with their own lifecycle — dev servers, watchers, builds")
        self.jobs_table = DataTable(["Name", "Command", "Status", "Runtime", "Exit", "Working dir"], stretch_column=1)
        self.jobs_table.set_mono_columns([1, 5])
        self.jobs_table.setMinimumHeight(140)
        self.jobs_table.itemSelectionChanged.connect(self._select_job)
        self.jobs_card.content.addWidget(self.jobs_table)
        job_controls = QHBoxLayout()
        job_controls.setSpacing(8)
        self.job_name = QLineEdit()
        self.job_name.setPlaceholderText("name")
        self.job_name.setFixedWidth(130)
        self.job_name.setFont(T.font(11))
        self.job_name.setStyleSheet(self._input_style())
        self.job_command = QLineEdit()
        self.job_command.setPlaceholderText("long-running command, e.g. python3 -m http.server 8000")
        self.job_command.setFont(T.font(11, mono=True))
        self.job_command.setStyleSheet(self._input_style())
        job_controls.addWidget(self.job_name)
        job_controls.addWidget(self.job_command, 1)
        job_controls.addWidget(ActionButton("Start", tone="accent", on_click=self.start_job))
        job_controls.addWidget(ActionButton("Stop", tone="danger", on_click=self.stop_job))
        job_controls.addWidget(ActionButton("Stop all", tone="danger", on_click=self.stop_all_jobs))
        self.jobs_card.content.addLayout(job_controls)
        self.job_log = LogView(max_lines=1200)
        self.job_log.setMinimumHeight(150)
        self.jobs_card.content.addWidget(self.job_log)
        self.add(self.jobs_card)

        # --- workspaces ----------------------------------------------------
        self.workspaces_card = Card("Workspaces", "Each task gets its own directory so parallel work cannot collide")
        self.workspaces_table = DataTable(["Task", "Mode", "Status", "Size", "Branch", "Path"], stretch_column=5)
        self.workspaces_table.set_mono_columns([0, 5])
        self.workspaces_table.setMinimumHeight(150)
        self.workspaces_card.content.addWidget(self.workspaces_table)
        ws_buttons = QHBoxLayout()
        ws_buttons.addWidget(ActionButton("Prune old workspaces", on_click=self.prune_workspaces))
        ws_buttons.addWidget(ActionButton("Kill task processes", tone="danger", on_click=self.kill_children))
        ws_buttons.addStretch(1)
        self.workspace_hint = QLabel("")
        self.workspace_hint.setFont(T.font(10))
        self.workspace_hint.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        ws_buttons.addWidget(self.workspace_hint)
        self.workspaces_card.content.addLayout(ws_buttons)
        self.add(self.workspaces_card)

        # --- history -------------------------------------------------------
        self.history_card = Card("Execution history", "Nothing is hidden: command, working directory, exit code and tail of output")
        self.history_table = DataTable(["When", "Command", "Exit", "Duration", "Task", "Working dir"], stretch_column=1)
        self.history_table.set_mono_columns([1, 5])
        self.history_table.setMinimumHeight(220)
        self.history_table.itemSelectionChanged.connect(self._select_history)
        self.history_card.content.addWidget(self.history_table)
        self.history_output = LogView(max_lines=600)
        self.history_output.setMinimumHeight(120)
        self.history_card.content.addWidget(self.history_output)
        self.add(self.history_card)

    # -- styles -----------------------------------------------------------
    @staticmethod
    def _input_style() -> str:
        return (
            f"QLineEdit {{ background: rgba(0,0,0,0.32); color: {T.TEXT}; border: 1px solid {T.HAIRLINE};"
            f" border-radius: 8px; padding: 4px 8px; }} QLineEdit:focus {{ border-color: {T.ACCENT}; }}"
        )

    @staticmethod
    def _combo_style() -> str:
        return (
            f"QComboBox {{ background: rgba(0,0,0,0.32); color: {T.TEXT}; border: 1px solid {T.HAIRLINE};"
            f" border-radius: 8px; padding: 4px 8px; }}"
            f"QComboBox QAbstractItemView {{ background: {T.SURFACE}; color: {T.TEXT}; }}"
        )

    # -- actions ----------------------------------------------------------
    def _copy(self, text: str) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(text)
        self.command_meta.setText("output copied to the clipboard")

    def _selected_workspace(self):
        index = self.workspace_choice.currentIndex()
        workspaces = getattr(self, "_workspaces", [])
        if 0 <= index < len(workspaces):
            return workspaces[index]
        return None

    def run_command(self) -> None:
        command = self.command.text().strip()
        workspace = self._selected_workspace()
        if not command:
            self.command_meta.setText("type a command first")
            return
        if workspace is None:
            self.command_meta.setText("no workspace available")
            return
        self.command_output.clear_text()
        self.command_meta.setText(f"running in {workspace.path} …")
        from codemind.sandbox.manager import CommandDenied

        try:
            result = self.app.sandbox.execute(
                command,
                workspace=workspace,
                task_id="manual",
                timeout=float(self.app.config.agent.command_timeout),
                purpose="manual",
                on_line=lambda stream, line: self.command_output.stream(line, stream),
            )
        except CommandDenied as exc:
            self.command_meta.setText(f"refused by policy: {exc}")
            return
        except Exception as exc:
            self.command_meta.setText(f"failed: {exc}")
            return
        self.command_meta.setText(
            f"exit {result.exit_code} in {result.duration:.2f}s" + (" (timed out)" if result.timed_out else "")
        )
        self.refresh()

    def start_job(self) -> None:
        workspace = self._selected_workspace()
        command = self.job_command.text().strip()
        if not command or workspace is None:
            self.workspace_hint.setText("pick a workspace and type a command")
            return
        name = self.job_name.text().strip() or command.split()[0]
        try:
            job = self.app.jobs.start(name, ["/bin/bash", "-lc", command], cwd=workspace.path, env_extra=self.app.sandbox.venv_env(workspace))
            self.current_job = job.id
            self.job_log.clear_text()
        except Exception as exc:
            self.workspace_hint.setText(f"could not start: {exc}")
        self.refresh()

    def stop_job(self) -> None:
        job_id = self.current_job
        if not job_id:
            row = self.jobs_table.current_row_data()
            job_id = getattr(self, "_job_ids", {}).get(row[0] if row else "", "")
        if job_id:
            self.app.jobs.stop(job_id)
        self.refresh()

    def create_workspace(self) -> None:
        """Copy a project into an isolated workspace without starting a task."""
        from PySide6.QtWidgets import QFileDialog

        start_dir = str(self.app.config.storage.projects_root or "")
        try:
            projects = self.app.knowledge.projects(limit=1)
            if projects and projects[0].get("path"):
                start_dir = str(projects[0]["path"])
        except Exception:
            pass
        if start_dir and not Path(start_dir).exists():
            start_dir = ""
        chosen = QFileDialog.getExistingDirectory(self, "Select the project to isolate", start_dir)
        if not chosen:
            self.workspace_hint.setText("no project selected")
            return
        try:
            workspace = self.app.sandbox.create_workspace(chosen, mode=self.app.config.sandbox.workspace_mode)
        except Exception as exc:
            self.workspace_hint.setText(f"could not create a workspace: {exc}")
            return
        self.workspace_hint.setText(f"workspace for {workspace.id} ready at {workspace.path}")
        self.refresh()

    def stop_all_jobs(self) -> None:
        count = self.app.jobs.stop_all()
        self.workspace_hint.setText(f"stopped {count} process(es)")
        self.refresh()

    def prune_workspaces(self) -> None:
        removed = self.app.sandbox.prune_workspaces()
        self.workspace_hint.setText(f"removed {removed} workspace(s)")
        self.refresh()

    def kill_children(self) -> None:
        killed = self.app.sandbox.kill_children()
        self.workspace_hint.setText(f"signalled {killed} child process(es)")
        self.refresh()

    def _select_job(self) -> None:
        row = self.jobs_table.current_row_data()
        if not row:
            return
        job_id = getattr(self, "_job_ids", {}).get(row[0], "")
        self.current_job = job_id or self.current_job
        if job_id:
            self.job_log.clear_text()
            self.job_log.append_line(self.app.jobs.log_tail(job_id, lines=400) or "(no output yet)")

    def _select_history(self) -> None:
        row = self.history_table.current_row_data()
        if not row:
            return
        entries = getattr(self, "_history", [])
        index = self.history_table.currentRow()
        if 0 <= index < len(entries):
            entry = entries[index]
            self.history_output.clear_text()
            self.history_output.append_line(f"$ {entry.get('argv', '')}\n{entry.get('stdout_tail') or ''}")
            if entry.get("stderr_tail"):
                self.history_output.stream(entry["stderr_tail"], "stderr")

    # -- events -----------------------------------------------------------
    def on_event(self, topic: str, payload: dict[str, Any]) -> None:
        if topic != Topics.SANDBOX_EXEC:
            return
        kind = payload.get("job_event")
        if kind == "job.line" and payload.get("id") == self.current_job:
            self.job_log.stream(str(payload.get("line", "")), str(payload.get("stream", "stdout")))
        elif kind in ("job.started", "job.finished"):
            self.workspace_hint.setText(f"{payload.get('name')} {kind.split('.')[-1]} (exit {payload.get('exit_code')})")
        elif payload.get("line") and not kind:
            self.command_output.stream(str(payload["line"]), str(payload.get("stream", "stdout")))

    # -- refresh ----------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_isolation()
        self._refresh_workspaces()
        self._refresh_jobs()
        self._refresh_history()

    def _refresh_isolation(self) -> None:
        status = self.app.sandbox.status()
        isolation = status["isolation"]
        self.tile_level.set_value(isolation["level"], sub=isolation.get("mechanism", ""))
        network = isolation.get("network") or ("allowed" if self.app.config.sandbox.allow_network else "disabled")
        self.tile_net.set_value(network, sub="configured in Settings → Sandbox", accent=T.SUCCESS if network != "blocked" else T.WARNING)
        in_use = sum(1 for entry in status.get("workspaces", []) if entry.get("dirty"))
        self.tile_workspaces.set_value(str(status["workspace_count"]), sub=f"{in_use} currently in use")
        self.tile_space.set_value(human_bytes(status["workspace_bytes"]), sub=f"keeps {self.app.config.sandbox.keep_workspaces} workspaces")
        history = status["history"]
        self.tile_execs.set_value(str(history.get("total", 0)), sub=f"{history.get('failed', 0)} failed · avg {history.get('avg_duration', 0):.1f}s")
        while self.isolation_body.count():
            item = self.isolation_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self.isolation_body.addWidget(KeyValue("Mechanism", isolation.get("mechanism", "—")))
        for note in isolation.get("notes") or []:
            self.isolation_body.addWidget(KeyValue("Note", note, value_color=T.TEXT_SOFT))
        while self.policy_body.count():
            item = self.policy_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        policy = status["policy"]
        self.policy_body.addWidget(hline())
        self.policy_body.addWidget(KeyValue("Network allowed", str(policy.get("allow_network")), value_color=T.SUCCESS if policy.get("allow_network") else T.WARNING))
        self.policy_body.addWidget(KeyValue("Destructive commands", "allowed (still asks for approval)" if policy.get("allow_destructive") else "refused", value_color=T.WARNING if policy.get("allow_destructive") else T.SUCCESS))
        self.policy_body.addWidget(KeyValue("Timeout", f"{self.app.config.agent.command_timeout}s per command"))
        self.policy_body.addWidget(KeyValue("Memory cap", f"{policy.get('memory_limit_mb')} MB" if policy.get("memory_limit_mb") else "no hard cap"))
        self.policy_body.addWidget(KeyValue("Blocked patterns", ", ".join(policy.get("blocked_patterns") or []) or "none", mono=True))
        self.policy_body.addWidget(KeyValue("Workspace mode", self.app.config.sandbox.workspace_mode))
        tools = status.get("commands") or {}
        present = [name for name, path in tools.items() if path]
        missing = [name for name, path in tools.items() if not path]
        self.policy_body.addWidget(KeyValue("Toolchain", f"{len(present)} available: {', '.join(present[:12])}", mono=True))
        if missing:
            self.policy_body.addWidget(KeyValue("Not installed", ", ".join(missing), mono=True, value_color=T.TEXT_SOFT))

    def _refresh_workspaces(self) -> None:
        workspaces = self.app.sandbox.list_workspaces()
        self._workspaces = workspaces
        current = self.workspace_choice.currentIndex()
        self.workspace_choice.blockSignals(True)
        self.workspace_choice.clear()
        for workspace in workspaces:
            self.workspace_choice.addItem(f"{workspace.task_id or workspace.id[:8]} · {workspace.path}")
        self.workspace_choice.blockSignals(False)
        if 0 <= current < self.workspace_choice.count():
            self.workspace_choice.setCurrentIndex(current)
        rows = []
        for workspace in workspaces:
            size = workspace.disk_usage()
            rows.append(
                [
                    workspace.task_id or workspace.id,
                    workspace.mode,
                    "in use" if workspace.dirty else "free",
                    human_bytes(size),
                    workspace.branch or "—",
                    str(workspace.path),
                ]
            )
        self.workspaces_table.set_rows(rows)

    def _refresh_jobs(self) -> None:
        jobs = self.app.jobs.jobs(limit=30)
        self._job_ids = {job["name"]: job["id"] for job in jobs}
        rows = []
        colors = []
        for job in jobs:
            rows.append([job["name"], job["command"], job["status"], job["runtime"], job["exit_code"], job["cwd"]])
            colors.append([None, None, T.SUCCESS if job["running"] else (T.DANGER if job["status"] == "failed" else T.TEXT_SOFT), None, None, None])
        self.jobs_table.set_rows(rows, colors=colors)
        if jobs:
            self.jobs_card.set_subtitle(f"{sum(1 for job in jobs if job['running'])} running · logs in {self.app.jobs.log_dir}")

    def _refresh_history(self) -> None:
        entries = self.app.sandbox.history(limit=60)
        self._history = entries
        rows = []
        colors = []
        for entry in entries:
            exit_code = entry.get("exit_code")
            rows.append(
                [
                    time.strftime("%H:%M:%S", time.localtime(entry.get("ts") or time.time())),
                    shorten(entry.get("argv", ""), 70),
                    "timeout" if entry.get("timed_out") else exit_code,
                    f"{entry.get('duration') or 0:.2f}s",
                    entry.get("task_id") or "—",
                    entry.get("cwd", ""),
                ]
            )
            colors.append([None, None, T.SUCCESS if exit_code == 0 else T.DANGER, None, None, None])
        self.history_table.set_rows(rows, colors=colors)
        stats = self.app.sandbox.history_stats()
        self.history_card.set_subtitle(
            f"{stats.get('total', 0)} commands · {stats.get('failed', 0)} with a non-zero exit · "
            f"{stats.get('timeouts', 0)} timed out · average {stats.get('avg_duration', 0):.2f}s"
        )
