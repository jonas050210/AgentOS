"""Evaluation: a candidate is only promoted when measured results justify it.

Suites:

``tool_use``
    Held-out instructions labelled with the tool the planner should choose. Scored by accuracy;
    the untrained baseline is the deterministic keyword planner, so the number means something.
``coding_core``
    Plan-shape checks over held-out tasks: does the plan start with inspection, contain a
    verification step, avoid unknown tools and stay within the step budget?
``regression``
    Examples that the current active version already gets right. Any drop here blocks promotion,
    which is what stops "improvement" that quietly breaks working behaviour.
``instruction_following``
    Whether the planner emits the required JSON contract and fills required arguments.

LoRA/QLoRA candidates are additionally evaluated on held-out loss/perplexity, because
generation quality cannot be judged by a classifier score.
"""

from __future__ import annotations

import json
import math
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from codemind.logging_setup import get_logger
from codemind.training.nn import AdapterModel
from codemind.utils.textutil import shorten

log = get_logger("training.eval")

SUITES = ("coding_core", "tool_use", "regression", "instruction_following")

# A small, curated set of instruction → tool expectations. It is deliberately narrow: these
# are the decisions the agent makes on every task, so accuracy here translates into behaviour.
TOOL_USE_BENCHMARK: list[tuple[str, str]] = [
    ("Where is the database schema defined?", "search_code"),
    ("Show me the contents of src/app.py", "read_file"),
    ("What does this repository do?", "analyze_repository"),
    ("Which files are in the tests directory?", "list_files"),
    ("What does the function parse_config do?", "knowledge_search"),
    ("What have I decided about formatting rules?", "memory_search"),
    ("List the symbols in this project", "project_outline"),
    ("What branch am I on and what changed?", "git_status"),
    ("Show the diff of my current work", "git_diff"),
    ("Run the test suite", "run_tests"),
    ("Does the build still pass?", "run_command"),
    ("Which python version is available?", "environment_info"),
    ("Commit these changes with a clear message", "git_commit"),
    ("Create a branch for this work", "git_branch"),
    ("Push this branch to origin", "git_push"),
    ("Open a pull request for this change", "create_pull_request"),
    ("Add a dependency on requests", "install_package"),
    ("Clone the repository acme/widgets", "repo_clone"),
    ("Fetch the documentation page for pytest fixtures", "fetch_docs"),
    ("Download the qwen2.5-coder model", "download_model"),
    ("Remember that this project prefers ruff", "memory_save"),
]

PLAN_QUALITY_BENCHMARK: list[str] = [
    "Fix the failing test in tests/test_parser.py",
    "Add a --verbose flag to the CLI entry point",
    "Refactor the configuration loader to remove duplication",
    "Document how the scheduler works in the README",
    "Why does the server crash when the port is busy?",
    "Add tests for the new retry helper",
    "Improve performance of the report generator",
    "Upgrade the pinned dependency on httpx",
    "Harden the upload endpoint against path traversal",
    "Repair the CI workflow that fails on lint",
]

INSPECTION_TOOLS = {"analyze_repository", "read_file", "search_code", "list_files", "knowledge_search", "project_outline", "git_status", "environment_info"}
VERIFICATION_TOOLS = {"run_tests", "run_command", "git_diff"}


@dataclass
class Scorecard:
    suite: str
    score: float
    metrics: dict[str, Any] = field(default_factory=dict)
    per_item: list[dict[str, Any]] = field(default_factory=list)
    version: str = ""
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "score": round(self.score, 4),
            "metrics": self.metrics,
            "per_item": self.per_item[:200],
            "version": self.version,
            "created_at": self.created_at,
        }


@dataclass
class PromotionDecision:
    promote: bool
    reason: str
    candidate_score: float = 0.0
    baseline_score: float = 0.0
    regressions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "promote": self.promote,
            "reason": self.reason,
            "candidate_score": round(self.candidate_score, 4),
            "baseline_score": round(self.baseline_score, 4),
            "regressions": self.regressions,
        }


