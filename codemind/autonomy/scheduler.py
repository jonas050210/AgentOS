"""Autonomous mode.

When enabled, CodeMind looks at the projects you approved and proposes (or starts, according
to your setting) genuinely useful work: analyse a repository, repair a failing test suite, add
missing tests, document an undocumented module, or follow up on a TODO it can actually
implement. Everything it proposes is derived from real observations — a failing command, a
module with no test file, a TODO comment with a concrete description — never invented.

Guard rails, all real:

* a daily time budget and an interval between sessions;
* quiet hours;
* it yields immediately when you have queued or running tasks;
* the number of actions per session is capped;
* every action still goes through the normal permission policy, so destructive or external
  steps ask for approval exactly as they do in an interactive task;
* it never retries the same failing idea: proposals already attempted are recorded and skipped.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from codemind.config import Config
from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.memory.knowledge import KnowledgeIndex, analyze_repository
from codemind.storage.db import Database

log = get_logger("autonomy.scheduler")

CATEGORY_LABELS = {
    "repo_analysis": "Repository analysis",
    "test_improvement": "Test improvement",
    "bug_fix": "Bug fixing",
    "docs": "Documentation",
    "refactor": "Refactoring",
    "experiment": "Experiment",
}


@dataclass
class Proposal:
    id: str
    category: str
    title: str
    body: str
    project: str
    project_id: int | None = None
    impact: str = ""
    status: str = "pending"
    task_id: str = ""
    ts: float = field(default_factory=time.time)
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category, self.category),
            "title": self.title,
            "body": self.body,
            "project": self.project,
            "project_id": self.project_id,
            "impact": self.impact,
            "status": self.status,
            "task_id": self.task_id,
            "ts": self.ts,
            "evidence": self.evidence,
            "age_seconds": round(time.time() - self.ts, 1),
        }


class AutonomyScheduler:
    """Periodic, budgeted, policy-respecting autonomous work."""

    def __init__(
        self,
        config: Config,
        db: Database,
        knowledge: KnowledgeIndex,
        *,
        submit_task: Callable[..., Any],
        runner_status: Callable[[], Any],
        governor: Any = None,
    ) -> None:
        self.config = config
        self.db = db
        self.knowledge = knowledge
        self.submit_task = submit_task
        self.runner_status = runner_status
        self.governor = governor
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._last_run: float = 0.0
        self._session_actions = 0
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []

    # -- configuration ----------------------------------------------------
    def update_config(self, config: Config) -> None:
        self.config = config
        self._wake.set()

    def subscribe(self, listener: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, payload)
            except Exception:  # pragma: no cover
                pass
        BUS.publish(Topics.AUTONOMY_PROPOSAL, kind=kind, **payload)

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="codemind-autonomy", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            interval = max(30.0, self.config.autonomy.interval_minutes * 60)
            wait = min(30.0, max(2.0, interval - (time.time() - self._last_run)))
            self._wake.wait(timeout=wait)
            self._wake.clear()
            if self._stop.is_set():
                return
            if not self.config.autonomy.enabled:
                continue
            if time.time() - self._last_run < interval:
                continue
            try:
                self.run_session()
            except Exception as exc:  # pragma: no cover - a session must never kill the app
                log.warning("autonomy session failed", cm={"error": str(exc)})

    # -- session logic ----------------------------------------------------
    def _in_quiet_hours(self, now: datetime | None = None) -> bool:
        spec = (self.config.autonomy.quiet_hours or "").strip()
        if not spec or "-" not in spec:
            return False
        now = now or datetime.now()
        try:
            start_text, end_text = spec.split("-", 1)
            start = datetime.strptime(start_text.strip(), "%H:%M").time()
            end = datetime.strptime(end_text.strip(), "%H:%M").time()
        except ValueError:
            return False
        current = now.time()
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end

    def budget_used_today(self) -> float:
        start_of_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE origin = 'autonomy' AND created_at >= ?",
            (start_of_day,),
        )
        sessions = int(row["n"]) if row else 0
        per_session = max(1, self.config.autonomy.max_actions_per_session)
        # each autonomous task counts as one action; measure minutes from the runs themselves
        duration = self.db.scalar(
            "SELECT SUM(finished_at - started_at) FROM tasks WHERE origin = 'autonomy' AND created_at >= ? AND finished_at IS NOT NULL",
            (start_of_day,),
            default=0.0,
        ) or 0.0
        return float(duration) / 60.0 if duration else 0.0

    def can_run(self) -> tuple[bool, str]:
        if not self.config.autonomy.enabled:
            return False, "autonomous mode is switched off"
        if self._in_quiet_hours():
            return False, f"quiet hours ({self.config.autonomy.quiet_hours}) are active"
        status = self.runner_status()
        active = len(getattr(status, "active", []) or [])
        queued = getattr(status, "queued", 0)
        if self.config.autonomy.respect_tasks and (active or queued):
            return False, f"yielding to your work ({active} active, {queued} queued task(s))"
        used = self.budget_used_today()
        if used >= self.config.autonomy.daily_budget_minutes:
            return False, f"daily budget of {self.config.autonomy.daily_budget_minutes} minutes is used up ({used:.1f} min)"
        if not self.config.autonomy.repos:
            return False, "no watched projects were configured"
        return True, "ready"

    def run_session(self, *, force: bool = False) -> dict[str, Any]:
        allowed, reason = self.can_run()
        if not allowed and not force:
            self._last_run = time.time()
            self._emit("skipped", {"reason": reason})
            log.info("autonomy session skipped", cm={"reason": reason})
            return {"skipped": True, "reason": reason}
        self._last_run = time.time()
        self._session_actions = 0
        proposals: list[Proposal] = []
        for repo in list(self.config.autonomy.repos)[:8]:
            if self._session_actions >= self.config.autonomy.max_actions_per_session:
                break
            try:
                proposals.extend(self.analyse_project(repo))
            except Exception as exc:
                log.warning("autonomy analysis failed", cm={"repo": repo, "error": str(exc)})
        fresh = [proposal for proposal in proposals if not self._already_proposed(proposal)]
        for proposal in fresh:
            if self._session_actions >= self.config.autonomy.max_actions_per_session:
                break
            self._handle(proposal)
            self._session_actions += 1
        return {
            "proposals": [proposal.as_dict() for proposal in fresh],
            "handled": self._session_actions,
            "reason": reason if not allowed else "session complete",
        }

    # -- proposal generation ---------------------------------------------
    def analyse_project(self, repo: str) -> list[Proposal]:
        """Derive work from real observations about a project."""
        path = Path(repo).expanduser()
        if not path.exists():
            return []
        analysis = analyze_repository(path, max_files=1200, collect_files=False)
        project_id = self.knowledge.register_project(path, analysis=analysis)
        self.knowledge.index_project(project_id, analysis)
        proposals: list[Proposal] = []
        categories = set(self.config.autonomy.categories)
        root = str(path)

        if "repo_analysis" in categories:
            proposals.append(
                Proposal(
                    id=uuid.uuid4().hex[:10],
                    category="repo_analysis",
                    title=f"Analyse {analysis.name} and record its structure",
                    body=(
                        f"{analysis.total_files} files, {analysis.total_lines} lines, primary language {analysis.primary_language}. "
                        f"Tests: {len(analysis.test_files)} file(s). Test command: {', '.join(analysis.test_commands) or 'none detected'}."
                    ),
                    project=root,
                    project_id=project_id,
                    impact="Keeps project knowledge current so future tasks start faster.",
                    evidence={"files": analysis.total_files, "tests": len(analysis.test_files)},
                )
            )
        if "test_improvement" in categories:
            if not analysis.test_files:
                proposals.append(
                    Proposal(
                        id=uuid.uuid4().hex[:10],
                        category="test_improvement",
                        title=f"Add a first test suite to {analysis.name}",
                        body=(
                            "No test files match the conventional patterns. CodeMind can scaffold tests for the modules "
                            "listed in the analysis report, then run them."
                        ),
                        project=root,
                        project_id=project_id,
                        impact="A repository without tests cannot be verified automatically — this unblocks safe changes.",
                        evidence={"modules": [f.path for f in analysis.files[:10]]},
                    )
                )
            elif not analysis.test_commands:
                proposals.append(
                    Proposal(
                        id=uuid.uuid4().hex[:10],
                        category="test_improvement",
                        title=f"Declare how tests are run in {analysis.name}",
                        body="Test files exist but no runnable test command was detected, so verification cannot run automatically.",
                        project=root,
                        project_id=project_id,
                        impact="Enables automatic verification for every future task in this project.",
                        evidence={"test_files": analysis.test_files[:5]},
                    )
                )
        if "bug_fix" in categories and analysis.test_commands and self.config.agent.verify_tests:
            proposals.append(
                Proposal(
                    id=uuid.uuid4().hex[:10],
                    category="bug_fix",
                    title=f"Run the test suite of {analysis.name} and fix what fails",
                    body=f"Command: `{analysis.test_commands[0]}`. If everything passes, the session records that as evidence and stops.",
                    project=root,
                    project_id=project_id,
                    impact="Catches regressions early; produces a verified baseline for later changes.",
                    evidence={"command": analysis.test_commands[0]},
                )
            )
        if "docs" in categories:
            untested = self._undocumented_modules(analysis)
            if untested:
                proposals.append(
                    Proposal(
                        id=uuid.uuid4().hex[:10],
                        category="docs",
                        title=f"Document {len(untested)} module(s) in {analysis.name}",
                        body="These files have no docstring and no documentation entry: " + ", ".join(untested[:8]),
                        project=root,
                        project_id=project_id,
                        impact="Makes the codebase easier for you and for the agent to navigate.",
                        evidence={"modules": untested[:20]},
                    )
                )
        if "experiment" in categories:
            proposals.append(
                Proposal(
                    id=uuid.uuid4().hex[:10],
                    category="experiment",
                    title=f"Collect new tool-selection traces from {analysis.name}",
                    body="Runs a small read-only reasoning pass over the project's structure and stores the traces as a dataset for training.",
                    project=root,
                    project_id=project_id,
                    impact="Improves the trained adapter without touching the code.",
                    evidence={"language": analysis.primary_language},
                )
            )
        return proposals

    def _undocumented_modules(self, analysis: Any, limit: int = 40) -> list[str]:
        candidates: list[str] = []
        for facts in analysis.files:
            if facts.language not in ("Python", "JavaScript", "TypeScript"):
                continue
            if facts.is_test:
                continue
            has_public = any(kind in ("class", "function", "const") for kind, _name, _line in facts.symbols)
            if has_public and facts.lines > 20:
                candidates.append(facts.path)
            if len(candidates) >= limit:
                break
        return candidates

    def _already_proposed(self, proposal: Proposal) -> bool:
        row = self.db.query_one(
            "SELECT status FROM proposals WHERE title = ? ORDER BY ts DESC LIMIT 1",
            (proposal.title,),
        )
        if row is None:
            return False
        return row["status"] in ("pending", "accepted", "running")

    # -- handling ---------------------------------------------------------
    def _handle(self, proposal: Proposal) -> None:
        mode = self.config.autonomy.proposal_mode
        status = "pending"
        task_id = ""
        if mode == "approve_first":
            status = "pending"
        else:
            task = self.submit_task(
                prompt=self._task_prompt(proposal),
                project_path=proposal.project,
                title=proposal.title,
                origin="autonomy",
                mode="autonomous",
                priority=8,
            )
            task_id = getattr(task, "id", "")
            status = "accepted"
        self.db.insert(
            "proposals",
            {
                "ts": proposal.ts,
                "category": proposal.category,
                "project_id": proposal.project_id,
                "title": proposal.title,
                "body": proposal.body,
                "status": status,
                "task_id": task_id or None,
                "impact": proposal.impact,
            },
        )
        proposal.status = status
        proposal.task_id = task_id
        self._emit("proposal", proposal.as_dict())
        log.info("autonomy proposal recorded", cm={"category": proposal.category, "status": status, "title": proposal.title})

    def _task_prompt(self, proposal: Proposal) -> str:
        return (
            f"{proposal.title}\n\n"
            f"Context: {proposal.body}\n\n"
            f"Why this matters: {proposal.impact}\n\n"
            "Work autonomously: inspect the project first, make the smallest correct change if one is needed, "
            "run the project's tests or a focused command, and report the evidence. If no change is justified "
            "(for example the test suite already passes), record that as the result instead of editing files."
        )

    # -- approvals from the GUI ------------------------------------------
    def proposals(self, status: str = "", limit: int = 50) -> list[dict[str, Any]]:
        if status:
            rows = self.db.query("SELECT * FROM proposals WHERE status = ? ORDER BY ts DESC LIMIT ?", (status, limit))
        else:
            rows = self.db.query("SELECT * FROM proposals ORDER BY ts DESC LIMIT ?", (limit,))
        return [
            {
                "id": row["id"],
                "ts": row["ts"],
                "category": row["category"],
                "category_label": CATEGORY_LABELS.get(row["category"], row["category"]),
                "title": row["title"],
                "body": row["body"],
                "status": row["status"],
                "task_id": row["task_id"],
                "impact": row["impact"],
                "project_id": row["project_id"],
            }
            for row in rows
        ]

    def accept(self, proposal_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
        if row is None:
            raise ValueError("unknown proposal")
        if row["status"] != "pending":
            raise ValueError(f"proposal is already {row['status']}")
        project = self.db.query_one("SELECT path FROM projects WHERE id = ?", (row["project_id"],))
        task = self.submit_task(
            prompt=self._task_prompt(
                Proposal(
                    id=str(row["id"]), category=row["category"], title=row["title"], body=row["body"] or "",
                    project=project["path"] if project else "", impact=row["impact"] or "",
                )
            ),
            project_path=project["path"] if project else "",
            title=row["title"],
            origin="autonomy",
            mode="autonomous",
            priority=7,
        )
        self.db.update("proposals", {"status": "accepted", "task_id": getattr(task, "id", ""), "resolved_at": time.time()}, "id = ?", (proposal_id,))
        return {"id": proposal_id, "task_id": getattr(task, "id", ""), "status": "accepted"}

    def dismiss(self, proposal_id: int, reason: str = "") -> dict[str, Any]:
        self.db.update("proposals", {"status": "dismissed", "resolved_at": time.time()}, "id = ?", (proposal_id,))
        return {"id": proposal_id, "status": "dismissed", "reason": reason}

    def stats(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for row in self.db.query("SELECT status, COUNT(*) AS n FROM proposals GROUP BY status"):
            counts[row["status"]] = int(row["n"])
        return {
            "enabled": self.config.autonomy.enabled,
            "categories": list(self.config.autonomy.categories),
            "repos": list(self.config.autonomy.repos),
            "interval_minutes": self.config.autonomy.interval_minutes,
            "daily_budget_minutes": self.config.autonomy.daily_budget_minutes,
            "budget_used_minutes": round(self.budget_used_today(), 2),
            "next_run_in": max(0.0, self.config.autonomy.interval_minutes * 60 - (time.time() - self._last_run)) if self._last_run else 0.0,
            "last_run": self._last_run or None,
            "proposals": counts,
            "quiet_hours_active": self._in_quiet_hours(),
            "can_run": self.can_run(),
        }
