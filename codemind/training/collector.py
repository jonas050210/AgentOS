"""Collect training examples from finished tasks — with a real quality gate.

The agent turns its own work into supervision:

* **task traces** — the instruction the user gave and the tool/plan the agent actually chose;
* **step traces** — what each step was and which tool it used;
* **failure lessons** — what went wrong and what to do differently.

Only traces that meet the criteria count: the task must have produced evidence (a verification
check that passed), the example must validate, and the quality score must clear the configured
threshold. Traces that fail are counted and reported, never silently trained on.

Memory is *not* training: memories are retrieved at task time, these examples change weights.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from codemind.config import Config
from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.storage.db import Database
from codemind.training.datasets import DatasetBuilder, Example, quality_score
from codemind.utils.textutil import shorten

log = get_logger("training.collector")

DEFAULT_DATASET = "task_traces"


@dataclass
class CollectionReport:
    task_id: str
    accepted: int = 0
    rejected: int = 0
    reasons: list[str] = field(default_factory=list)
    dataset: str = ""
    examples: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "reasons": self.reasons[:10],
            "dataset": self.dataset,
            "examples": self.examples,
        }


class TaskTraceCollector:
    """Builds and maintains the task-trace dataset."""

    def __init__(self, config: Config, db: Database, datasets: DatasetBuilder) -> None:
        self.config = config
        self.db = db
        self.datasets = datasets
        self.dataset_name = DEFAULT_DATASET

    def update_config(self, config: Config) -> None:
        self.config = config

    # -- collection -------------------------------------------------------
    def collect(self, task: Any, outcome: Any) -> CollectionReport:
        report = CollectionReport(task_id=getattr(task, "id", ""))
        verification = (getattr(outcome, "verification", None) or {}) or (getattr(task, "verify", None) or {})
        verified = bool(verification.get("verified"))
        succeeded = getattr(outcome, "status", "") == "completed"
        if self.config.training.curated_only and not verified:
            report.rejected = 1
            report.reasons.append("task was not verified, so its trace was not used")
            return report

        examples: list[Example] = []
        context = self._context(task)
        steps = [step for step in getattr(task, "steps", []) or []]
        # A label is only trustworthy when the action actually worked, so failures never become
        # training targets unless you explicitly opt into replaying mistakes as negatives.
        successful = [step for step in steps if getattr(step, "tool", "") and getattr(step, "status", "") == "done"]
        failed = [step for step in steps if getattr(step, "tool", "") and getattr(step, "status", "") == "failed"]
        if successful and succeeded and verified:
            examples.append(
                Example(
                    instruction=str(getattr(task, "prompt", "")).strip(),
                    action={"tool": successful[0].tool, "steps": [{"tool": step.tool, "title": step.title} for step in successful[:8]]},
                    context=context,
                    quality=quality_score(Example(str(getattr(task, "prompt", "")), action={"tool": successful[0].tool}), verified=verified, success=True),
                    source="task_trace",
                    meta={"task_id": getattr(task, "id", ""), "origin": getattr(task, "origin", ""), "status": getattr(outcome, "status", "")},
                )
            )
        for step in successful:
            if not step.tool:
                continue
            instruction = f"{getattr(task, 'prompt', '')}\n\nNext step: {step.title}"
            if step.intent or step.detail:
                instruction += f"\nIntent: {step.intent or step.detail}"
            example = Example(
                instruction=instruction,
                action={"tool": step.tool, "arguments": {k: v for k, v in (step.arguments or {}).items() if k != "content"}},
                context=context,
                quality=quality_score(Example(instruction), verified=verified, success=True),
                source="step_trace",
                meta={"task_id": getattr(task, "id", ""), "step": step.idx, "status": step.status},
            )
            examples.append(example)
        if failed and not self.config.memory.learn_from_failures:
            report.reasons.append(f"{len(failed)} failing step(s) were excluded from the dataset (only verified actions become labels)")
        if not examples:
            report.rejected = 1
            report.reasons.append(
                "no verified, successful steps to learn from in this task"
                + (f" ({len(failed)} step(s) failed)" if failed else "")
            )
            return report

        accepted: list[Example] = []
        threshold = self.config.training.quality_threshold
        for example in examples:
            if example.quality < threshold:
                report.rejected += 1
                report.reasons.append(f"below the quality gate ({example.quality:.2f} < {threshold:.2f}): {shorten(example.instruction, 60)}")
                continue
            accepted.append(example)
        if not accepted:
            return report

        existing = self._load_existing()
        # merge=True: from_examples re-reads what is already stored, de-duplicates and rewrites
        # atomically, so concurrent finished tasks cannot overwrite each other's traces.
        result = self.datasets.from_examples(
            self.dataset_name,
            accepted,
            notes=f"{len(existing) + len(accepted)} traces collected from finished tasks",
            merge=True,
        )
        report.accepted = len(accepted)
        report.dataset = self.dataset_name
        report.examples = result["examples"]
        BUS.publish(Topics.MEMORY_INDEX, dataset=self.dataset_name, accepted=report.accepted, examples=report.examples)
        log.info("task traces collected", cm={"task": report.task_id, "accepted": report.accepted, "total": report.examples})
        return report

    def _context(self, task: Any) -> str:
        facts = {}
        result = getattr(task, "result", None) or {}
        if isinstance(result, dict):
            facts = result.get("facts") or {}
        if not facts:
            return f"project: {getattr(task, 'project_path', '') or 'workspace'}"
        return (
            f"language: {facts.get('primary_language', 'unknown')}; "
            f"files: {facts.get('total_files', 0)}; "
            f"tests: {len(facts.get('test_files') or [])}; "
            f"test command: {'; '.join((facts.get('test_commands') or [])[:1]) or 'none detected'}"
        )

    def _load_existing(self) -> list[Example]:
        row = self.db.query_one("SELECT path FROM datasets WHERE name = ?", (self.dataset_name,))
        if not row:
            return []
        path = Path(row["path"])
        if not path.exists():
            return []
        examples: list[Example] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            examples.append(Example.from_dict(payload))
        return examples

    # -- reporting --------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        row = self.db.query_one("SELECT path, examples, updated_at FROM datasets WHERE name = ?", (self.dataset_name,))
        return {
            "dataset": self.dataset_name,
            "exists": bool(row),
            "examples": int(row["examples"]) if row else 0,
            "updated_at": row["updated_at"] if row else None,
            "path": row["path"] if row else "",
            "curated_only": self.config.training.curated_only,
            "quality_threshold": self.config.training.quality_threshold,
        }

    def recent_examples(self, limit: int = 20) -> list[dict[str, Any]]:
        examples = self._load_existing()
        return [example.as_dict() for example in examples[-limit:]][::-1]