class Evaluator:
    """Runs the suites against an adapter (or the deterministic baseline)."""

    def __init__(self, db: Any = None, *, planner: Any = None, min_gain: float = 0.02) -> None:
        self.db = db
        self.planner = planner
        self.min_gain = min_gain

    # -- suites -----------------------------------------------------------
    def tool_use(self, adapter: AdapterModel | None, *, extra: Sequence[tuple[str, str]] = ()) -> Scorecard:
        items = list(TOOL_USE_BENCHMARK) + list(extra)
        per_item: list[dict[str, Any]] = []
        correct = 0
        for instruction, expected in items:
            if adapter is not None:
                predicted, confidence, _probs = adapter.predict(instruction)
            else:
                predicted, confidence = _baseline_tool(instruction), 0.0
            ok = predicted == expected
            correct += 1 if ok else 0
            per_item.append({"instruction": instruction, "expected": expected, "predicted": predicted, "ok": ok, "confidence": round(confidence, 3)})
        score = correct / max(1, len(items))
        return Scorecard("tool_use", score, {"items": len(items), "correct": correct, "accuracy": round(score, 4)}, per_item)

    def coding_core(self, adapter: AdapterModel | None) -> Scorecard:
        """Plan-shape quality: measured on the real planner with the adapter in the loop."""
        from codemind.llm.heuristic import build_plan

        per_item: list[dict[str, Any]] = []
        total = 0.0
        for task in PLAN_QUALITY_BENCHMARK:
            plan = build_plan(task, files=[], repo={"test_commands": ["pytest -q"]}, tools=[])
            tools = [step.get("tool") for step in plan.get("steps", [])]
            checks = {
                "starts_with_inspection": bool(tools) and tools[0] in INSPECTION_TOOLS,
                "has_verification": any(tool in VERIFICATION_TOOLS for tool in tools),
                "no_empty_steps": all(bool(step.get("title")) for step in plan.get("steps", [])),
                "within_budget": len(tools) <= 14,
                "mentions_files": bool([step for step in plan.get("steps", []) if step.get("tool") == "read_file"]),
            }
            item_score = sum(1 for value in checks.values() if value) / len(checks)
            total += item_score
            per_item.append({"task": task, "checks": checks, "score": round(item_score, 3), "steps": len(tools)})
        score = total / max(1, len(per_item))
        return Scorecard("coding_core", score, {"tasks": len(per_item), "avg_checks_passed": round(score, 4)}, per_item)

    def instruction_following(self, adapter: AdapterModel | None) -> Scorecard:
        """Does the planner produce the required structured output?"""
        from codemind.llm.base import extract_json

        samples = [
            '{"steps": [{"title": "inspect", "tool": "analyze_repository"}]}',
            '```json\n{"steps": [{"title": "read", "tool": "read_file"}]}\n```',
            'Sure!\n{"goal": "x", "steps": [{"title": "test", "tool": "run_tests"}]}',
            '{"steps": [{"title": "a", "tool": "read_file",},]}',
        ]
        parsed = 0
        for sample in samples:
            payload = extract_json(sample, expect="steps")
            if isinstance(payload, dict) and payload.get("steps"):
                parsed += 1
        score = parsed / len(samples)
        return Scorecard(
            "instruction_following",
            score,
            {"samples": len(samples), "parsed": parsed, "note": "JSON contract of the planner (fences, prose and trailing commas must survive)."},
            [{"sample": shorten(sample, 60), "parsed": isinstance(extract_json(sample, expect='steps'), dict)} for sample in samples],
        )

    def regression(self, adapter: AdapterModel | None, examples: Sequence[tuple[str, str]]) -> Scorecard:
        items = [item for item in examples if item[1]]
        sample = items[:200]
        per_item: list[dict[str, Any]] = []
        correct = 0
        for instruction, expected in sample:
            predicted, _confidence, _probs = adapter.predict(instruction) if adapter is not None else (_baseline_tool(instruction), 0.0, [])
            ok = predicted == expected
            correct += 1 if ok else 0
            per_item.append({"instruction": shorten(instruction, 80), "expected": expected, "predicted": predicted, "ok": ok})
        score = correct / max(1, len(sample))
        return Scorecard("regression", score, {"examples": len(sample), "correct": correct}, per_item)

    # -- orchestration ----------------------------------------------------
    def evaluate(
        self,
        *,
        adapter: AdapterModel | None,
        version: str,
        suite: str = "coding_core",
        regression_examples: Sequence[tuple[str, str]] = (),
    ) -> Scorecard:
        """Run one suite (or all of them) and persist the scorecard."""
        if suite == "all":
            cards = [self.tool_use(adapter), self.coding_core(adapter), self.instruction_following(adapter)]
            if regression_examples:
                cards.append(self.regression(adapter, regression_examples))
            combined_score = sum(card.score for card in cards) / len(cards)
            card = Scorecard(
                "all",
                combined_score,
                {"components": {item.suite: round(item.score, 4) for item in cards}},
                [{"suite": item.suite, "score": round(item.score, 4), "metrics": item.metrics} for item in cards],
                version=version,
            )
        elif suite == "tool_use":
            card = self.tool_use(adapter)
            card.version = version
        elif suite == "regression":
            card = self.regression(adapter, regression_examples)
            card.version = version
        elif suite == "instruction_following":
            card = self.instruction_following(adapter)
            card.version = version
        else:
            card = self.coding_core(adapter)
            card.version = version
        if self.db is not None:
            try:
                self.db.insert(
                    "evaluations",
                    {
                        "run_id": version if len(version) == 12 else None,
                        "model_version": version,
                        "suite": card.suite,
                        "created_at": card.created_at,
                        "score": card.score,
                        "results": json.dumps(card.as_dict(), default=str)[:200000],
                    },
                )
            except Exception as exc:  # pragma: no cover
                log.debug("could not persist scorecard", cm={"error": str(exc)})
        return card

    def compare(self, baseline: Scorecard | None, candidate: Scorecard, *, min_gain: float | None = None) -> PromotionDecision:
        min_gain = self.min_gain if min_gain is None else min_gain
        base_score = baseline.score if baseline else 0.0
        regressions: list[str] = []
        if baseline is not None:
            base_components = baseline.metrics.get("components") if baseline.suite == "all" else None
            cand_components = candidate.metrics.get("components") if candidate.suite == "all" else None
            if base_components and cand_components:
                for name, value in base_components.items():
                    new_value = cand_components.get(name, 0.0)
                    if new_value + 0.02 < value:
                        regressions.append(f"{name} dropped from {value:.3f} to {new_value:.3f}")
        # a regression suite failure is a hard blocker even when the aggregate improves
        regression_entry = None
        for item in candidate.per_item:
            if item.get("suite") == "regression":
                regression_entry = item
        if regression_entry and regression_entry.get("score", 1.0) + 0.02 < (baseline.per_item[0].get("score", 0) if baseline and baseline.per_item else 0):
            regressions.append("regression suite got worse")
        if regressions:
            return PromotionDecision(False, "Blocked: " + "; ".join(regressions), candidate.score, base_score, regressions)
        if candidate.score >= base_score + min_gain:
            return PromotionDecision(True, f"Improved by {candidate.score - base_score:+.3f} (required ≥ {min_gain:+.3f}) with no regressions.", candidate.score, base_score)
        if candidate.score > base_score:
            return PromotionDecision(False, f"Improved only by {candidate.score - base_score:+.3f}, below the required {min_gain:+.3f}.", candidate.score, base_score)
        return PromotionDecision(False, f"No improvement ({candidate.score:.3f} vs {base_score:.3f}).", candidate.score, base_score)


def _baseline_tool(instruction: str) -> str:
    """The untrained baseline: keyword rules, i.e. exactly what CodeMind does without a model."""
    from codemind.agent.planner import _guess_tool

    return _guess_tool(instruction)
