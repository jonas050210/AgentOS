"""Task model, state machine and persistence.

A task is the unit of work the user cares about: what was asked, what the agent planned, what
it actually did, what changed and whether it was verified. All of that survives a restart,
which is what makes "resume" meaningful rather than cosmetic.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.storage.db import Database, _loads

log = get_logger("agent.task")


class TaskStatus:
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    TERMINAL = (COMPLETED, FAILED, CANCELLED, BLOCKED)
    ACTIVE = (PLANNING, RUNNING, WAITING_APPROVAL, VERIFYING, CANCELLING, PAUSED)

    LABELS = {
        QUEUED: "Queued",
        PLANNING: "Planning",
        RUNNING: "Working",
        WAITING_APPROVAL: "Waiting for approval",
        PAUSED: "Paused",
        CANCELLING: "Cancelling",
        VERIFYING: "Verifying",
        COMPLETED: "Completed",
        FAILED: "Failed",
        BLOCKED: "Blocked",
        CANCELLED: "Cancelled",
        INTERRUPTED: "Interrupted",
    }


class StepState:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_MODEL = "needs_model"
    WAITING_APPROVAL = "waiting_approval"

    LABELS = {
        PENDING: "Pending",
        RUNNING: "Running",
        DONE: "Done",
        FAILED: "Failed",
        SKIPPED: "Skipped",
        NEEDS_MODEL: "Needs a model",
        WAITING_APPROVAL: "Waiting for approval",
    }


@dataclass
class Step:
    idx: int
    title: str
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    intent: str = ""
    detail: str = ""
    verify: str = ""
    status: str = StepState.PENDING
    attempts: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    origin: str = "plan"  # plan | repair

    def as_dict(self) -> dict[str, Any]:
        return {
            "idx": self.idx,
            "title": self.title,
            "tool": self.tool,
            "arguments": self.arguments,
            "intent": self.intent,
            "detail": self.detail,
            "verify": self.verify,
            "status": self.status,
            "status_label": StepState.LABELS.get(self.status, self.status),
            "attempts": self.attempts,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "evidence": self.evidence,
            "origin": self.origin,
        }

    @classmethod
    def from_row(cls, row: Any) -> "Step":
        payload = _loads(row["result"], {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        return cls(
            idx=int(row["idx"]),
            title=row["title"],
            tool=row["tool"] or "",
            arguments=payload.get("arguments") or {},
            detail=row["detail"] or "",
            status=row["status"],
            attempts=int(row["attempts"] or 0),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            result=payload.get("result"),
            error=row["error"] or "",
            evidence=_loads(row["evidence"], []) or [],
            origin=payload.get("origin") or "plan",
        )


@dataclass
class Task:
    id: str
    title: str
    prompt: str
    project_path: str = ""
    project_id: int | None = None
    mode: str = "assisted"
    origin: str = "user"
    status: str = TaskStatus.QUEUED
    priority: int = 5
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    workspace_id: str = ""
    workspace_path: str = ""
    plan: dict[str, Any] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    error: str = ""
    verify: dict[str, Any] = field(default_factory=dict)
    tool_calls: int = 0
    tokens: int = 0
    attempts: int = 0
    autonomy: str = ""

    @property
    def label(self) -> str:
        return TaskStatus.LABELS.get(self.status, self.status)

    @property
    def is_active(self) -> bool:
        return self.status in TaskStatus.ACTIVE

    @property
    def is_terminal(self) -> bool:
        return self.status in TaskStatus.TERMINAL

    def as_dict(self, include_steps: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "project_path": self.project_path,
            "project_id": self.project_id,
            "mode": self.mode,
            "origin": self.origin,
            "status": self.status,
            "status_label": self.label,
            "priority": self.priority,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": (self.finished_at - self.started_at) if (self.finished_at and self.started_at) else None,
            "workspace_id": self.workspace_id,
            "workspace_path": self.workspace_path,
            "plan": self.plan,
            "result": self.result,
            "summary": self.summary,
            "error": self.error,
            "verify": self.verify,
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "autonomy": self.autonomy,
            "step_counts": {
                "total": len(self.steps),
                "done": sum(1 for s in self.steps if s.status == StepState.DONE),
                "failed": sum(1 for s in self.steps if s.status == StepState.FAILED),
                "pending": sum(1 for s in self.steps if s.status in (StepState.PENDING, StepState.RUNNING)),
            },
        }
        if include_steps:
            payload["steps"] = [step.as_dict() for step in self.steps]
        return payload


class TaskStore:
    """Persistence and query API for tasks (SQLite backed)."""

    def __init__(self, db: Database, *, recovery: bool = True) -> None:
        self.db = db
        if recovery:
            self.recover_interrupted()

    # -- creation ---------------------------------------------------------
    def create(
        self,
        prompt: str,
        *,
        title: str = "",
        project_path: str = "",
        project_id: int | None = None,
        mode: str = "assisted",
        origin: str = "user",
        priority: int = 5,
        autonomy: str = "",
        task_id: str = "",
    ) -> Task:
        task = Task(
            id=task_id or uuid.uuid4().hex[:12],
            title=(title or _derive_title(prompt)),
            prompt=prompt.strip(),
            project_path=str(project_path or ""),
            project_id=project_id,
            mode=mode,
            origin=origin,
            priority=priority,
            autonomy=autonomy,
        )
        self.db.insert(
            "tasks",
            {
                "id": task.id,
                "project_id": project_id,
                "title": task.title,
                "prompt": task.prompt,
                "mode": task.mode,
                "origin": task.origin,
                "status": task.status,
                "priority": task.priority,
                "created_at": task.created_at,
                "workdir": task.project_path or None,
            },
        )
        BUS.publish(Topics.TASK_CREATED, task_id=task.id, title=task.title, project=task.project_path, origin=origin)
        log.info("task created", cm={"task": task.id, "title": task.title, "project": task.project_path})
        return task

    # -- reads ------------------------------------------------------------
    def get(self, task_id: str, *, with_steps: bool = True) -> Task | None:
        row = self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if row is None:
            return None
        task = Task(
            id=row["id"],
            title=row["title"],
            prompt=row["prompt"],
            project_path=row["workdir"] or "",
            project_id=row["project_id"],
            mode=row["mode"],
            origin=row["origin"],
            status=row["status"],
            priority=int(row["priority"] or 5),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            plan=_loads(row["plan"], {}) or {},
            result=_loads(row["result"], {}) or {},
            summary=row["summary"] or "",
            error=row["error"] or "",
            verify=_loads(row["verify"], {}) or {},
            tool_calls=int(row["tool_calls"] or 0),
            tokens=int(row["tokens"] or 0),
            attempts=int(row["attempts"] or 0),
        )
        task.autonomy = (task.result or {}).get("autonomy", "")
        if with_steps:
            task.steps = self.steps(task_id)
            workspace = (task.result or {}).get("workspace") or {}
            task.workspace_id = workspace.get("id", "")
            task.workspace_path = workspace.get("path", "")
        return task

    def steps(self, task_id: str) -> list[Step]:
        rows = self.db.query("SELECT * FROM task_steps WHERE task_id = ? ORDER BY idx", (task_id,))
        return [Step.from_row(row) for row in rows]

    def list(self, *, status: str | None = None, limit: int = 50, project_id: int | None = None, origin: str | None = None) -> list[Task]:
        clauses: list[str] = []
        params: list[Any] = []
        if status == "active":
            clauses.append(f"status IN ({','.join('?' for _ in TaskStatus.ACTIVE)})")
            params.extend(TaskStatus.ACTIVE)
        elif status == "history":
            clauses.append(f"status IN ({','.join('?' for _ in TaskStatus.TERMINAL + (TaskStatus.INTERRUPTED,))})")
            params.extend(TaskStatus.TERMINAL + (TaskStatus.INTERRUPTED,))
        elif status:
            clauses.append("status = ?")
            params.append(status)
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if origin:
            clauses.append("origin = ?")
            params.append(origin)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT id FROM tasks {where} ORDER BY "
            "CASE WHEN status IN ('running','planning','verifying') THEN 0 WHEN status = 'queued' THEN 1 ELSE 2 END, "
            "priority ASC, created_at DESC LIMIT ?",
            [*params, limit],
        )
        tasks: list[Task] = []
        for row in rows:
            task = self.get(row["id"], with_steps=True)
            if task:
                tasks.append(task)
        return tasks

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self.db.query("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"):
            out[row["status"]] = int(row["n"])
        out["total"] = sum(out.values())
        out["active"] = sum(out.get(status, 0) for status in TaskStatus.ACTIVE)
        return out

    def events(self, task_id: str, limit: int = 400) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM task_events WHERE task_id = ? ORDER BY ts DESC LIMIT ?", (task_id, limit))
        return [
            {
                "id": row["id"],
                "ts": row["ts"],
                "kind": row["kind"],
                "message": row["message"],
                "payload": _loads(row["payload"], {}) or {},
            }
            for row in reversed(rows)
        ]

    def diffs(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM task_diffs WHERE task_id = ? ORDER BY ts", (task_id,))
        return [
            {
                "ts": row["ts"],
                "path": row["path"],
                "kind": row["kind"],
                "diff": row["diff"],
                "added": row["added"],
                "removed": row["removed"],
            }
            for row in rows
        ]

    # -- writes -----------------------------------------------------------
    def update(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        payload: dict[str, Any] = {}
        for key, value in fields.items():
            if key in ("plan", "result", "verify"):
                payload[key] = json.dumps(value, default=str)
            else:
                payload[key] = value
        self.db.update("tasks", payload, "id = ?", (task_id,))
        BUS.publish(Topics.TASK_UPDATED, task_id=task_id, fields=sorted(payload))

    def set_status(self, task_id: str, status: str, *, error: str = "", summary: str = "") -> None:
        payload: dict[str, Any] = {"status": status}
        if status == TaskStatus.RUNNING:
            payload["started_at"] = time.time()
        if status in TaskStatus.TERMINAL or status == TaskStatus.INTERRUPTED:
            payload["finished_at"] = time.time()
        if error:
            payload["error"] = error[:4000]
        if summary:
            payload["summary"] = summary
        self.update(task_id, **payload)
        BUS.publish(Topics.TASK_UPDATED, task_id=task_id, status=status)

    def save_plan(self, task_id: str, plan: dict[str, Any]) -> list[Step]:
        steps = plan_to_steps(plan)
        self.update(task_id, plan=plan)
        with self.db.transaction() as conn:
            for step in steps:
                conn.execute(
                    """
                    INSERT INTO task_steps (task_id, idx, title, detail, status, tool, result)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id, idx) DO UPDATE SET
                        title=excluded.title, detail=excluded.detail, tool=excluded.tool
                    """,
                    (
                        task_id,
                        step.idx,
                        step.title,
                        step.detail or step.intent,
                        step.status,
                        step.tool,
                        json.dumps({"arguments": step.arguments}, default=str),
                    ),
                )
        BUS.publish(Topics.TASK_PLAN, task_id=task_id, plan=plan, steps=[step.as_dict() for step in steps])
        log.info("plan saved", cm={"task": task_id, "steps": len(steps), "planner": plan.get("planner")})
        return steps

    def add_step(self, task_id: str, step: Step) -> Step:
        self.db.upsert(
            "task_steps",
            {
                "task_id": task_id,
                "idx": step.idx,
                "title": step.title,
                "detail": step.detail or step.intent,
                "status": step.status,
                "tool": step.tool,
                "result": json.dumps({"arguments": step.arguments}, default=str),
            },
            ["task_id", "idx"],
        )
        BUS.publish(Topics.TASK_STEP, task_id=task_id, step=step.as_dict())
        return step

    def update_step(self, task_id: str, step: Step) -> None:
        self.db.execute(
            """
            UPDATE task_steps SET status = ?, attempts = ?, started_at = ?, finished_at = ?, result = ?, error = ?, evidence = ?
            WHERE task_id = ? AND idx = ?
            """,
            (
                step.status,
                step.attempts,
                step.started_at,
                step.finished_at,
                json.dumps(
                    {"arguments": step.arguments, "result": step.result, "origin": step.origin},
                    default=str,
                ),
                step.error[:4000],
                json.dumps(step.evidence, default=str),
                task_id,
                step.idx,
            ),
        )
        BUS.publish(Topics.TASK_STEP, task_id=task_id, step=step.as_dict())

    def log_event(self, task_id: str, kind: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self.db.insert(
            "task_events",
            {
                "task_id": task_id,
                "ts": time.time(),
                "kind": kind,
                "message": message[:2000],
                "payload": json.dumps(payload or {}, default=str)[:8000],
            },
        )
        BUS.publish(Topics.TASK_LOG, task_id=task_id, kind=kind, message=message, payload=payload or {})

    # -- lifecycle --------------------------------------------------------
    def recover_interrupted(self) -> list[str]:
        """Mark tasks that were running when the process died so they can be resumed."""
        rows = self.db.query(
            f"SELECT id, title FROM tasks WHERE status IN ({','.join('?' for _ in TaskStatus.ACTIVE)})",
            TaskStatus.ACTIVE,
        )
        recovered: list[str] = []
        for row in rows:
            self.db.update("tasks", {"status": TaskStatus.INTERRUPTED, "finished_at": time.time()}, "id = ?", (row["id"],))
            self.log_event(row["id"], "recovery", "Task was interrupted by a restart and can be resumed.")
            recovered.append(row["id"])
        if recovered:
            log.info("recovered interrupted tasks", cm={"count": len(recovered), "ids": recovered[:10]})
        return recovered

    def delete(self, task_id: str) -> None:
        self.db.execute("DELETE FROM task_steps WHERE task_id = ?", (task_id,))
        self.db.execute("DELETE FROM task_events WHERE task_id = ?", (task_id,))
        self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def prune(self, keep: int = 500) -> int:
        rows = self.db.query(
            f"SELECT id FROM tasks WHERE status IN ({','.join('?' for _ in TaskStatus.TERMINAL)}) "
            "ORDER BY finished_at DESC LIMIT -1 OFFSET ?",
            [*TaskStatus.TERMINAL, keep],
        )
        for row in rows:
            self.delete(row["id"])
        return len(rows)

    def stats(self) -> dict[str, Any]:
        counts = self.counts()
        duration = self.db.scalar(
            "SELECT AVG(finished_at - started_at) FROM tasks WHERE finished_at IS NOT NULL AND started_at IS NOT NULL",
            default=0.0,
        )
        tool_calls = int(self.db.scalar("SELECT COUNT(*) FROM tool_calls", default=0) or 0)
        return {
            "counts": counts,
            "avg_duration": round(float(duration or 0.0), 1),
            "tool_calls": tool_calls,
        }


def plan_to_steps(plan: dict[str, Any]) -> list[Step]:
    steps: list[Step] = []
    for index, raw in enumerate(plan.get("steps") or []):
        if not isinstance(raw, dict):
            continue
        steps.append(
            Step(
                idx=index,
                title=str(raw.get("title") or raw.get("intent") or f"Step {index + 1}"),
                tool=str(raw.get("tool") or ""),
                arguments=dict(raw.get("arguments") or {}),
                intent=str(raw.get("intent") or ""),
                detail=str(raw.get("detail") or ""),
                verify=str(raw.get("verify") or ""),
            )
        )
    return steps


def _derive_title(prompt: str, limit: int = 70) -> str:
    text = " ".join((prompt or "").split())
    if not text:
        return "Untitled task"
    first = text.split(". ")[0]
    return (first[: limit - 1] + "…") if len(first) > limit else first
