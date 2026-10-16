"""Planning: turn a request into an ordered, verifiable plan.

Two paths, one contract:

* a real local model (Ollama / llama.cpp) is asked for JSON and its answer is validated
  against the actual tool list — unknown tools, missing verification steps and oversized
  plans are corrected before anything runs;
* without a model, the deterministic planner in :mod:`codemind.llm.heuristic` produces the
  plan from repository facts and file scoring.

If a model answers with something unusable, the planner falls back to the deterministic plan
and records that it did so — the user always knows which planner produced the plan.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from codemind.agent.task import Step, plan_to_steps
from codemind.config import Config
from codemind.events import BUS, Topics
from codemind.llm.base import ChatMessage, LLMError, extract_json
from codemind.llm.heuristic import build_plan, classify_intent, score_files
from codemind.llm.templates import PLANNER_PROMPT, SYSTEM_PROMPT, fill, render_tools
from codemind.logging_setup import get_logger
from codemind.memory.memory import MemoryEntry, MemoryStore

log = get_logger("agent.planner")

VERIFICATION_TOOLS = {"run_tests", "run_command", "git_diff"}
INSPECTION_TOOLS = {"analyze_repository", "read_file", "search_code", "list_files", "knowledge_search", "project_outline", "git_status", "environment_info"}


@dataclass
class Plan:
    goal: str
    steps: list[Step]
    planner: str
    verification: str = ""
    assumptions: list[str] = field(default_factory=list)
    intent: str = ""
    notes: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "planner": self.planner,
            "verification": self.verification,
            "assumptions": self.assumptions,
            "intent": self.intent,
            "notes": self.notes,
            "steps": [step.as_dict() for step in self.steps],
        }


class Planner:
    """Produces validated plans using a model when one is available."""

    def __init__(self, config: Config, memory: MemoryStore, model_manager: Any = None, advisor: Any = None) -> None:
        self.config = config
        self.memory = memory
        self.models = model_manager
        self.advisor = advisor

    def suggest_tool(self, instruction: str, candidates: Sequence[str] = ()) -> tuple[str, float] | None:
        """Consult the promoted CodeMind adapter, when one is active."""
        if self.advisor is None:
            return None
        try:
            return self.advisor.suggest_tool(instruction, candidates)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - the adapter must never break planning
            return None

    def update_config(self, config: Config) -> None:
        self.config = config

    def plan(
        self,
        task_prompt: str,
        *,
        facts: dict[str, Any],
        files: Sequence[Any] = (),
        tools: Sequence[Any] = (),
        project_id: int | None = None,
        prior_failures: Sequence[str] = (),
    ) -> Plan:
        """Ask the best available planner and validate the answer."""
        memory_block, memories = ("", [])
        if self.config.memory.enabled:
            memory_block, memories = self.memory.context_block(
                f"{task_prompt} {' '.join(facts.get('entry_points', [])[:5])}",
                project_id=project_id,
                top_k=self.config.memory.retrieval_top_k,
            )
        tried_model = False
        notes: list[str] = []
        if self.models is not None and self.config.models.backend != "heuristic":
            backend, reason = self.models.resolve_backend()
            if backend.kind != "heuristic":
                tried_model = True
                plan = self._plan_with_model(backend, task_prompt, facts, files, tools, memory_block, notes)
                if plan is not None:
                    plan.notes.extend(notes)
                    return plan
                notes.append(f"{backend.name} produced an unusable plan; the deterministic planner was used instead.")
            else:
                notes.append(reason)
        plan = self._plan_locally(task_prompt, facts, files, tools, memory_block, prior_failures, notes)
        return plan

    # -- model path -------------------------------------------------------
    def _plan_with_model(
        self,
        backend: Any,
        task_prompt: str,
        facts: dict[str, Any],
        files: Sequence[Any],
        tools: Sequence[Any],
        memory_block: str,
        notes: list[str],
    ) -> Plan | None:
        context = {
            "task": task_prompt,
            "repo": {k: v for k, v in facts.items() if k not in ("files", "readme_excerpt")},
            "files": [
                {"path": f.path if hasattr(f, "path") else f.get("path"), "language": (f.language if hasattr(f, "language") else f.get("language"))}
                for f in score_files(task_prompt, files, limit=20)
            ],
            "max_steps": min(14, self.config.agent.max_steps),
            "memory": [entry.title for entry in self.memory.recall(task_prompt, project_id=None, top_k=5, mark_used=False)] if self.config.memory.enabled else [],
        }
        prompt = fill(
            PLANNER_PROMPT,
            task=task_prompt,
            facts=json.dumps(context["repo"], indent=2, default=str)[:6000],
            files=json.dumps(context["files"], indent=2, default=str)[:3000],
            memory=memory_block or "(nothing relevant stored yet)",
            tools=render_tools(tools),
            max_steps=context["max_steps"],
        ) + f"\n\n```codemind-context\n{json.dumps(context, default=str)[:20000]}\n```"
        started = time.time()
        try:
            response = backend.chat(
                [ChatMessage.system(SYSTEM_PROMPT), ChatMessage.user(prompt)],
                temperature=self.config.models.temperature,
                max_tokens=self.config.models.max_output_tokens,
                json_mode=backend.kind != "heuristic",
                context=context,
                mode="plan",
                task=task_prompt,
            )
        except LLMError as exc:
            notes.append(f"{backend.name} failed while planning: {exc}")
            return None
        except Exception as exc:  # pragma: no cover - defensive
            notes.append(f"{backend.name} raised {type(exc).__name__}: {exc}")
            return None
        payload = extract_json(response.text, expect="steps")
        if not isinstance(payload, dict) or not payload.get("steps"):
            log.warning("model plan was unusable", cm={"backend": backend.name, "chars": len(response.text)})
            return None
        plan = self._validate(payload, tools, planner_name=f"{backend.name}:{getattr(backend, 'model', '')}", task_prompt=task_prompt)
        plan.raw["model_duration_s"] = round(time.time() - started, 2)
        plan.raw["tokens"] = response.total_tokens
        return plan

    # -- deterministic path ----------------------------------------------
    def _plan_locally(
        self,
        task_prompt: str,
        facts: dict[str, Any],
        files: Sequence[Any],
        tools: Sequence[Any],
        memory_block: str,
        prior_failures: Sequence[str],
        notes: list[str],
    ) -> Plan:
        payload = build_plan(
            task_prompt,
            files=files,
            repo=facts,
            tools=tools,
            memory=[line for line in memory_block.splitlines()[1:6]] if memory_block else [],
            max_steps=min(14, self.config.agent.max_steps),
            prior_lessons=list(prior_failures),
        )
        plan = self._validate(payload, tools, planner_name="deterministic", task_prompt=task_prompt)
        plan.notes.extend(notes)
        return plan

    # -- validation -------------------------------------------------------
    def _validate(self, payload: dict[str, Any], tools: Sequence[Any], *, planner_name: str, task_prompt: str) -> Plan:
        names = {getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else "") for t in tools}
        names.discard("")
        raw_steps = [step for step in (payload.get("steps") or []) if isinstance(step, dict)]
        notes: list[str] = []
        if len(raw_steps) > self.config.agent.max_steps:
            notes.append(f"Plan was trimmed from {len(raw_steps)} to {self.config.agent.max_steps} steps (the configured ceiling).")
            raw_steps = raw_steps[: self.config.agent.max_steps]
        cleaned: list[dict[str, Any]] = []
        for index, step in enumerate(raw_steps):
            tool = str(step.get("tool") or "").strip()
            title = str(step.get("title") or step.get("intent") or f"Step {index + 1}").strip()
            arguments = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
            detail = str(step.get("detail") or step.get("intent") or "")
            verify = str(step.get("verify") or "")
            if not tool:
                # a promoted adapter gets the first say; the rule-based guess is the fallback
                suggestion = self.suggest_tool(f"{task_prompt}\n{title} {detail}", tuple(sorted(names)))
                if suggestion is not None:
                    tool, confidence = suggestion
                    notes.append(f"Step {index + 1}: the trained adapter chose {tool} (confidence {confidence:.2f}).")
                if not tool:
                    tool = _guess_tool(title + " " + detail)
            if tool not in names:
                notes.append(f"Replaced unknown tool {tool!r} in step {index + 1} with a safe fallback.")
                tool = "read_file" if "read" in title.lower() else "search_code" if "search" in title.lower() else "noop"
                if tool == "read_file" and not arguments.get("path"):
                    arguments = {"path": "."}
                if tool == "search_code" and not arguments.get("query"):
                    arguments = {"query": " ".join(title.split()[:4]) or task_prompt[:40]}
                if tool == "noop":
                    arguments = {"reason": f"step '{title}' had no usable tool"}
            if tool == "run_tests" and not arguments.get("command"):
                detected = (payload.get("verification") or "").strip()
                if detected and detected not in ("the diff plus a focused command",):
                    arguments["command"] = detected
            cleaned.append({"title": title, "tool": tool, "arguments": arguments, "intent": str(step.get("intent") or ""), "detail": detail, "verify": verify})
        if not any(step["tool"] in VERIFICATION_TOOLS for step in cleaned):
            verification = (payload.get("verification") or "").strip()
            tool = "run_tests" if verification or (payload.get("verification")) else "git_diff"
            arguments: dict[str, Any] = {"command": verification} if tool == "run_tests" and verification and verification != "the diff plus a focused command" else {}
            if tool == "run_tests":
                detected = (payload.get("repo", {}).get("test_commands") if isinstance(payload.get("repo"), dict) else None) or []
                if not arguments.get("command") and detected:
                    arguments = {"command": detected[0]}
                elif not arguments.get("command"):
                    tool = "git_diff"
                    arguments = {}
                    notes.append("No test command was detected, so verification falls back to a review of the diff.")
            cleaned.append({"title": "Verify the result", "tool": tool, "arguments": arguments, "intent": "Prove the change works.", "detail": "", "verify": "The command succeeds or the diff is reviewed."})
        if not any(step["tool"] in INSPECTION_TOOLS for step in cleaned[:3]) and not _is_analysis_only(cleaned):
            cleaned.insert(0, {"title": "Inspect the codebase first", "tool": "analyze_repository", "arguments": {"path": "."}, "intent": "Understand the code before editing it.", "detail": "", "verify": "Repository facts are available."})
        steps = plan_to_steps({"steps": cleaned})
        plan = Plan(
            goal=str(payload.get("goal") or task_prompt[:200]),
            steps=steps,
            planner=planner_name,
            verification=str(payload.get("verification") or "review the diff"),
            assumptions=[str(item) for item in (payload.get("assumptions") or [])][:8],
            intent=str(payload.get("intent") or classify_intent(task_prompt)),
            notes=notes,
            raw={k: v for k, v in payload.items() if k != "steps"},
        )
        return plan


def _guess_tool(text: str) -> str:
    lowered = text.lower()
    if "test" in lowered:
        return "run_tests"
    if "read" in lowered or "inspect file" in lowered:
        return "read_file"
    if "search" in lowered or "find" in lowered or "locate" in lowered:
        return "search_code"
    if "list" in lowered:
        return "list_files"
    if "commit" in lowered:
        return "git_commit"
    if "diff" in lowered:
        return "git_diff"
    if "run" in lowered or "execute" in lowered or "command" in lowered or "build" in lowered:
        return "run_command"
    if "edit" in lowered or "fix" in lowered or "implement" in lowered or "write" in lowered or "add" in lowered:
        return "edit_file"
    if "analyse" in lowered or "analyze" in lowered or "inspect" in lowered:
        return "analyze_repository"
    return "search_code"


def _is_analysis_only(steps: Sequence[dict[str, Any]]) -> bool:
    return all(step["tool"] in INSPECTION_TOOLS | {"noop"} for step in steps)
