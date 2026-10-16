"""Overview: the one screen that answers "what is CodeMind doing right now?"."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from codemind.ui import theme as T
from codemind.ui.views.base import SectionHeader, View
from codemind.ui.widgets.glass import (
    ActionButton,
    Card,
    EmptyState,
    KeyValue,
    Pill,
    Sparkline,
    StatTile,
    StatusDot,
    hline,
)
from codemind.ui.widgets.table import DataTable
from codemind.utils.files import human_duration
from codemind.utils.textutil import shorten


class OverviewView(View):
    title = "Overview"
    subtitle = "Live state of the agent, the machine and what it has learned"

    def build(self) -> None:
        # --- status strip -------------------------------------------------
        self.status_card = Card("", direction="h", padding=14)
        self.dot = StatusDot("starting")
        self.status_card.body.addWidget(self.dot)
        self.status_card.body.addStretch(1)
        self.state_pills = QHBoxLayout()
        self.state_pills.setSpacing(8)
        self.pill_agent = Pill("agent idle")
        self.pill_model = Pill("model: probing")
        self.pill_training = Pill("training idle")
        self.pill_storage = Pill("storage: —")
        self.pill_usb = Pill("usb: checking")
        for pill in (self.pill_agent, self.pill_model, self.pill_training, self.pill_storage, self.pill_usb):
            self.state_pills.addWidget(pill)
        self.status_card.body.addLayout(self.state_pills)
        self.add(self.status_card)

        # --- resource tiles ----------------------------------------------
        tiles = QGridLayout()
        tiles.setHorizontalSpacing(T.GAP)
        tiles.setVerticalSpacing(T.GAP)
        self.tile_cpu = StatTile("CPU", "—", sub="waiting for a sample", accent=T.ACCENT, icon="▮")
        self.tile_ram = StatTile("Memory", "—", sub="", accent=T.VIOLET, icon="▤")
        self.tile_gpu = StatTile("GPU", "—", sub="", accent=T.SUCCESS, icon="▥")
        self.tile_disk = StatTile("Storage", "—", sub="", accent=T.INFO, icon="▦")
        self.tile_tasks = StatTile("Tasks", "0", sub="none active", accent=T.ACCENT, icon="◆")
        self.tile_memory = StatTile("Memory store", "0", sub="entries", accent=T.WARNING, icon="✧")
        self.tile_learning = StatTile("Learning", "—", sub="", accent=T.SUCCESS, icon="◇")
        self.tile_evidence = StatTile("Evidence", "—", sub="proven work", accent=T.SUCCESS, icon="✓")
        self.tiles = (
            self.tile_cpu,
            self.tile_ram,
            self.tile_gpu,
            self.tile_disk,
            self.tile_tasks,
            self.tile_memory,
            self.tile_learning,
            self.tile_evidence,
        )
        # four tiles per row: eight across one row would elide every caption
        for index, tile in enumerate(self.tiles):
            tiles.addWidget(tile, index // 4, index % 4)
        wrapper = QWidget()
        wrapper.setLayout(tiles)
        wrapper.setStyleSheet("background: transparent;")
        self.add(wrapper)

        # --- charts -------------------------------------------------------
        charts = QHBoxLayout()
        charts.setSpacing(T.GAP)
        self.cpu_card = Card("Processor", "Total CPU load across all cores, sampled every few seconds")
        self.cpu_spark = Sparkline(color_value=T.ACCENT, height=70)
        self.cpu_card.content.addWidget(self.cpu_spark)
        self.cpu_note = QLabel("")
        self.cpu_note.setFont(T.font(10))
        self.cpu_note.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        self.cpu_card.content.addWidget(self.cpu_note)
        charts.addWidget(self.cpu_card, 1)

        self.ram_card = Card("Memory pressure", "Physical memory in use, with the governor's decision")
        self.ram_spark = Sparkline(color_value=T.VIOLET, height=70)
        self.ram_card.content.addWidget(self.ram_spark)
        self.ram_note = QLabel("")
        self.ram_note.setFont(T.font(10))
        self.ram_note.setStyleSheet(f"color: {T.TEXT_SOFT}; background: transparent;")
        self.ram_card.content.addWidget(self.ram_note)
        charts.addWidget(self.ram_card, 1)
        self.add_layout(charts)

        # --- current work -------------------------------------------------
        self.work_card = Card("Current work", "The task the agent is on right now, and what is waiting")
        self.work_body = QVBoxLayout()
        self.work_body.setSpacing(6)
        self.work_card.content.addLayout(self.work_body)
        self.add(self.work_card)

        # --- training + learning ------------------------------------------
        row = QHBoxLayout()
        row.setSpacing(T.GAP)
        self.training_card = Card("Training", "Headless runs continue whether or not this window is open")
        self.training_body = QVBoxLayout()
        self.training_body.setSpacing(6)
        self.training_card.content.addLayout(self.training_body)
        row.addWidget(self.training_card, 1)
        self.learning_card = Card("Learning", "Memory is retrieved knowledge; the adapter is trained behaviour")
        self.learning_body = QVBoxLayout()
        self.learning_body.setSpacing(6)
        self.learning_card.content.addLayout(self.learning_body)
        row.addWidget(self.learning_card, 1)
        self.add_layout(row)

        # --- autonomy ------------------------------------------------------
        self.autonomy_card = Card("Autonomous mode", "Scheduled work on the projects you approved, inside a daily budget")
        self.autonomy_body = QVBoxLayout()
        self.autonomy_body.setSpacing(6)
        self.autonomy_card.content.addLayout(self.autonomy_body)
        self.add(self.autonomy_card)

        # --- activity + alerts --------------------------------------------
        row2 = QHBoxLayout()
        row2.setSpacing(T.GAP)
        self.activity_card = Card("Recent activity", "The last tasks CodeMind ran for you")
        self.activity_table = DataTable(["When", "Task", "Status", "Tools"], stretch_column=1)
        self.activity_table.set_mono_columns([3])
        self.activity_table.setMinimumHeight(190)
        self.activity_card.content.addWidget(self.activity_table)
        row2.addWidget(self.activity_card, 3)

        self.alerts_card = Card("Attention", "Anything that needs a decision or a fix")
        self.alerts_body = QVBoxLayout()
        self.alerts_body.setSpacing(6)
        self.alerts_card.content.addLayout(self.alerts_body)
        row2.addWidget(self.alerts_card, 2)
        self.add_layout(row2)

        self.footer = QLabel("")
        self.footer.setFont(T.font(10))
        self.footer.setStyleSheet(f"color: {T.TEXT_FAINT}; background: transparent;")
        self.add(self.footer)

    # -- refresh ----------------------------------------------------------
    def refresh(self) -> None:
        snapshot = self.app.overview_snapshot()
        hardware = snapshot["hardware"]
        resources = snapshot["resources"]
        agent = snapshot["agent"]
        training = snapshot["training"]
        self._refresh_status(snapshot)
        self._refresh_resources(snapshot)
        self._refresh_work(agent)
        self._refresh_training(training)
        self._refresh_learning(snapshot)
        self._refresh_evidence(snapshot)
        self._refresh_activity(agent["recent"])
        self._refresh_autonomy(snapshot)
        self._refresh_alerts(snapshot)

    def _refresh_status(self, snap: dict[str, Any]) -> None:
        runner = snap["agent"]["runner"]
        active = runner.get("active") or []
        if not snap["hardware_ready"]:
            state, color, label = "starting", T.ACCENT, "probing hardware"
        elif active:
            state, color, label = "running", T.SUCCESS, f"working on {len(active)} task(s)"
        elif runner.get("queued"):
            state, color, label = "queued", T.ACCENT, f"{runner['queued']} task(s) queued"
        else:
            state, color, label = "idle", T.TEXT_SOFT, "idle — ready for a task"
        self.dot.set_state(label, color)
        counts = snap["agent"]["counts"]
        self.pill_agent.set_state(f"agent: {state}", color)
        backend = snap["agent"]["backend"]
        self.pill_model.set_state(f"model: {backend['name']}", T.ACCENT if backend["name"] != "heuristic" else T.TEXT_SOFT)
        runs = snap["training"]["active"]
        if runs:
            run = runs[0]
            self.pill_training.set_state(f"training: {run['name']} {run.get('progress', {}).get('percent', 0):.0f}%", T.VIOLET)
        else:
            self.pill_training.set_state("training: idle", T.TEXT_SOFT)
        storage = snap["storage"]
        free_gb = (storage.get("internal_free_bytes") or 0) / (1024**3)
        self.pill_storage.set_state(f"storage: {free_gb:.0f} GB free", T.INFO)
        if storage.get("usb_active"):
            self.pill_usb.set_state(f"usb: {storage.get('usb_active')}", T.SUCCESS)
        else:
            self.pill_usb.set_state("usb: not attached", T.TEXT_SOFT)

    def _refresh_resources(self, snap: dict[str, Any]) -> None:
        resources = snap["resources"]
        hardware = snap["hardware"]
        cpu = resources.get("cpu_percent") or 0.0
        self.tile_cpu.set_value(
            f"{cpu:.0f}%",
            sub=f"{hardware.get('logical_cores', 0)} threads · load {resources.get('load1') or 0:.2f}",
            accent=T.level_color(cpu, inverted=True) if cpu else T.ACCENT,
        )
        ram_used = resources.get("ram_used_mb") or 0.0
        ram_total = resources.get("ram_total_mb") or hardware.get("ram_total_mb") or 0.0
        fraction = (ram_used / ram_total) if ram_total else 0.0
        self.tile_ram.set_value(
            f"{ram_used / 1024:.1f} GB",
            sub=f"of {ram_total / 1024:.1f} GB ({fraction * 100:.0f}%)",
            accent=T.level_color(fraction * 100) if ram_total else T.VIOLET,
        )
        vram_total = resources.get("vram_total_mb") or 0
        if vram_total:
            vram_used = resources.get("vram_used_mb") or 0
            self.tile_gpu.set_value(
                f"{vram_used / 1024:.1f} GB",
                sub=f"of {vram_total / 1024:.1f} GB VRAM · {resources.get('gpu_util') or 0:.0f}% util",
                accent=T.level_color((vram_used / vram_total) * 100),
            )
        else:
            self.tile_gpu.set_value("—", sub="no CUDA GPU", accent=T.TEXT_FAINT)
        disk_free = resources.get("disk_free_gb")
        self.tile_disk.set_value(f"{disk_free:.0f} GB" if disk_free else "—", sub=f"free at {snap['app']['data_root']}", accent=T.INFO)

        counts = snap["agent"]["counts"]
        active = counts.get("active", 0)
        attention = counts.get("failed", 0) + counts.get("blocked", 0)
        self.tile_tasks.set_value(str(counts.get("total", 0)), sub=f"{active} active · {counts.get('completed', 0)} completed · {attention} need attention")

        mem = snap["memory"]
        self.tile_memory.set_value(str(mem.get("total", 0)), sub=f"{mem.get('pinned', 0)} pinned · quality {mem.get('avg_quality', 0):.2f}")

        training = snap["training"]
        adapter = training.get("active_adapter")
        if adapter:
            metrics = adapter.get("metrics") or {}
            self.tile_learning.set_value(
                adapter["version"][:8],
                sub=f"adapter active · tool accuracy {metrics.get('accuracy', 0):.2f}" if metrics.get("accuracy") is not None else "adapter active",
            )
        else:
            self.tile_learning.set_value("baseline", sub=f"{training['stats'].get('runs', 0)} run(s) · none promoted")

        cpu_series = snap.get("resource_history") or []
        ram_series = snap.get("ram_history") or []
        self.cpu_spark.set_values(cpu_series, ceiling=100.0)
        self.ram_spark.set_values(ram_series, ceiling=(hardware.get("ram_total_mb") or None))
        governor = snap.get("governor") or {}
        self.cpu_note.setText(
            f"Governor: {governor.get('mode', '—')} · agent parallelism {governor.get('agent_parallel', '—')} · "
            f"training threads {governor.get('training_threads', '—')}"
            + (f" · training paused ({governor.get('pause_reason')})" if governor.get("pause_training") else "")
        )
        self.ram_note.setText(
            f"{snap['app']['memory_bytes'].get('rss_mb', 0):.0f} MB used by CodeMind itself · "
            f"{resources.get('swap_used_mb') or 0:.0f} MB swap. {hardware.get('summary_line', '')}"
        )

    def _refresh_work(self, agent: dict[str, Any]) -> None:
        while self.work_body.count():
            item = self.work_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
            elif item.layout() is not None:
                _clear(item.layout())
        active = agent.get("active_task")
        if active:
            head = QHBoxLayout()
            title = QLabel(shorten(active["title"] or active["prompt"], 90))
            title.setFont(T.font(13, QFont.DemiBold))
            title.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
            head.addWidget(title)
            head.addStretch(1)
            status = active["status"]
            head.addWidget(Pill(status, color_value=T.STATUS_COLORS.get(status, T.TEXT_SOFT), filled=True))
            holder = QWidget()
            holder.setLayout(head)
            holder.setStyleSheet("background: transparent;")
            self.work_body.addWidget(holder)
            steps = [step for step in (active.get("steps") or []) if step.get("status") not in ("pending",)]
            done = sum(1 for step in steps if step["status"] == "done")
            self.work_body.addWidget(KeyValue("Progress", f"{done}/{len(active.get('steps') or [])} steps · {active.get('tool_calls', 0)} tool calls"))
            if steps:
                last = steps[-1]
                self.work_body.addWidget(KeyValue("Last step", f"{last['title']} → {last.get('tool') or '—'}", mono=True))
            self.work_body.addWidget(KeyValue("Workspace", active.get("workspace") or "project in place", mono=True))
        else:
            self.work_body.addWidget(EmptyState("Nothing running", "Open Coding Agent to hand CodeMind a task, or enable autonomous mode in Settings."))
        queued = agent.get("queue") or []
        if queued:
            self.work_body.addWidget(hline())
            for task in queued[:5]:
                self.work_body.addWidget(KeyValue(f"Queued {task['id'][:6]}", shorten(task["title"] or task["prompt"], 70)))
        pending = agent.get("approvals_pending") or []
        if pending:
            self.work_body.addWidget(hline())
            for request in pending[:3]:
                self.work_body.addWidget(KeyValue("Approval", shorten(request.get("summary", ""), 80), value_color=T.WARNING))
        jobs = agent.get("jobs") or []
        if jobs:
            self.work_body.addWidget(hline())
            for job in jobs[:4]:
                state = "running" if job.get("running") else "finished"
                color_value = T.ACCENT if job.get("running") else T.SUCCESS if job.get("exit_code") == 0 else T.DANGER
                self.work_body.addWidget(KeyValue(f"Process ({state})", f"{shorten(job.get('command', ''), 58)} · {job.get('runtime', '—')}", value_color=color_value))

    def _refresh_training(self, training: dict[str, Any]) -> None:
        _clear_layout(self.training_body)
        active = training.get("active") or []
        if active:
            run = active[0]
            self.training_body.addWidget(KeyValue("Running", f"{run['name']} · {run['method']}", mono=True))
            progress = run.get("progress") or {}
            self.training_body.addWidget(KeyValue("Progress", f"{progress.get('percent', 0):.1f}% · step {progress.get('step', 0)}/{progress.get('total_steps') or '—'}"))
            if progress.get("message"):
                self.training_body.addWidget(KeyValue("Phase", str(progress["message"])))
            metrics = run.get("metrics") or {}
            if metrics.get("loss") is not None:
                self.training_body.addWidget(KeyValue("Loss", f"{metrics['loss']:.4f} · accuracy {metrics.get('accuracy', 0):.3f}"))
        else:
            recent = training.get("recent") or []
            if recent:
                run = recent[0]
                self.training_body.addWidget(KeyValue("Last run", f"{run['name']} · {run['status']}", mono=True))
                metrics = run.get("metrics") or {}
                if metrics:
                    self.training_body.addWidget(KeyValue("Result", f"loss {metrics.get('final_loss', '—')} · accuracy {metrics.get('accuracy', '—')}"))
            else:
                self.training_body.addWidget(EmptyState("No training runs yet", "Training runs headlessly on your GPU or CPU; open the Training view to start one."))
        stats = training.get("stats") or {}
        self.training_body.addWidget(KeyValue("Totals", f"{stats.get('runs', 0)} runs · {stats.get('completed', 0)} completed · {stats.get('failed', 0)} failed"))
        self.training_body.addWidget(KeyValue("Adapter", (training.get("active_adapter") or {}).get("version", "none promoted")[:14], mono=True))

    def _refresh_learning(self, snap: dict[str, Any]) -> None:
        _clear_layout(self.learning_body)
        mem = snap["memory"]
        knowledge = snap["knowledge"]
        kinds = mem.get("by_kind") or {}
        for kind, count in sorted(kinds.items(), key=lambda item: item[1], reverse=True)[:5]:
            self.learning_body.addWidget(KeyValue(kind.replace("_", " "), f"{count}"))
        self.learning_body.addWidget(hline())
        self.learning_body.addWidget(KeyValue("Memories", f"{mem.get('total', 0)} total · {mem.get('usable', 0)} usable"))
        self.learning_body.addWidget(KeyValue("Indexed files", f"{knowledge.get('files', 0)} files · {knowledge.get('symbols', 0)} symbols · {knowledge.get('suites', 0)} test suites"))
        stats = snap["training"]["stats"]
        self.learning_body.addWidget(KeyValue("Training datasets", f"{stats.get('datasets', 0)} · {stats.get('examples', 0)} examples"))
        self.learning_body.addWidget(KeyValue("Checkpoints", f"{stats.get('checkpoints', 0)} stored ({stats.get('artifact_size', '0 B')})"))
        lessons = kinds.get("lesson", 0) + kinds.get("mistake", 0)
        if lessons:
            self.learning_body.addWidget(KeyValue("Lessons recorded", f"{lessons}", value_color=T.SUCCESS))

    def _refresh_autonomy(self, snap: dict[str, Any]) -> None:
        state = snap.get("autonomy") or {}
        _clear_layout(self.autonomy_body)
        can_run, reason = (state.get("can_run") or (False, "unknown"))[0], (state.get("can_run") or (False, "unknown"))[1]
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(Pill("enabled" if state.get("enabled") else "disabled", color_value=T.SUCCESS if state.get("enabled") else T.TEXT_SOFT))
        head.addWidget(Pill(f"every {state.get('interval_minutes', 0)} min", color_value=T.ACCENT))
        head.addWidget(
            Pill(
                f"budget {state.get('budget_used_minutes', 0):.1f}/{state.get('daily_budget_minutes', 0)} min",
                color_value=T.WARNING if state.get("budget_used_minutes", 0) >= state.get("daily_budget_minutes", 1) else T.INFO,
            )
        )
        head.addStretch(1)
        head.addWidget(ActionButton("Run a session now", tone="accent", on_click=self._run_autonomy_now))
        holder = QWidget()
        holder.setLayout(head)
        holder.setStyleSheet("background: transparent;")
        self.autonomy_body.addWidget(holder)
        self.autonomy_body.addWidget(KeyValue("State", reason, value_color=T.SUCCESS if can_run else T.TEXT_SOFT))
        projects = state.get("repos") or []
        self.autonomy_body.addWidget(KeyValue("Watched projects", ", ".join(projects) or "none — add one in Settings → Autonomy"))
        proposals = self.app.autonomy.proposals(status="pending", limit=5)
        if not proposals:
            self.autonomy_body.addWidget(EmptyState("No proposals waiting", "CodeMind suggests work here when it finds something worth doing (failing tests, missing coverage, new traces to learn from).", icon="◈"))
        for proposal in proposals:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            layout.addWidget(Pill(proposal.get("category_label", ""), color_value=T.ACCENT))
            title = QLabel(shorten(proposal.get("title", ""), 84))
            title.setFont(T.font(11))
            title.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
            title.setWordWrap(True)
            layout.addWidget(title, 1)
            layout.addWidget(ActionButton("Accept", tone="success", on_click=lambda pid=proposal["id"]: self._accept_proposal(pid)))
            layout.addWidget(ActionButton("Dismiss", on_click=lambda pid=proposal["id"]: self._dismiss_proposal(pid)))
            holder = QWidget()
            holder.setLayout(layout)
            holder.setStyleSheet("background: transparent;")
            self.autonomy_body.addWidget(holder)

    # -- hooks used by the command palette ---------------------------------
    def run_autonomy_now(self) -> None:
        """Run one autonomy session immediately (same as the button on the card)."""
        self._run_autonomy_now()

    def accept_all_proposals(self) -> None:
        """Turn every waiting proposal into a queued task."""
        try:
            pending = self.app.autonomy.proposals(status="pending", limit=25)
        except Exception as exc:
            self.autonomy_card.set_subtitle(f"could not read proposals: {exc}")
            return
        if not pending:
            self.autonomy_card.set_subtitle("no proposals waiting — nothing to accept")
            return
        accepted = 0
        for proposal in pending:
            try:
                self.app.autonomy.accept(proposal["id"])
                accepted += 1
            except Exception as exc:
                self.autonomy_card.set_subtitle(f"could not accept proposal {proposal['id']}: {exc}")
                break
        self.autonomy_card.set_subtitle(f"accepted {accepted} of {len(pending)} proposals — they are queued as tasks")
        self.refresh()

    def _run_autonomy_now(self) -> None:
        try:
            result = self.app.autonomy.run_session(force=True)
            handled = result.get("handled", 0)
            proposals = result.get("proposals") or []
            self.autonomy_card.set_subtitle(
                f"Session finished: {handled} action(s) taken, {len(proposals)} new proposal(s) — {result.get('reason', '')}"
            )
        except Exception as exc:
            self.autonomy_card.set_subtitle(f"session failed: {exc}")
        self.refresh()

    def _accept_proposal(self, proposal_id: int) -> None:
        try:
            result = self.app.autonomy.accept(proposal_id)
            self.autonomy_card.set_subtitle(f"Accepted: task {result.get('task_id', '')[:8]} queued.")
        except Exception as exc:
            self.autonomy_card.set_subtitle(f"could not accept: {exc}")
        self.refresh()

    def _dismiss_proposal(self, proposal_id: int) -> None:
        try:
            self.app.autonomy.dismiss(proposal_id, reason="dismissed from the overview")
        except Exception as exc:
            self.autonomy_card.set_subtitle(f"could not dismiss: {exc}")
        self.refresh()

    def _refresh_evidence(self, snap: dict[str, Any]) -> None:
        """The honesty tile: how much of the completed work carries passing evidence."""
        quality = snap.get("quality") or {}
        completed = int(quality.get("completed") or 0)
        if not completed:
            self.tile_evidence.set_value("—", sub="no completed task yet", accent=T.TEXT_FAINT)
            return
        unverified = int(quality.get("unverified") or 0)
        rate = float(quality.get("verified_rate") or 0.0) * 100
        accent = T.SUCCESS if unverified == 0 else T.WARNING
        sub = f"{quality.get('verified', 0)} of {completed} with passing checks"
        if unverified:
            sub += f" · {unverified} unproven"
        self.tile_evidence.set_number(rate, lambda value: f"{value:.0f}%", sub=sub, accent=accent)

    def _refresh_activity(self, recent: list[dict[str, Any]]) -> None:
        rows = []
        colors = []
        for task in recent[:8]:
            status = task["status"]
            rows.append(
                [
                    time.strftime("%H:%M", time.localtime(task.get("created_at") or time.time())),
                    shorten(task["title"] or task["prompt"], 64),
                    status,
                    task.get("tool_calls", 0),
                ]
            )
            colors.append([None, None, T.STATUS_COLORS.get(status, T.TEXT_SOFT), None])
        self.activity_table.set_rows(rows, colors=colors)

    def _refresh_alerts(self, snap: dict[str, Any]) -> None:
        _clear_layout(self.alerts_body)
        alerts: list[tuple[str, str, str]] = []
        if snap["app"].get("config_warnings"):
            pass  # already surfaced below
        for check in (snap.get("health") or {}).get("checks", []) or []:
            if not check["ok"]:
                alerts.append((check["name"].title(), check["detail"], T.DANGER))
        for task in snap["agent"]["recent"]:
            if task["status"] in ("failed", "blocked") and time.time() - (task.get("finished_at") or task.get("created_at") or 0) < 3600:
                alerts.append(("Task needs attention", shorten(task["title"] or task["prompt"], 70), T.WARNING))
        for run in snap["training"]["recent"]:
            if run["status"] == "failed":
                alerts.append(("Training failed", shorten(run.get("error") or run["name"], 70), T.DANGER))
        autonomy = snap.get("autonomy") or {}
        for proposal in (autonomy.get("pending") or [])[:3]:
            alerts.append(("Autonomy proposal", shorten(proposal.get("title", ""), 70), T.ACCENT))
        storage = snap["storage"]
        if storage.get("usb_categories") and not storage.get("usb_active"):
            alerts.append(("External storage", "USB categories are configured but no drive is mounted; internal storage is in use.", T.WARNING))
        if storage.get("pending_sync"):
            alerts.append(("External storage", f"{storage['pending_sync']} item(s) waiting to sync to the USB drive.", T.ACCENT))
        for warning in snap["app"].get("config_warnings") or []:
            alerts.append(("Settings", shorten(warning, 90), T.WARNING))
        quality = snap.get("quality") or {}
        if quality.get("unverified"):
            names = ", ".join(claim.get("task_id", "")[:8] for claim in (quality.get("unverified_tasks") or [])[:3])
            alerts.append(
                (
                    "Unproven claims",
                    f"{quality['unverified']} completed task(s) without passing evidence: {names} — open them and check the verification.",
                    T.WARNING,
                )
            )
        if not alerts:
            self.alerts_body.addWidget(EmptyState("Nothing needs you", "No failures, no pending approvals, no health problems.", icon="✓"))
        for title, detail, color_value in alerts[:6]:
            self.alerts_body.addWidget(KeyValue(title, detail, value_color=color_value))
        self.footer.setText(
            f"CodeMind running since {time.strftime('%H:%M:%S', time.localtime(snap['app']['uptime'] and time.time() - snap['app']['uptime'] or time.time()))} · "
            f"uptime {human_duration(snap['app']['uptime'])} · database {snap['app']['db_path']} · logs {snap['app']['log_path']}"
        )


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            continue
        child = item.layout()
        if child is not None:
            _clear(child)


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
