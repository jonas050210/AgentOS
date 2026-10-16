"""Application context: one object that owns every subsystem.

The GUI, the CLI and the tests all build this and nothing else. It is responsible for the
order of construction (storage → database → memory → hardware → sandbox → agent → training →
autonomy), for propagating configuration changes to the components that care about them, and
for shutting everything down without leaving orphaned processes behind.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from codemind.agent.executor import AgentExecutor
from codemind.agent.permissions import ApprovalBroker, PermissionPolicy
from codemind.agent.planner import Planner
from codemind.agent.runner import TaskRunner
from codemind.agent.task import TaskStore
from codemind.agent.tools import ToolRegistry, build_default_tools
from codemind.autonomy.scheduler import AutonomyScheduler
from codemind.config import Config, ConfigStore
from codemind.events import BUS, Topics
from codemind.github.client import GithubClient
from codemind.llm.manager import ModelManager
from codemind.logging_setup import configure_logging, get_logger, set_console, set_level
from codemind.memory.knowledge import KnowledgeIndex
from codemind.memory.memory import MemoryStore
from codemind.sandbox.manager import SandboxManager
from codemind.storage.db import Database
from codemind.storage.layout import StorageLayout, default_data_root
from codemind.system.governor import ResourceGovernor
from codemind.system.hardware import HardwareProfile, detect_hardware
from codemind.system.resources import ResourceSampler, current_snapshot, process_usage
from codemind.training.manager import TrainingManager
from codemind.utils.files import human_bytes
from codemind.utils.jobs import JobManager

log = get_logger("app")


@dataclass
class ContextInfo:
    data_root: str
    db_path: str
    log_path: str
    started_at: float
    config_warnings: list[str] = field(default_factory=list)


class CodeMindApp:
    """The composition root."""

    def __init__(self, data_root: str | Path | None = None, *, load_config: bool = True, start_background: bool = True) -> None:
        started = time.time()
        self.data_root = Path(data_root).expanduser() if data_root else default_data_root()
        self.config_store = ConfigStore(self.data_root / "settings.json")
        self.config: Config = self.config_store.load() if load_config else Config()
        # logging first: everything after this can log somewhere real
        log_path = configure_logging(
            self.data_root / "logs",
            level=self.config.logging.level,
            console=self.config.logging.console,
            keep_days=self.config.logging.keep_days,
        )
        self.layout = StorageLayout.create(
            self.data_root,
            usb_override=self.config.storage.usb_path,
            marker=self.config.storage.usb_marker,
            usb_categories=self.config.storage.usb_categories,
            projects_root=self.config.storage.projects_root,
            workspace_root=self.config.sandbox.workspace_root,
        )
        self.db = Database(self.layout.db_path)
        self.memory = MemoryStore(self.db, min_quality=self.config.memory.min_quality, learn_from_failures=self.config.memory.learn_from_failures)
        self.knowledge = KnowledgeIndex(self.db)
        self.hardware: HardwareProfile = HardwareProfile(
            cpu_model="probing…", physical_cores=1, logical_cores=1, cpu_max_mhz=None, ram_total_mb=0, ram_available_mb=0, swap_total_mb=0
        )
        self.governor = ResourceGovernor()
        self.sampler = ResourceSampler(
            interval=self.config.resources.sample_interval,
            disk_path=self.data_root,
            gpu=True,
            persist=self._persist_sample,
        )
        self.jobs = JobManager(self.db, self.data_root / "logs" / "jobs")
        self.sandbox = SandboxManager(
            self.config,
            self.db,
            workspaces_root=self.layout.workspaces_dir(),
            projects_root=self.layout.projects_dir(),
        )
        self.github = GithubClient(self.config, self.db, data_root=self.data_root)
        self.models = ModelManager(self.config, self.hardware)
        self.policy = PermissionPolicy(self.config)
        self.approvals = ApprovalBroker(self.db)
        self.tasks = TaskStore(self.db)
        self.registry = ToolRegistry(build_default_tools())
        self.training = TrainingManager(self.config, self.db, self.layout, self.hardware, governor=self.governor, sampler=self.sampler)
        self.planner = Planner(self.config, self.memory, self.models, advisor=self.training)
        self.executor = AgentExecutor(
            config=self.config,
            db=self.db,
            store=self.tasks,
            registry=self.registry,
            planner=self.planner,
            memory=self.memory,
            knowledge=self.knowledge,
            sandbox=self.sandbox,
            approvals=self.approvals,
            policy=self.policy,
            governor=self.governor,
            model_manager=self.models,
            github=self.github,
            data_root=self.data_root,
        )
        self.runner = TaskRunner(self.executor, self.tasks, self.config, on_finish=self._on_task_finished, governor=self.governor)
        self.autonomy = AutonomyScheduler(
            self.config,
            self.db,
            self.knowledge,
            submit_task=self.submit_task,
            runner_status=self.runner.status,
            governor=self.governor,
        )
        self.info = ContextInfo(
            data_root=str(self.data_root),
            db_path=str(self.layout.db_path),
            log_path=str(log_path),
            started_at=started,
            config_warnings=list(self.config_store.load_errors),
        )
        self._hardware_ready = threading.Event()
        self._config_listeners: list[Callable[[Config, Sequence[str]], None]] = []
        self.config_store.subscribe(self._on_config_change)
        self.sampler.persist = self._persist_sample
        if start_background:
            self.start_background()
        else:
            # no background threads: detect hardware synchronously so every consumer has real data
            self._probe_hardware()
        log.info(
            "CodeMind context ready",
            cm={
                "data_root": str(self.data_root),
                "db": str(self.layout.db_path),
                "usb": str(self.layout.usb_active) if self.layout.usb_active else None,
                "python": self.hardware.python,
            },
        )

    # -- startup ----------------------------------------------------------
    def start_background(self, probe_hardware: bool = True) -> None:
        self.sampler.start()
        if probe_hardware:
            thread = threading.Thread(target=self._probe_hardware, name="codemind-hardware", daemon=True)
            thread.start()
        else:
            self._probe_hardware()
        self.runner.start()
        try:
            adopted = self.training.reconcile_artifacts()
            if adopted:
                log.info("recovered promoted adapter artefacts", cm={"paths": adopted})
        except Exception as exc:  # pragma: no cover
            log.warning("could not reconcile training artefacts", cm={"error": str(exc)})
        self.autonomy.start()
        self._governor_thread = threading.Thread(target=self._governor_loop, name="codemind-governor", daemon=True)
        self._governor_thread.start()

    def _probe_hardware(self) -> None:
        try:
            profile = detect_hardware(extra_paths=[str(self.layout.resolve("models"))])
        except Exception as exc:  # pragma: no cover - detection is already defensive
            log.warning("hardware probe failed", cm={"error": str(exc)})
            return
        self.hardware = profile
        self.models.hardware = profile
        self.training.update_hardware(profile)
        self.runner.set_hardware(profile, self.sampler)
        self.governor.update(profile, self.config, self.sampler.latest, publish=True)
        BUS.publish(Topics.HARDWARE, **profile.as_dict())
        self._hardware_ready.set()

    def wait_for_hardware(self, timeout: float = 90.0) -> bool:
        return self._hardware_ready.wait(timeout)

    def rescan_hardware(self) -> None:
        """Probe the machine again (new GPU driver, extra disk, different USB drive)."""
        self._hardware_ready.clear()
        thread = threading.Thread(target=self._probe_hardware, name="codemind-hardware-rescan", daemon=True)
        thread.start()

    # -- evidence ---------------------------------------------------------
    def quality_report(self, limit: int = 200) -> dict[str, Any]:
        """How much of the reported work is actually proven (see codemind.report.quality)."""
        from codemind.report.quality import honesty_report

        return honesty_report(self, limit=limit)

    def write_passport(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        """Write the change passport of a task (see codemind.report.passport)."""
        from codemind.report.passport import write_passport

        return write_passport(self, task_id, **kwargs)

    def rollback_task(self, task_id: str, *, force: bool = False) -> dict[str, Any]:
        """Restore the files a task changed to their pre-task content."""
        from codemind.agent.rollback import apply_rollback

        return apply_rollback(self, task_id, force=force)

    def rollback_plan(self, task_id: str) -> dict[str, Any]:
        from codemind.agent.rollback import plan_rollback

        return plan_rollback(self, task_id)

    def _governor_loop(self) -> None:
        while not getattr(self, "_stopped", False):
            interval = max(2.0, self.config.resources.sample_interval)
            time.sleep(interval)
            try:
                status = self.runner.status()
                self.governor.update(
                    self.hardware,
                    self.config,
                    self.sampler.latest,
                    active_tasks=len(status.active),
                    queued_tasks=status.queued,
                    training_runs=len(self.training.engine.active()),
                )
            except Exception as exc:  # pragma: no cover
                log.debug("governor loop error", cm={"error": str(exc)})

    # -- configuration ----------------------------------------------------
    def update_settings(self, values: dict[str, Any]) -> list[str]:
        changed = self.config_store.update(values)
        if changed:
            self.config = self.config_store.config
            self._apply_config(changed)
        return changed

    def reset_section(self, section: str) -> None:
        self.config_store.reset_section(section)
        self.config = self.config_store.config
        self._apply_config([f"{section}.*"])

    def _on_config_change(self, config: Config, changed: Sequence[str]) -> None:
        self.config = config
        self._apply_config(changed)

    def _apply_config(self, changed: Sequence[str]) -> None:
        self.sampler.interval = max(0.5, self.config.resources.sample_interval)
        if any(key.startswith("app.") for key in changed):
            # the appearance belongs to the settings, not to the window: consumers outside the GUI
            # (rendering tools, tests) must see the same colours without an event loop running
            try:
                from codemind.ui import theme as ui_theme

                ui_theme.apply_appearance(self.config)
            except Exception:  # pragma: no cover - a headless install without a usable Qt
                pass
        self.memory.min_quality = self.config.memory.min_quality
        self.memory.learn_from_failures = self.config.memory.learn_from_failures
        self.sandbox.update_config(self.config)
        self.github.update_config(self.config)
        self.models.update_config(self.config)
        self.policy.update_config(self.config)
        self.training.update_config(self.config)
        self.planner.update_config(self.config)
        self.executor.update_config(self.config)
        self.autonomy.update_config(self.config)
        if any(key.startswith("logging.") for key in changed):
            set_level(self.config.logging.level)
            set_console(self.config.logging.console)
        if any(key.startswith("resources.") for key in changed):
            self.governor.update(self.hardware, self.config, self.sampler.latest)
        if any(key == "storage.usb_path" or key.startswith("storage.") for key in changed):
            self.layout.usb_override = self.config.storage.usb_path
            self.layout.usb_categories = tuple(self.config.storage.usb_categories)
            self.layout.scan(force=True)
        if any(key.startswith("agent.") for key in changed):
            self.runner.update_config(self.config)
        BUS.publish(Topics.SETTINGS_CHANGED, changed=list(changed))
        for listener in list(self._config_listeners):
            try:
                listener(self.config, changed)
            except Exception:  # pragma: no cover
                pass
        log.info("settings updated", cm={"changed": list(changed)[:10]})

    def on_config_change(self, listener: Callable[[Config, Sequence[str]], None]) -> Callable[[], None]:
        self._config_listeners.append(listener)
        return lambda: self._config_listeners.remove(listener) if listener in self._config_listeners else None

    # -- tasks ------------------------------------------------------------
    def submit_task(
        self,
        prompt: str,
        *,
        project_path: str = "",
        title: str = "",
        mode: str = "assisted",
        origin: str = "user",
        priority: int = 5,
        autonomy: str = "",
    ) -> Any:
        return self.runner.submit(
            prompt,
            project_path=project_path,
            title=title,
            mode=mode,
            origin=origin,
            priority=priority,
            autonomy=autonomy,
        )

    def _on_task_finished(self, task: Any, outcome: Any) -> None:
        """Collect training traces from finished tasks and refresh project knowledge."""
        try:
            report = self.training.collector.collect(task, outcome)
            if report.accepted:
                log.info("collected task traces", cm=report.as_dict())
                self.tasks.log_event(task.id, "training-data", f"{report.accepted} training example(s) collected (quality-gated).")
            elif report.reasons:
                self.tasks.log_event(task.id, "training-data", f"No training examples collected: {report.reasons[0]}")
        except Exception as exc:  # pragma: no cover - collection must not break the run
            log.warning("trace collection failed", cm={"error": str(exc)})
        if task.project_path:
            try:
                project = self.knowledge.project_by_path(task.project_path)
                if project is None:
                    self.knowledge.register_project(task.project_path)
                self.layout.prune_workspaces()
            except Exception as exc:  # pragma: no cover
                log.debug("post-task bookkeeping failed", cm={"error": str(exc)})

    # -- persistence helpers ---------------------------------------------
    def _persist_sample(self, snapshot: Any) -> None:
        try:
            self.db.insert(
                "resource_samples",
                {
                    "ts": snapshot.ts,
                    "cpu_percent": snapshot.cpu_percent,
                    "ram_used_mb": snapshot.ram_used_mb,
                    "ram_total_mb": snapshot.ram_total_mb,
                    "gpu_util": snapshot.gpu_util,
                    "vram_used_mb": snapshot.vram_used_mb,
                    "vram_total_mb": snapshot.vram_total_mb,
                    "disk_free_gb": snapshot.disk_free_gb,
                    "source": snapshot.source,
                },
            )
        except Exception as exc:  # pragma: no cover
            log.debug("could not persist resource sample", cm={"error": str(exc)})

    def sample_history(self, minutes: int = 30) -> list[dict[str, Any]]:
        cutoff = time.time() - minutes * 60
        rows = self.db.query(
            "SELECT ts, cpu_percent, ram_used_mb, ram_total_mb, gpu_util, vram_used_mb, vram_total_mb FROM resource_samples WHERE ts >= ? ORDER BY ts",
            (cutoff,),
        )
        return [dict(row) for row in rows]

    # -- snapshots for the UI --------------------------------------------
    def overview_snapshot(self) -> dict[str, Any]:
        status = self.runner.status()
        tasks = self.tasks.list(limit=12)
        active_training = [run.as_dict() for run in self.training.engine.active()]
        active_task = next((task for task in tasks if task.id in status.active), None)
        backend = self.models.active_backend_info()
        isolation = self.sandbox.status()
        governor = self.governor.plan
        db_stats = self.db.stats()
        memory_stats = self.memory.stats()
        training_stats = self.training.engine.stats()
        return {
            "hardware": self.hardware.as_dict(),
            "hardware_ready": self._hardware_ready.is_set(),
            "resources": self.sampler.latest.as_dict(),
            "resource_history": self.sampler.series("cpu_percent", 120),
            "ram_history": self.sampler.series("ram_used_mb", 120),
            "governor": governor.as_dict() if governor else None,
            "agent": {
                "runner": status.as_dict(),
                "active_task": active_task.as_dict() if active_task else None,
                "queue": [task.as_dict(include_steps=False) for task in tasks if task.status == "queued"],
                "recent": [task.as_dict(include_steps=False) for task in tasks],
                "counts": self.tasks.counts(),
                "backend": backend,
                "approvals_pending": [request.as_dict() for request in self.approvals.pending()],
                "jobs": self.jobs.jobs(limit=8),
                "tools": len(self.registry.names()),
            },
            "training": {
                "stats": training_stats,
                "active": active_training,
                "recent": [run.as_dict() for run in self.training.engine.list(5)],
                "active_adapter": self.training.active_version(),
                "advisor_loaded": self.training.advisor is not None,
            },
            "quality": self.quality_report(limit=60),
            "memory": memory_stats,
            "knowledge": self.knowledge.stats(),
            "storage": self.layout.summary(),
            "database": db_stats,
            "sandbox": {
                "isolation": isolation["isolation"],
                "workspace_count": isolation["workspace_count"],
                "workspace_bytes": isolation["workspace_bytes"],
                "history": isolation["history"],
            },
            "github": self.github.status(),
            "autonomy": self.autonomy.stats(),
            "app": {
                "data_root": str(self.data_root),
                "db_path": str(self.layout.db_path),
                "log_path": self.info.log_path,
                "uptime": round(time.time() - self.info.started_at, 1),
                "version": self.hardware.python,
                "config_warnings": self.info.config_warnings,
                "memory_bytes": self._process_memory(),
            },
        }

    def _process_memory(self) -> dict[str, Any]:
        import os

        usage = process_usage(os.getpid())
        return {"rss_mb": usage.get("rss_mb", 0), "threads": usage.get("threads", 0), "cpu_percent": usage.get("cpu_percent", 0.0)}

    def health(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        checks.append({"name": "database", "ok": self.db.integrity_check() == "ok", "detail": self.db.integrity_check()})
        checks.append({"name": "storage", "ok": self.data_root.exists(), "detail": str(self.data_root)})
        checks.append({"name": "usb", "ok": True, "detail": str(self.layout.usb_active) if self.layout.usb_active else "not attached (internal storage in use)"})
        backend = self.models.active_backend_info()
        checks.append({"name": "inference", "ok": True, "detail": f"{backend['name']} — {backend['reason']}"})
        isolation = self.sandbox.isolation
        checks.append({"name": "sandbox", "ok": True, "detail": f"{isolation.level}: {isolation.summary()}"})
        jobs = self.jobs.stats()
        checks.append({"name": "background jobs", "ok": True, "detail": f"{jobs['running']} running, {jobs['failed']} failed, logs in {jobs['log_dir']}"})
        checks.append({"name": "training", "ok": True, "detail": f"{'enabled' if self.config.training.enabled else 'disabled'}; auto method {self.training.environment()['auto_method']}"})
        token = self.github.token_status()
        checks.append({"name": "github", "ok": token.configured, "detail": token.detail})
        return {
            "ok": all(check["ok"] for check in checks),
            "checks": checks,
            "uptime": round(time.time() - self.info.started_at, 1),
            "python": self.hardware.python,
        }

    # -- shutdown ---------------------------------------------------------
    def shutdown(self) -> None:
        self._stopped = True
        log.info("shutting down CodeMind")
        BUS.publish(Topics.SHUTDOWN)
        try:
            self.approvals.deny_all("application is shutting down")
        except Exception:
            pass
        for component in (self.autonomy, self.runner, self.training, self.jobs, self.models, self.sampler):
            try:
                component.shutdown() if hasattr(component, "shutdown") else component.stop()
            except Exception as exc:  # pragma: no cover
                log.debug("component shutdown failed", cm={"component": type(component).__name__, "error": str(exc)})
        try:
            self.db.prune(executions_days=30, samples_minutes=max(240, self.config.resources.history_minutes * 2))
        except Exception:
            pass
        self.db.close()
        log.info("CodeMind stopped")
