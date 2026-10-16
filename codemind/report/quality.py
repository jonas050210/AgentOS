"""The honesty metric: how much of what CodeMind reports is actually proven.

The product promise is "no success without evidence", so it needs a number. A task counts as
*verified* only when every check recorded by the executor passed — the same condition the agent
itself uses before it is allowed to call a task completed. A completed task without that evidence is
reported as an *unverified claim*: not hidden, not rounded away, but listed with its task id.
"""

from __future__ import annotations

import time
from typing import Any

UNVERIFIED_STATUSES = ("completed",)
CLAIMABLE = ("failed", "blocked")


def _verify_payload(task: Any) -> dict[str, Any]:
    payload = getattr(task, "verify", None)
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str) and payload.strip():
        from codemind.storage.db import _loads

        return _loads(payload, {}) or {}
    return {}


def unverified_tasks(app: Any, *, limit: int = 200) -> list[dict[str, Any]]:
    """Completed tasks that carry no passing evidence — these must never silently grow."""
    out: list[dict[str, Any]] = []
    for task in app.tasks.list(limit=limit):
        if task.status not in UNVERIFIED_STATUSES:
            continue
        payload = _verify_payload(task)
        if payload.get("verified"):
            continue
        out.append(
            {
                "task_id": task.id,
                "title": task.title,
                "status": task.status,
                "summary": (task.summary or "")[:200],
                "checks": payload.get("checks") or [],
                "finished_at": task.finished_at,
            }
        )
    return out


def honesty_report(app: Any, *, limit: int = 200) -> dict[str, Any]:
    """Counts and rates over recent task history.

    ``verified_rate`` is the share of completed tasks that produced passing evidence;
    ``unverified`` is the number of claims that would need a human look. Both come from the stored
    task rows, not from anything the UI tracks separately.
    """
    tasks = app.tasks.list(limit=limit)
    completed = [task for task in tasks if task.status in UNVERIFIED_STATUSES]
    verified = [task for task in completed if _verify_payload(task).get("verified")]
    unverified = [task for task in completed if not _verify_payload(task).get("verified")]
    failed = [task for task in tasks if task.status in CLAIMABLE]
    with_evidence = [task for task in completed if (_verify_payload(task).get("checks") or [])]
    return {
        "sampled": len(tasks),
        "completed": len(completed),
        "verified": len(verified),
        "unverified": len(unverified),
        "verified_rate": round(len(verified) / len(completed), 4) if completed else 0.0,
        "with_any_evidence": len(with_evidence),
        "failed": len(failed),
        "unverified_tasks": [
            {"task_id": task.id, "title": task.title, "summary": (task.summary or "")[:160]} for task in unverified[:10]
        ],
        "generated_at": time.time(),
    }
