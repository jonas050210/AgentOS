"""Roll back the file changes of a task — for real, not just in the UI.

Every write the agent performs goes through :class:`codemind.agent.tools.FileJournal`, which stores
the pre-image of each touched file (full text, plus whether the file existed at all). This module
turns those pre-images back into files: the earliest pre-image per path wins, files that did not
exist are removed again, and every restored path is reported.

Deliberate limits, because a rollback is a destructive operation:

* a task that is still queued, running or repairing is refused — the agent would immediately fight
  the restoration;
* paths outside the task's workspace/project are refused (no writing anywhere else on disk);
* if a file changed *after* the task finished (a human edited it), the rollback still happens but
  the file is reported as ``modified_since`` so nothing is silently lost.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger

log = get_logger("agent.rollback")

ACTIVE_STATUSES = ("queued", "planning", "running", "verifying", "repairing", "cancelling")


class RollbackRefused(RuntimeError):
    """Raised when a rollback would be unsafe or pointless."""


def _paths(app: Any, task: Any) -> list[Path]:
    """Where a rollback is allowed to write: the project plus its sandbox workspaces."""
    allowed: list[Path] = []
    for raw in (getattr(task, "project_path", "") or "",):
        if raw:
            allowed.append(Path(str(raw)))
    try:
        for workspace in app.sandbox.list_workspaces():
            root = getattr(workspace, "path", None) or getattr(workspace, "root", None)
            if root:
                allowed.append(Path(str(root)))
    except Exception as exc:  # pragma: no cover - sandbox listing is defensive
        log.debug("could not list workspaces", cm={"error": str(exc)})
    return allowed


def _inside(path: Path, roots: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            base = Path(root).resolve()
        except OSError:
            continue
        if resolved == base or base in resolved.parents:
            return True
    return False


def preimages(app: Any, task_id: str) -> list[dict[str, Any]]:
    """The earliest recorded pre-image of every path a task touched, in write order."""
    rows = app.db.query(
        "SELECT id, path, existed, content, ts FROM task_backups WHERE task_id = ? ORDER BY id",
        (task_id,),
    )
    first: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = str(row["path"])
        if path not in first:
            first[path] = {
                "path": path,
                "existed": bool(row["existed"]),
                "content": row["content"] if row["content"] is not None else "",
                "ts": row["ts"],
            }
    return list(first.values())


def plan_rollback(app: Any, task_id: str) -> dict[str, Any]:
    """Describe what a rollback would do — used by the UI before anything is written."""
    task = app.tasks.get(task_id)
    if task is None:
        raise RollbackRefused(f"unknown task {task_id!r}")
    if task.status in ACTIVE_STATUSES:
        raise RollbackRefused(f"task {task_id[:8]} is still {task.status}; cancel it before rolling back")
    snapshots = preimages(app, task_id)
    diffs = app.tasks.diffs(task_id)
    files = {entry["path"]: entry for entry in diffs}
    changes: list[dict[str, Any]] = []
    for snap in snapshots:
        path = Path(snap["path"])
        exists_now = path.exists()
        current = ""
        modified_since = False
        if exists_now:
            try:
                current = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                current = ""
            modified_since = current != _expected_final(diffs, snap["path"])
        changes.append(
            {
                "path": snap["path"],
                "exists": exists_now,
                "existed_before": snap["existed"],
                "will_delete": not snap["existed"],
                "will_restore": bool(snap["existed"]),
                "modified_since_task": modified_since,
                "added": (files.get(snap["path"]) or {}).get("added"),
                "removed": (files.get(snap["path"]) or {}).get("removed"),
            }
        )
    return {
        "task_id": task.id,
        "title": task.title,
        "status": task.status,
        "project_path": task.project_path,
        "files": changes,
        "count": len(changes),
        "deletes": sum(1 for change in changes if change["will_delete"]),
        "restores": sum(1 for change in changes if change["will_restore"]),
        "modified_since_task": [change["path"] for change in changes if change["modified_since_task"]],
    }


def _expected_final(diffs: list[dict[str, Any]], path: str) -> str:
    """The content the last recorded diff implies *should* be on disk (best effort, for reporting)."""
    last = None
    for entry in diffs:
        if entry["path"] == path:
            last = entry
    if last is None:
        return ""
    return str(last.get("diff") or "")


def apply_rollback(app: Any, task_id: str, *, force: bool = False) -> dict[str, Any]:
    """Restore every file the task touched to its pre-task content.

    Raises :class:`RollbackRefused` when the task is still active, when there is nothing to restore,
    or when a touched path lies outside the project/workspaces (unless ``force`` is set).
    """
    plan = plan_rollback(app, task_id)
    if not plan["files"]:
        raise RollbackRefused(
            "this task never modified a file, so there is nothing to roll back (read-only or analysis-only task)"
        )
    task = app.tasks.get(task_id)
    roots = _paths(app, task)
    snapshots = preimages(app, task_id)
    restored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for snap in snapshots:
        path = Path(snap["path"])
        if not force and not _inside(path, roots):
            skipped.append({"path": snap["path"], "reason": "outside the project and its workspaces"})
            continue
        try:
            if snap["existed"]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(snap["content"], encoding="utf-8")
                action = "restored"
            else:
                if path.exists():
                    path.unlink()
                action = "deleted"
            restored.append({"path": snap["path"], "action": action, "bytes": len(snap["content"])})
        except OSError as exc:
            skipped.append({"path": snap["path"], "reason": f"could not write: {exc}"})
    result = {
        "task_id": task_id,
        "restored": restored,
        "skipped": skipped,
        "count": len(restored),
        "forced": force,
        "at": time.time(),
        "project_path": plan["project_path"],
    }
    try:
        app.tasks.log_event(
            task_id,
            "rollback",
            f"rolled back {len(restored)} file(s)"
            + (f", skipped {len(skipped)}" if skipped else ""),
            {"paths": [item["path"] for item in restored], "skipped": skipped},
        )
    except Exception:  # pragma: no cover - the log line is a nicety
        pass
    BUS.publish(Topics.TASK_UPDATED, task_id=task_id, fields=["rollback"])
    log.info("rolled back a task", cm={"task": task_id, "restored": len(restored), "skipped": len(skipped)})
    return result


__all__ = ["RollbackRefused", "apply_rollback", "plan_rollback", "preimages"]
