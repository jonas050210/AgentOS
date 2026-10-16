"""The change passport: a verifiable record of what one task actually did.

Motivation: an agent that says "done" is worth very little unless the claim can be checked. A
passport contains the goal, the plan, every step with its tool, arguments and result, the commands
that ran, the diffs that were applied, the verification checks and the model/adapter that was in use
— in the order it happened, and chained with SHA-256 so that any later edit of the file is
detectable. It is written next to the artefacts (``artifacts/passports``) and can be exported as
JSON or Markdown.

The chain is deliberately simple: ``h_n = sha256(h_{n-1} ‖ canonical(entry_n))`` starting from 64
zeroes. It proves internal consistency of the file, not authenticity — that is all a local tool can
honestly promise, and it is said that way in the report.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from codemind.logging_setup import get_logger

log = get_logger("report.passport")

CHAIN_VERSION = 1
GENESIS = "0" * 64
MAX_DIFF_CHARS = 20_000


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def chain_hashes(entries: Sequence[dict[str, Any]]) -> list[str]:
    """Fold an ordered list of entries into a hash chain."""
    hashes: list[str] = []
    previous = GENESIS
    for entry in entries:
        digest = hashlib.sha256((previous + _canonical(entry)).encode("utf-8")).hexdigest()
        hashes.append(digest)
        previous = digest
    return hashes


def verify_chain(passport: dict[str, Any]) -> tuple[bool, str]:
    """Check a passport against its own chain. Returns ``(ok, explanation)``."""
    entries = passport.get("entries") or []
    stored = passport.get("hashes") or []
    if not isinstance(entries, list) or not isinstance(stored, list):
        return False, "the passport has no entry chain"
    if len(entries) != len(stored):
        return False, f"{len(entries)} entries but {len(stored)} hashes"
    recomputed = chain_hashes(entries)
    for index, (expected, actual) in enumerate(zip(recomputed, stored)):
        if expected != actual:
            return False, f"entry {index} was modified after the passport was written"
    root = passport.get("root")
    if root and recomputed and root != recomputed[-1]:
        return False, "the recorded root hash does not match the entries"
    return True, f"{len(entries)} entries verified against the chain"


def _entry(kind: str, **payload: Any) -> dict[str, Any]:
    return {"kind": kind, "at": round(time.time(), 4), **payload}


def _safe(query: Any, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in query(sql, params)]
    except Exception as exc:  # pragma: no cover - a broken database is reported elsewhere
        log.debug("passport query failed", cm={"error": str(exc)})
        return []


def build_passport(app: Any, task_id: str, *, include_diffs: bool = True) -> dict[str, Any]:
    """Collect everything known about one task into a single, ordered record."""
    task = app.tasks.get(task_id)
    if task is None:
        raise ValueError(f"unknown task {task_id!r}")

    steps = list(getattr(task, "steps", []) or [])
    checks = ((getattr(task, "verify", None) or {}) if isinstance(getattr(task, "verify", None), dict) else {}).get("checks") or []
    events = app.tasks.events(task_id, limit=400)
    diffs = app.tasks.diffs(task_id) if include_diffs else []
    tool_calls = _safe(
        app.db.query,
        "SELECT ts, tool, ok, duration, risk, summary FROM tool_calls WHERE task_id = ? ORDER BY id LIMIT 500",
        (task_id,),
    )
    backend = app.models.active_backend_info()
    adapter = None
    try:
        adapter = app.training.active_version()
    except Exception:  # pragma: no cover - training may be disabled
        adapter = None

    entries: list[dict[str, Any]] = [
        _entry(
            "task",
            task_id=task.id,
            title=task.title,
            prompt=task.prompt,
            status=task.status,
            mode=task.mode,
            project=task.project_path,
            started_at=task.started_at,
            finished_at=task.finished_at,
            origin=getattr(task, "origin", ""),
        )
    ]
    plan = getattr(task, "plan", None)
    if plan:
        entries.append(_entry("plan", goal=(plan.get("goal") if isinstance(plan, dict) else str(plan)) or "", steps=len(steps)))
    for step in steps:
        entries.append(
            _entry(
                "step",
                index=getattr(step, "idx", None),
                title=step.title,
                tool=step.tool,
                status=step.status,
                origin=getattr(step, "origin", ""),
                attempts=getattr(step, "attempts", 1),
                started_at=step.started_at,
                finished_at=step.finished_at,
                error=(step.error or "")[:400],
            )
        )
    for call in tool_calls:
        entries.append(
            _entry(
                "tool",
                tool=call.get("tool"),
                ok=bool(call.get("ok")),
                duration=round(float(call.get("duration") or 0.0), 3),
                risk=call.get("risk"),
                summary=(call.get("summary") or "")[:300],
            )
        )
    for entry in diffs:
        entries.append(
            _entry(
                "diff",
                path=entry.get("path"),
                kind=entry.get("kind"),
                added=entry.get("added"),
                removed=entry.get("removed"),
                digest=hashlib.sha256(str(entry.get("diff") or "").encode("utf-8")).hexdigest()[:32],
            )
        )
    for check in checks:
        entries.append(_entry("check", name=check.get("name"), ok=bool(check.get("ok")), detail=str(check.get("detail") or "")[:300]))
    for event in events:
        entries.append(_entry("event", event_kind=event.get("kind"), message=str(event.get("message") or "")[:300]))
    entries.append(_entry("result", status=task.status, summary=task.summary, error=(task.error or "")[:600]))

    hashes = chain_hashes(entries)
    verified = bool((getattr(task, "verify", None) or {}).get("verified")) if isinstance(getattr(task, "verify", None), dict) else False
    passport = {
        "passport_version": CHAIN_VERSION,
        "task_id": task.id,
        "title": task.title,
        "prompt": task.prompt,
        "status": task.status,
        "summary": task.summary,
        "error": task.error,
        "mode": task.mode,
        "project_path": task.project_path,
        "verified": verified,
        "checks": checks,
        "verification_note": str(((getattr(task, "verify", None) or {}) if isinstance(getattr(task, "verify", None), dict) else {}).get("note") or ""),
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "duration_seconds": round((task.finished_at or 0) - (task.started_at or 0), 3) if task.finished_at and task.started_at else None,
        "models": {
            "name": backend.get("name"),
            "kind": backend.get("kind"),
            "model": backend.get("model"),
            "reason": backend.get("reason"),
        },
        "adapter": adapter,
        "files_changed": [
            {"path": entry.get("path"), "kind": entry.get("kind"), "added": entry.get("added"), "removed": entry.get("removed")}
            for entry in diffs
        ],
        "tool_calls": len(tool_calls),
        "steps": [
            {
                "index": getattr(step, "idx", None),
                "title": step.title,
                "tool": step.tool,
                "status": step.status,
                "origin": getattr(step, "origin", ""),
                "error": (step.error or "")[:400],
            }
            for step in steps
        ],
        "diffs": [
            {
                "path": entry.get("path"),
                "added": entry.get("added"),
                "removed": entry.get("removed"),
                "diff": str(entry.get("diff") or "")[:MAX_DIFF_CHARS],
            }
            for entry in diffs
        ],
        "events": [{"kind": event.get("kind"), "message": event.get("message")} for event in events[:200]],
        "entries": entries,
        "hashes": hashes,
        "root": hashes[-1] if hashes else GENESIS,
        "chain": {"algorithm": "sha256", "version": CHAIN_VERSION, "genesis": GENESIS},
        "generated_at": time.time(),
        "generated_by": "CodeMind change passport",
    }
    ok, note = verify_chain(passport)
    passport["chain_ok"] = ok
    passport["chain_note"] = note
    return passport


def _fmt_time(value: float | None) -> str:
    if not value:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def render_markdown(passport: dict[str, Any]) -> str:
    """Human-readable version of the passport (what you would paste into a PR)."""
    status = str(passport.get("status", ""))
    verified = "verified" if passport.get("verified") else "NOT verified"
    lines: list[str] = []
    lines.append(f"# Change passport — {passport.get('title') or passport.get('task_id')}")
    lines.append("")
    lines.append(f"- **Status:** {status} ({verified})")
    lines.append(f"- **Task:** `{passport.get('task_id')}` · mode {passport.get('mode')}")
    if passport.get("project_path"):
        lines.append(f"- **Project:** `{passport.get('project_path')}`")
    lines.append(f"- **Started:** {_fmt_time(passport.get('started_at'))} · **Finished:** {_fmt_time(passport.get('finished_at'))}")
    if passport.get("duration_seconds") is not None:
        lines.append(f"- **Duration:** {passport['duration_seconds']:.2f} s")
    models = passport.get("models") or {}
    lines.append(f"- **Model:** {models.get('name') or 'built-in planner'} — {models.get('reason') or ''}".rstrip(" —"))
    adapter = passport.get("adapter") or {}
    lines.append(f"- **Trained adapter:** {adapter.get('version') or 'baseline'}")
    lines.append("")
    lines.append("## Goal")
    lines.append("")
    lines.append(str(passport.get("prompt") or "").strip() or "—")
    lines.append("")
    lines.append("## What was done")
    lines.append("")
    summary = str(passport.get("summary") or "").strip()
    lines.append(summary or "_no summary recorded_")
    lines.append("")
    lines.append("## Plan and steps")
    lines.append("")
    lines.append("| # | Step | Tool | Status | Origin |")
    lines.append("| --- | --- | --- | --- | --- |")
    for step in passport.get("steps") or []:
        lines.append(
            f"| {step.get('index')} | {step.get('title')} | `{step.get('tool')}` | {step.get('status')} | {step.get('origin') or 'plan'} |"
        )
    if not passport.get("steps"):
        lines.append("| — | _no steps recorded_ | — | — | — |")
    lines.append("")
    lines.append("## Verification")
    lines.append("")
    checks = passport.get("checks") or []
    if checks:
        for check in checks:
            mark = "PASS" if check.get("ok") else "FAIL"
            lines.append(f"- **[{mark}] {check.get('name')}** — {check.get('detail')}")
    else:
        lines.append("- _No verification checks were recorded for this task._")
    lines.append("")
    files = passport.get("files_changed") or []
    lines.append("## Files changed")
    lines.append("")
    if files:
        lines.append("| File | Kind | + | − |")
        lines.append("| --- | --- | --- | --- |")
        for entry in files:
            lines.append(f"| `{entry.get('path')}` | {entry.get('kind')} | {entry.get('added')} | {entry.get('removed')} |")
    else:
        lines.append("_No files were modified._")
    lines.append("")
    diffs = [entry for entry in (passport.get("diffs") or []) if entry.get("diff")]
    if diffs:
        lines.append("## Diff")
        lines.append("")
        for entry in diffs:
            lines.append(f"### `{entry.get('path')}`")
            lines.append("")
            lines.append("```diff")
            lines.append(str(entry.get("diff")).rstrip())
            lines.append("```")
            lines.append("")
    lines.append("## Integrity")
    lines.append("")
    lines.append(
        f"- Chain: SHA-256 over {len(passport.get('entries') or [])} entries, "
        f"algorithm version {passport.get('chain', {}).get('version')}"
    )
    lines.append(f"- Root hash: `{passport.get('root')}`")
    lines.append(f"- Self-check at export: {'passed' if passport.get('chain_ok') else 'FAILED'} — {passport.get('chain_note')}")
    lines.append(
        "- The chain proves this file was not edited after it was written. It does not prove who "
        "wrote it, and it only repeats what the run recorded — the checks above are the actual evidence."
    )
    lines.append("")
    return "\n".join(lines)


def write_passport(
    app: Any,
    task_id: str,
    *,
    formats: Iterable[str] = ("json", "md"),
    directory: Path | None = None,
    include_diffs: bool = True,
) -> dict[str, Any]:
    """Build a passport and write it to disk. Returns paths, root hash and the verification flags."""
    passport = build_passport(app, task_id, include_diffs=include_diffs)
    target = Path(directory) if directory else Path(app.layout.resolve("artifacts")) / "passports"
    target.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    written: dict[str, str] = {}
    for fmt in formats:
        if fmt == "json":
            path = target / f"{task_id}-{stamp}.json"
            path.write_text(json.dumps(passport, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        elif fmt in ("md", "markdown"):
            path = target / f"{task_id}-{stamp}.md"
            path.write_text(render_markdown(passport), encoding="utf-8")
        else:  # pragma: no cover - defensive
            raise ValueError(f"unsupported passport format {fmt!r}")
        written[fmt] = str(path)
    latest = target / f"{task_id}-latest.{'md' if 'md' in written else 'json'}"
    try:
        source = Path(written.get("md") or written.get("json"))
        latest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - convenience symlink-like copy
        log.debug("could not update the latest passport copy", cm={"error": str(exc)})
    result = {
        "task_id": task_id,
        "files": written,
        "latest": str(latest),
        "root": passport["root"],
        "chain_ok": passport["chain_ok"],
        "verified": passport["verified"],
        "status": passport["status"],
        "files_changed": len(passport["files_changed"]),
        "checks": len(passport["checks"]),
    }
    try:
        app.db.insert(
            "task_events",
            {
                "task_id": task_id,
                "ts": time.time(),
                "kind": "passport",
                "message": f"Change passport written to {written.get('md') or written.get('json')}",
                "payload": json.dumps({"root": passport["root"], "chain_ok": passport["chain_ok"]}),
            },
        )
    except Exception:  # pragma: no cover - the passport itself is already on disk
        pass
    from codemind.events import BUS, Topics

    BUS.publish(Topics.TASK_LOG, task_id=task_id, kind="passport", message="change passport written", payload=result)
    return result
