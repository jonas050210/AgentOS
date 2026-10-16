"""The task runner: queue, workers, cancellation and resume.

One runner owns the pool. Tasks are picked by priority and age, exactly ``max_parallel_tasks``
run at a time, and every task gets its own workspace and its own executor context. Training is
*yielded* rather than starved: the runner reports how many tasks are active/queued so the
governor can pause training while you are waiting for an edit.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from codemind.agent.executor import AgentExecutor, TaskOutcome
from codemind.agent.task import Task, TaskStatus, TaskStore
from codemind.config import Config
from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger

log = get_logger("agent.runner")


@dataclass
class RunnerStatus:
    running: bool
    active: list[str] = field(default_factory=list)
    queued: int = 0
    completed: int = 0
    failed: int = 0
    workers: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "active": list(self.active),
            "active_count": len(self.active),
            "queued": self.queued,
            "completed": self.completed,
            "failed": self.failed,
            "workers": self.workers,
        }


class TaskRunner:
    """Owns the worker threads and the task lifecycle."""

    def __init__(
        self,
        executor: AgentExecutor,
        store: TaskStore,
        config: Config,
        *,
        on_finish: Callable[[Task, TaskOutcome], None] | None = None,
        governor: Any = None,
    ) -> None:
        self.executor = executor
        self.store = store
        self.config = config
        self.on_finish = on_finish
        self.governor = governor
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._active: dict[str, Task] = {}
        self._completed = 0
        self._failed = 0
        self._resume_flags: dict[str, bool] = {}
        self._running = False

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop.clear()
            workers = max(1, min(4, self.config.agent.max_parallel_tasks))
            for index in range(workers):
                thread = threading.Thread(target=self._worker, name=f"codemind-task-{index}", daemon=True)
                thread.start()
                self._threads.append(thread)
        log.info("task runner started", cm={"workers": len(self._threads)})
        self._publish()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        for thread in list(self._threads):
            thread.join(timeout=timeout)
        self._threads.clear()
        self._running = False
        log.info("task runner stopped")

    def update_config(self, config: Config) -> None:
        self.config = config
        self.executor.update_config(config)
        # adjust worker count on the next start; existing workers finish their tasks
        if self._running and len(self._threads) != max(1, min(4, config.agent.max_parallel_tasks)):
            self.restart()

    def restart(self) -> None:
        self.stop()
        self.start()

    # -- submission -------------------------------------------------------
    def submit(
        self,
        prompt: str,
        *,
        project_path: str = "",
        title: str = "",
        mode: str = "assisted",
        origin: str = "user",
        priority: int = 5,
        autonomy: str = "",
        auto_start: bool = True,
    ) -> Task:
        task = self.store.create(
            prompt,
            title=title,
            project_path=project_path,
            mode=mode,
            origin=origin,
            priority=priority,
            autonomy=autonomy or self.config.agent.autonomy,
        )
        if auto_start:
            self.wake()
        return task

    def wake(self) -> None:
        self._wake.set()

    def cancel(self, task_id: str) -> bool:
        task = self.store.get(task_id, with_steps=False)
        if task is None:
            return False
        if task.status == TaskStatus.QUEUED:
            self.store.set_status(task_id, TaskStatus.CANCELLED, summary="Cancelled before it started.")
            self._publish()
            return True
        if task_id in self._active or task.status in TaskStatus.ACTIVE:
            self.store.set_status(task_id, TaskStatus.CANCELLING)
            self.executor.cancel(task_id)
            return True
        return False

    def pause(self, task_id: str) -> bool:
        """Pause after the current step: the plan and evidence are kept for resuming."""
        task = self.store.get(task_id, with_steps=False)
        if task is None or task.status not in TaskStatus.ACTIVE:
            return False
        self._resume_flags[task_id] = True
        self.cancel(task_id)
        return True

    def resume(self, task_id: str) -> bool:
        task = self.store.get(task_id, with_steps=False)
        if task is None:
            return False
        if task.status not in (TaskStatus.INTERRUPTED, TaskStatus.CANCELLED, TaskStatus.PAUSED, TaskStatus.BLOCKED, TaskStatus.FAILED):
            return False
        self._resume_flags[task_id] = True
        self.store.set_status(task_id, TaskStatus.QUEUED)
        self.store.log_event(task_id, "resume", "Task re-queued to continue from its stored plan.")
        self.wake()
        return True

    def retry(self, task_id: str) -> bool:
        """Re-run a finished task from scratch (fresh workspace, fresh plan)."""
        task = self.store.get(task_id, with_steps=False)
        if task is None:
            return False
        self.store.update(task_id, plan={}, result={}, verify={}, error="", summary="", workspace_id=None)
        self.store.db.execute("DELETE FROM task_steps WHERE task_id = ?", (task_id,))
        self._resume_flags.pop(task_id, None)
        self.store.set_status(task_id, TaskStatus.QUEUED)
        self.store.log_event(task_id, "retry", "Task re-queued for a fresh attempt.")
        self.wake()
        return True

    # -- worker -----------------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            task = self._next_task()
            if task is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            resume = bool(self._resume_flags.pop(task.id, False)) or task.status == TaskStatus.INTERRUPTED
            with self._lock:
                self._active[task.id] = task
            self._publish()
            try:
                outcome = self.executor.run(task, resume=resume)
                with self._lock:
                    if outcome.status == TaskStatus.COMPLETED:
                        self._completed += 1
                    else:
                        self._failed += 1
                if self.on_finish is not None:
                    try:
                        fresh = self.store.get(task.id)
                        self.on_finish(fresh or task, outcome)
                    except Exception as exc:  # pragma: no cover - hook isolation
                        log.warning("task finish hook failed", cm={"error": str(exc)})
            except Exception as exc:  # pragma: no cover - executor already handles this
                log.exception("task worker crashed", cm={"task": task.id})
                self.store.set_status(task.id, TaskStatus.FAILED, error=f"worker error: {exc}")
                with self._lock:
                    self._failed += 1
            finally:
                with self._lock:
                    self._active.pop(task.id, None)
                self._publish()
        return

    def _next_task(self) -> Task | None:
        with self._lock:
            capacity = max(1, self.config.agent.max_parallel_tasks) - len(self._active)
        if capacity <= 0:
            return None
        rows = self.store.db.query(
            "SELECT id FROM tasks WHERE status = ? ORDER BY priority ASC, created_at ASC LIMIT 1",
            (TaskStatus.QUEUED,),
        )
        if not rows:
            return None
        return self.store.get(rows[0]["id"], with_steps=False)

    # -- reporting --------------------------------------------------------
    def status(self) -> RunnerStatus:
        with self._lock:
            active = list(self._active)
            completed, failed = self._completed, self._failed
        queued = int(self.store.db.scalar("SELECT COUNT(*) FROM tasks WHERE status = ?", (TaskStatus.QUEUED,), default=0) or 0)
        return RunnerStatus(
            running=self._running,
            active=active,
            queued=queued,
            completed=completed,
            failed=failed,
            workers=max(1, len(self._threads)),
        )

    def _publish(self) -> None:
        status = self.status()
        BUS.publish(Topics.TASK_UPDATED, runner=status.as_dict())
        if self.governor is not None and getattr(self, "_hardware", None) is not None:
            try:
                self.governor.update(
                    self._hardware,
                    self.config,
                    self._sampler.latest if getattr(self, "_sampler", None) else None,
                    active_tasks=len(status.active),
                    queued_tasks=status.queued,
                )
            except Exception as exc:  # pragma: no cover - governor is optional
                log.debug("governor update failed", cm={"error": str(exc)})

    def set_hardware(self, hardware: Any, sampler: Any = None) -> None:
        self._hardware = hardware
        self._sampler = sampler

    def queue_snapshot(self, limit: int = 20) -> list[dict[str, Any]]:
        return [task.as_dict(include_steps=False) for task in self.store.list(limit=limit)]
