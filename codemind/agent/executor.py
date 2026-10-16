"""The execution loop.

For every step the executor: picks the tool call (from the plan, from the model, or from a
mechanical fix), asks the permission policy, runs the tool, reads the result, and — when
something fails — diagnoses the output and inserts a *different* repair step. Two invariants
matter more than anything else here:

* **no silent success.** A task is only reported as completed when its verification step
  produced evidence that is stored with the task.
* **no infinite loops.** Identical actions are never repeated: every attempt is hashed and
  compared, repairs are budgeted, and the loop stops with an explicit reason.
"""

from __future__ import annotations

import json
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from codemind.agent.permissions import ApprovalBroker, PermissionPolicy, RiskLevel
from codemind.agent.planner import Plan, Planner
from codemind.agent.task import Step, StepState, Task, TaskStatus, TaskStore
from codemind.agent.tools import FileJournal, ToolContext, ToolRegistry, ToolResult
from codemind.config import Config
from codemind.events import BUS, Topics
from codemind.llm.base import ChatMessage, LLMError
from codemind.llm.heuristic import build_plan, diagnose, lesson_from_failure
from codemind.llm.templates import DIAGNOSIS_PROMPT, STEP_PROMPT, SUMMARY_PROMPT, SYSTEM_PROMPT, fill, render_state
from codemind.logging_setup import get_logger
from codemind.memory.knowledge import KnowledgeIndex, analyze_repository
from codemind.memory.memory import MemoryStore
from codemind.sandbox.manager import SandboxManager, Workspace
from codemind.storage.db import Database
from codemind.utils.files import human_bytes, human_duration
from codemind.utils.textutil import shorten, unified_diff

log = get_logger("agent.executor")

AUTHORING_TOOLS = {"write_file", "edit_file"}
MAX_REPAIRS_PER_TASK = 6
MAX_STEP_RETRIES_AFTER_REPAIR = 2
MAX_TOOL_SIGNATURE_REPEATS = 1


class TaskCancelled(RuntimeError):
    """Raised internally when the user cancels a task."""


@dataclass
class TaskOutcome:
    task_id: str
    status: str
    summary: str = ""
    error: str = ""
    verification: dict[str, Any] = field(default_factory=dict)
    diffs: dict[str, Any] = field(default_factory=dict)
    steps_completed: int = 0
    tool_calls: int = 0
    tokens: int = 0
    duration: float = 0.0
    lessons: list[str] = field(default_factory=list)
    planner: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "summary": self.summary,
            "error": self.error,
            "verification": self.verification,
            "diffs": self.diffs,
            "steps_completed": self.steps_completed,
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "duration": round(self.duration, 2),
            "lessons": self.lessons,
            "planner": self.planner,
        }


class AgentExecutor:
    """Runs one task at a time on a worker thread."""

    def __init__(
        self,
        *,
        config: Config,
        db: Database,
        store: TaskStore,
        registry: ToolRegistry,
        planner: Planner,
        memory: MemoryStore,
        knowledge: KnowledgeIndex,
        sandbox: SandboxManager,
        approvals: ApprovalBroker,
        policy: PermissionPolicy,
        governor: Any = None,
        model_manager: Any = None,
        github: Any = None,
        data_root: Path | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.store = store
        self.registry = registry
        self.planner = planner
        self.memory = memory
        self.knowledge = knowledge
        self.sandbox = sandbox
        self.approvals = approvals
        self.policy = policy
        self.governor = governor
        self.models = model_manager
        self.github = github
        self.data_root = Path(data_root or Path.cwd())
        self._cancel: dict[str, bool] = {}
        self._active_task: str = ""
        self._facts_cache: dict[str, dict[str, Any]] = {}

    def update_config(self, config: Config) -> None:
        self.config = config
        self.policy.update_config(config)
        self.planner.update_config(config)

    # -- control ----------------------------------------------------------
    def cancel(self, task_id: str) -> None:
        self._cancel[task_id] = True
        self.sandbox.kill_children(task_id)
        self.approvals.deny_all("task cancelled")

    def is_cancelled(self, task_id: str) -> bool:
        return bool(self._cancel.get(task_id))

    # -- main entry -------------------------------------------------------
    def run(self, task: Task, *, resume: bool = False) -> TaskOutcome:
        started = time.time()
        self._active_task = task.id
        self._cancel.pop(task.id, None)
        outcome = TaskOutcome(task_id=task.id, status=TaskStatus.RUNNING)
        journal: FileJournal | None = None
        try:
            self.store.set_status(task.id, TaskStatus.PLANNING)
            workspace = self._prepare_workspace(task)
            journal = FileJournal(self.db, backup_root=self.data_root / "backups" / task.id, task_id=task.id)
            context = self._build_context(task, workspace, journal)
            facts = self._analyse(context, task, resume=resume)
            plan = self._plan(task, facts, context, resume=resume)
            outcome.planner = plan.planner
            self._store_plan(task, plan, journal)
            self.store.set_status(task.id, TaskStatus.RUNNING)
            self.store.log_event(task.id, "plan", f"Plan ready: {len(plan.steps)} steps via {plan.planner}")
            if not resume:
                self._capture_baseline(task, context)
            self._run_steps(task, plan, context, outcome)
            self._verify(task, plan, context, outcome, journal)
            outcome.duration = time.time() - started
            outcome.summary = self._summarise(task, plan, outcome, journal, context)
            final_status = self._decide_status(task, outcome)
            outcome.status = final_status
            self.store.update(
                task.id,
                result={
                    **outcome.as_dict(),
                    "workspace": {"id": workspace.id, "path": str(workspace.path), "mode": workspace.mode},
                    "plan": plan.raw,
                    "autonomy": self.config.agent.autonomy,
                },
                summary=outcome.summary,
                verify=outcome.verification,
                tool_calls=outcome.tool_calls,
                tokens=outcome.tokens,
            )
            self.store.set_status(task.id, final_status, summary=outcome.summary, error=outcome.error)
            self._write_memory(task, plan, outcome, journal)
            self.store.log_event(task.id, "finish", f"Task {TaskStatus.LABELS.get(final_status, final_status)}: {shorten(outcome.summary, 200)}")
        except TaskCancelled:
            outcome.status = TaskStatus.CANCELLED
            outcome.error = "cancelled by the user"
            self.store.set_status(task.id, TaskStatus.CANCELLED, error=outcome.error, summary="Cancelled by the user.")
            self.store.log_event(task.id, "cancel", "Task cancelled")
        except Exception as exc:
            outcome.status = TaskStatus.FAILED
            outcome.error = f"{type(exc).__name__}: {exc}"
            log.exception("task failed", cm={"task": task.id})
            self.store.set_status(task.id, TaskStatus.FAILED, error=outcome.error, summary=f"The task stopped with an unexpected error: {outcome.error}")
            self.store.log_event(task.id, "error", outcome.error, {"traceback": traceback.format_exc()[-4000:]})
        finally:
            self._active_task = ""
            outcome.duration = time.time() - started
            outcome.diffs = journal.summary() if journal else {}
            outcome.steps_completed = sum(1 for step in self.store.steps(task.id) if step.status == StepState.DONE)
            BUS.publish(Topics.TASK_FINISHED, **outcome.as_dict())
        return outcome

    # -- preparation ------------------------------------------------------
    def _prepare_workspace(self, task: Task) -> Workspace:
        project = Path(task.project_path or self.data_root)
        mode = self.config.sandbox.workspace_mode
        if task.mode == "assisted" and not project.exists():
            raise FileNotFoundError(f"project path does not exist: {project}")
        workspace = self.sandbox.create_workspace(project, task_id=task.id, mode=mode)
        self.sandbox.save_workspace_metadata(workspace)
        self.store.log_event(
            task.id,
            "workspace",
            f"Workspace {workspace.id} ({workspace.mode}) at {workspace.path}",
            {"id": workspace.id, "path": str(workspace.path), "mode": workspace.mode, "branch": workspace.branch},
        )
        return workspace

    def _build_context(self, task: Task, workspace: Workspace, journal: FileJournal) -> ToolContext:
        def on_output(stream: str, line: str) -> None:
            BUS.publish(Topics.TASK_LOG, task_id=task.id, kind=f"output.{stream}", message=line)

        context = ToolContext(
            config=self.config,
            db=self.db,
            memory=self.memory,
            knowledge=self.knowledge,
            sandbox=self.sandbox,
            policy=self.policy,
            approvals=self.approvals,
            github=self.github,
            governor=self.governor,
            task_id=task.id,
            workspace=workspace,
            project_path=Path(task.project_path) if task.project_path else None,
            journal=journal,
            project_id=task.project_id,
            on_output=on_output,
            cancel_check=lambda: self.is_cancelled(task.id),
            data_root=self.data_root,
        )
        return context

    def _set_facts(self, task_id: str, facts: dict[str, Any]) -> None:
        self._facts_cache[task_id] = facts or {}

    def _analyse(self, context: ToolContext, task: Task, *, resume: bool) -> dict[str, Any]:
        """Always inspect before editing — on resume the stored facts are reused."""
        if resume and (task.result or {}).get("facts"):
            facts = task.result["facts"]
            context.project_id = (task.result or {}).get("project_id") or context.project_id
        else:
            result = self.registry.execute("analyze_repository", {"path": "."}, context)
            facts = result.data if result.ok else {}
            self._set_facts(task.id, facts)
            if result.ok:
                self.store.log_event(task.id, "analysis", shorten(result.output, 400), {"index": facts.get("index", {})})
        if facts.get("root"):
            context.project_id = context.project_id or None
            if context.project_id is None:
                project = self.knowledge.project_by_path(context.cwd)
                context.project_id = project["id"] if project else None
        self._check_cancel(task.id)
        return facts

    def _plan(self, task: Task, facts: dict[str, Any], context: ToolContext, *, resume: bool) -> Plan:
        if resume and task.plan.get("steps"):
            steps = [Step(**{k: v for k, v in step.items() if k in Step.__dataclass_fields__}) for step in task.plan["steps"]]
            # steps already done stay done
            stored = {step.idx: step for step in self.store.steps(task.id)}
            for step in steps:
                if step.idx in stored and stored[step.idx].status == StepState.DONE:
                    step.status = StepState.DONE
                    step.evidence = stored[step.idx].evidence
            plan = Plan(
                goal=task.plan.get("goal", task.title),
                steps=steps,
                planner=task.plan.get("planner", "deterministic"),
                verification=task.plan.get("verification", ""),
                assumptions=task.plan.get("assumptions", []),
                intent=task.plan.get("intent", ""),
                notes=task.plan.get("notes", []),
                raw=task.plan,
            )
            self.store.log_event(task.id, "plan", f"Resuming with the stored plan ({len(steps)} steps)")
            return plan
        files = []
        project_root = Path(context.cwd)
        try:
            analysis = analyze_repository(project_root, max_files=min(800, self.config.agent.analysis_depth), collect_files=True)
            files = analysis.files
            if not facts:
                facts = analysis.as_dict()
        except Exception as exc:  # pragma: no cover - analysis is defensive already
            log.warning("file scan for planning failed", cm={"error": str(exc)})
        plan = self.planner.plan(
            task.prompt,
            facts=facts,
            files=files,
            tools=self.registry.all(),
            project_id=context.project_id,
            prior_failures=self._prior_failures(task, context),
        )
        BUS.publish(Topics.TASK_PLAN, task_id=task.id, plan=plan.as_dict())
        return plan

    def _prior_failures(self, task: Task, context: ToolContext) -> list[str]:
        """Feed previous mistakes for this project back into planning."""
        if not self.config.memory.learn_from_failures:
            return []
        entries = self.memory.search(task.prompt, kinds=("mistake",), project_id=context.project_id, limit=5, min_quality=self.config.memory.min_quality)
        return [f"{entry.title}: {shorten(entry.body, 160)}" for entry in entries]

    def _store_plan(self, task: Task, plan: Plan, journal: FileJournal) -> None:
        steps = self.store.save_plan(task.id, plan.as_dict())
        if plan.notes:
            self.store.log_event(task.id, "plan.note", " ".join(plan.notes[:4]))
        log.info("executing plan", cm={"task": task.id, "steps": len(steps), "planner": plan.planner})
        return None

    def _capture_baseline(self, task: Task, context: ToolContext) -> None:
        if not (self.config.agent.verify_tests and self.config.agent.auto_detect_tests):
            return
        result = self.registry.execute("run_tests", {}, context, approval_timeout=300)
        tests = (result.data or {}).get("tests", {})
        baseline = {
            "command": (result.data or {}).get("command", ""),
            "ok": bool(result.ok),
            "tests": tests,
            "captured_at": time.time(),
        }
        self.store.update(task.id, verify={"baseline": baseline})
        self.store.log_event(
            task.id,
            "baseline",
            f"Baseline test run: {tests.get('summary', result.summary(160))}" if result.ok or tests else f"Baseline unavailable: {result.summary(160)}",
            baseline,
        )

    # -- step loop --------------------------------------------------------
    def _run_steps(self, task: Task, plan: Plan, context: ToolContext, outcome: TaskOutcome) -> None:
        queue: list[Step] = list(plan.steps)
        repairs = 0
        signatures: dict[str, int] = {}
        allowances: dict[int, int] = {}      # extra attempts granted after a repair
        retries: dict[int, int] = {}         # how often a step was retried after being repaired
        budget = self.config.security.max_tool_calls_per_task
        while queue:
            self._check_cancel(task.id)
            step = queue.pop(0)
            if step.status == StepState.DONE:
                continue
            if outcome.tool_calls >= budget:
                self.store.log_event(task.id, "limit", f"Stopped after {budget} tool calls (safety ceiling).")
                break
            call = self._choose_call(task, plan, step, context)
            if call is None:
                step.status = StepState.NEEDS_MODEL
                step.error = (
                    "This step needs code authoring, which requires a local model. Configure a model in "
                    "Models (Ollama or llama.cpp) or split the task into inspection/verification work."
                )
                self.store.update_step(task.id, step)
                self.store.log_event(task.id, "needs_model", f"Step {step.idx + 1} needs a model: {step.title}")
                continue
            tool_name, arguments = call
            signature = _signature(tool_name, arguments)
            allowance = allowances.get(step.idx, 0)
            if allowance > 0:
                allowances[step.idx] = allowance - 1
            if signatures.get(signature, 0) >= MAX_TOOL_SIGNATURE_REPEATS and allowance <= 0:
                step.status = StepState.FAILED
                step.error = "The same action was already attempted; refusing to repeat it."
                self.store.update_step(task.id, step)
                self.store.log_event(task.id, "repeat", f"Skipped a repeated action: {tool_name} {shorten(json.dumps(arguments), 120)}")
                continue
            signatures[signature] = signatures.get(signature, 0) + 1

            step.status = StepState.RUNNING
            step.started_at = time.time()
            step.tool = tool_name
            step.arguments = arguments
            step.attempts += 1
            self.store.update_step(task.id, step)
            BUS.publish(Topics.TASK_STEP, task_id=task.id, step=step.as_dict())
            self.store.log_event(task.id, "step", f"▶ {step.idx + 1}. {step.title} — {tool_name}({_arg_summary(arguments)})")

            result = self.registry.execute(tool_name, arguments, context)
            outcome.tool_calls += 1
            step.finished_at = time.time()
            step.result = {"ok": result.ok, "summary": result.summary(400), "data": _shrink(result.data)}
            step.evidence.append(
                {
                    "tool": tool_name,
                    "arguments": arguments,
                    "ok": result.ok,
                    "summary": result.summary(600),
                    "at": time.time(),
                    "risk": result.risk,
                }
            )
            if result.ok:
                step.status = StepState.DONE
                self.store.update_step(task.id, step)
                self.store.log_event(task.id, "step.ok", f"✓ {step.title}: {shorten(result.output or result.summary(200), 300)}")
                if step.origin.startswith("repair:"):
                    requeued = self._requeue_repaired(task, plan, step, queue, retries, allowances)
                    if requeued:
                        continue
                continue

            step.error = result.error or result.summary(300)
            step.status = StepState.FAILED
            self.store.update_step(task.id, step)
            self.store.log_event(task.id, "step.fail", f"✗ {step.title}: {step.error}", {"exit": (result.data or {}).get("exit_code")})
            if result.blocked or result.denied:
                step.evidence.append({"blocked": True, "reason": step.error})
                continue
            if repairs >= MAX_REPAIRS_PER_TASK:
                self.store.log_event(task.id, "repair.limit", f"Repair budget reached ({MAX_REPAIRS_PER_TASK}); continuing with the remaining steps.")
                continue
            repair = self._diagnose(task, plan, step, tool_name, arguments, result, signatures, context)
            if repair is None:
                continue
            repairs += 1
            queue.insert(0, repair)
            self.store.add_step(task.id, repair)
            plan.steps.append(repair)
            self.store.log_event(task.id, "repair", f"↻ Repair step: {repair.title} — {repair.tool}")

    def _requeue_repaired(
        self,
        task: Task,
        plan: Plan,
        repair: Step,
        queue: list[Step],
        retries: dict[int, int],
        allowances: dict[int, int],
    ) -> bool:
        """After a successful repair, retry the step it was meant to unblock.

        This is what makes self-correction real: the fix is only useful if the original action
        is attempted again and its outcome recorded. Retries are capped so a fix that does not
        help cannot spin the loop.
        """
        try:
            target_idx = int(repair.origin.split(":", 1)[1])
        except (IndexError, ValueError):
            return False
        original = next((item for item in plan.steps if item.idx == target_idx), None)
        if original is None or retries.get(target_idx, 0) >= MAX_STEP_RETRIES_AFTER_REPAIR:
            return False
        retries[target_idx] = retries.get(target_idx, 0) + 1
        allowances[target_idx] = allowances.get(target_idx, 0) + 1
        original.status = StepState.PENDING
        original.error = ""
        self.store.update_step(task.id, original)
        queue.insert(0, original)
        self.store.log_event(task.id, "retry", f"↺ Retrying step {target_idx + 1} after the repair ({retries[target_idx]}): {original.title}")
        return True

    def _choose_call(self, task: Task, plan: Plan, step: Step, context: ToolContext) -> tuple[str, dict[str, Any]] | None:
        """Decide *how* to perform a step, loading a model in only when it is needed."""
        tool = step.tool or ""
        arguments = dict(step.arguments or {})

        if not tool or tool not in self.registry.names():
            candidates = tuple(self.registry.names())
            suggestion = self.planner.suggest_tool(f"{task.prompt}\n{step.title} {step.detail}", candidates)
            if suggestion is not None:
                suggested, confidence = suggestion
                self.store.log_event(
                    task.id,
                    "advisor",
                    f"Trained adapter chose {suggested} for '{shorten(step.title, 60)}' (confidence {confidence:.2f})",
                )
                tool = suggested
                return tool, arguments
            if not tool:
                return None
        if tool in AUTHORING_TOOLS:
            prepared = self._author(task, step, context)
            if prepared is None:
                return None
            return prepared

        if tool == "run_command" and not arguments.get("command"):
            arguments["command"] = plan.verification or "python3 -m compileall -q ."
        if tool == "search_code" and not arguments.get("query"):
            arguments["query"] = " ".join(step.title.split()[:4]) or task.prompt[:40]
        if tool == "read_file" and not arguments.get("path"):
            arguments["path"] = "."
        if tool == "run_tests" and not arguments.get("command"):
            suites: list[str] = []
            if context.project_id is not None:
                suites = self.knowledge.test_suites(context.project_id)
            if suites:
                arguments["command"] = suites[0]

        if tool:
            return tool, arguments

        # No tool was chosen by the planner: ask the model, or fall back to keywords.
        backend, _reason = (self.models.resolve_backend() if self.models else (None, ""))
        if backend is not None and backend.kind != "heuristic":
            chosen = self._model_tool_choice(task, plan, step, context, backend)
            if chosen:
                return chosen
        suggestion = self.planner.suggest_tool(f"{task.prompt}\n{step.title} {step.detail}", tuple(self.registry.names()))
        if suggestion is not None:
            tool_name, confidence = suggestion
            self.store.log_event(task.id, "advisor", f"The trained adapter chose {tool_name} (confidence {confidence:.2f})")
            return tool_name, arguments
        from codemind.agent.planner import _guess_tool

        guessed = _guess_tool(f"{step.title} {step.detail}")
        return guessed, arguments

    def _author(self, task: Task, step: Step, context: ToolContext) -> tuple[str, dict[str, Any]] | None:
        """Fill in the content for a write/edit step.

        A real model writes the code when one is available. Without a model the executor
        applies *mechanical* transformations it can prove are correct (test scaffolding for an
        existing module, analysis reports, formatter fixes, missing package markers). When
        neither applies it reports honestly that the step needs a model, instead of writing
        speculative code into your repository.
        """
        arguments = dict(step.arguments or {})
        instruction = str(arguments.get("instruction") or step.detail or step.title)
        path_hint = str(arguments.get("path") or "")
        backend, _reason = (self.models.resolve_backend() if self.models else (None, ""))
        if backend is not None and backend.kind != "heuristic":
            generated = self._model_edit(task, step, context, backend, instruction, path_hint)
            if generated is not None:
                return generated
        mechanical = mechanical_edit(instruction, path_hint, context, task)
        if mechanical is not None:
            return mechanical
        return None

    def _model_edit(
        self,
        task: Task,
        step: Step,
        context: ToolContext,
        backend: Any,
        instruction: str,
        path_hint: str,
    ) -> tuple[str, dict[str, Any]] | None:
        target = Path(path_hint) if path_hint else None
        existing = ""
        if target:
            try:
                resolved = context.workspace.resolve(path_hint) if context.workspace else context.cwd / path_hint
                if resolved.exists():
                    existing = resolved.read_text(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                existing = ""
        prompt = (
            f"Task: {task.prompt}\n\nStep: {step.title}\nIntent: {step.intent or step.detail}\n"
            f"File: {path_hint or '(choose a sensible path)'}\n\n"
            + (f"Current content of {path_hint}:\n```\n{existing[:12000]}\n```\n\n" if existing else "")
            + "Return JSON only: {\"path\": \"...\", \"mode\": \"write|edit\", \"content\": \"...\", "
              "\"old_text\": \"...\", \"new_text\": \"...\", \"reason\": \"...\"}.\n"
              "Use mode 'edit' with an exact old_text excerpt when changing an existing file."
        )
        try:
            response = backend.chat(
                [ChatMessage.system(SYSTEM_PROMPT), ChatMessage.user(prompt)],
                temperature=self.config.models.temperature,
                max_tokens=self.config.models.max_output_tokens,
                json_mode=True,
                mode="edit",
            )
        except LLMError as exc:
            self.store.log_event(task.id, "model.error", f"Model call failed: {exc}")
            return None
        from codemind.llm.base import extract_json

        payload = extract_json(response.text, expect="content")
        if not isinstance(payload, dict):
            self.store.log_event(task.id, "model.error", "The model did not return usable JSON for this edit.")
            return None
        path = str(payload.get("path") or path_hint or "")
        if not path:
            return None
        if payload.get("mode") == "edit" and payload.get("old_text") is not None:
            return "edit_file", {"path": path, "old_text": payload.get("old_text"), "new_text": payload.get("new_text", "")}
        content = payload.get("content")
        if content is None:
            return None
        return "write_file", {"path": path, "content": str(content)}

    def _model_tool_choice(self, task: Task, plan: Plan, step: Step, context: ToolContext, backend: Any) -> tuple[str, dict[str, Any]] | None:
        state = {
            "workspace": str(context.cwd),
            "project": task.project_path,
            "tools": [tool.name for tool in self.registry.all()],
            "step": step.as_dict(),
            "plan_goal": plan.goal,
        }
        prompt = fill(
            STEP_PROMPT,
            index=step.idx + 1,
            total=len(plan.steps),
            title=step.title,
            intent=step.intent or step.title,
            detail=step.detail,
            verify=step.verify,
            state=render_state(state),
            recent="(no recent output)",
        ) + f"\n\n```codemind-context\n{json.dumps({'step': step.as_dict()}, default=str)[:8000]}\n```"
        try:
            response = backend.chat(
                [ChatMessage.system(SYSTEM_PROMPT), ChatMessage.user(prompt)],
                temperature=0.1,
                max_tokens=800,
                json_mode=True,
                context={"step": step.as_dict()},
                mode="step",
            )
        except LLMError:
            return None
        from codemind.llm.base import extract_json

        payload = extract_json(response.text, expect="tool")
        if not isinstance(payload, dict) or not payload.get("tool"):
            return None
        tool = str(payload["tool"])
        if self.registry.get(tool) is None:
            return None
        arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
        return tool, arguments

    # -- diagnosis --------------------------------------------------------
    def _diagnose(
        self,
        task: Task,
        plan: Plan,
        step: Step,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
        signatures: dict[str, int],
        context: ToolContext,
    ) -> Step | None:
        attempts = [{"tool": evidence.get("tool"), "arguments": evidence.get("arguments")} for evidence in step.evidence if evidence.get("tool")]
        output = (result.data or {}).get("stdout", "") + "\n" + (result.data or {}).get("stderr", "") if result.data else result.error
        diagnosis: dict[str, Any] | None = None
        backend, _reason = (self.models.resolve_backend() if self.models else (None, ""))
        if backend is not None and backend.kind != "heuristic" and output.strip():
            diagnosis = self._model_diagnosis(task, step, tool_name, arguments, output, attempts, backend)
        if diagnosis is None:
            diagnosis = diagnose(
                title=step.title,
                tool=tool_name,
                arguments=arguments,
                output=output or result.error,
                attempts=attempts,
                repo=self._facts_cache.get(task.id, {}),
            )
        if diagnosis.get("give_up") or not diagnosis.get("next"):
            self.store.log_event(task.id, "diagnosis", f"{diagnosis.get('diagnosis', 'Failure without a known fix')}")
            self._remember_failure(task, diagnosis, step, result, context)
            return None
        next_action = diagnosis["next"]
        tool = str(next_action.get("tool") or "")
        args = next_action.get("arguments") if isinstance(next_action.get("arguments"), dict) else {}
        if self.registry.get(tool) is None:
            return None
        if signatures.get(_signature(tool, args), 0) >= MAX_TOOL_SIGNATURE_REPEATS:
            self.store.log_event(task.id, "diagnosis", "The diagnosis proposed an action that was already tried; stopping this branch.")
            self._remember_failure(task, diagnosis, step, result, context)
            return None
        self.store.log_event(
            task.id,
            "diagnosis",
            f"{diagnosis.get('diagnosis', '')} → next: {tool} ({shorten(json.dumps(args), 120)})",
            {"fix": diagnosis.get("fix", "")},
        )
        self._remember_failure(task, diagnosis, step, result, context)
        return Step(
            idx=max(len(plan.steps), max((existing.idx for existing in plan.steps), default=-1) + 1),
            title=f"Repair: {str(diagnosis.get('fix') or 'follow the diagnosis')[:80]}",
            tool=tool,
            arguments=args,
            intent=str(diagnosis.get("diagnosis", ""))[:400],
            detail=str(diagnosis.get("fix", ""))[:400],
            verify="The original step can be retried afterwards.",
            origin=f"repair:{step.idx}",
        )

    def _model_diagnosis(
        self,
        task: Task,
        step: Step,
        tool_name: str,
        arguments: dict[str, Any],
        output: str,
        attempts: list[dict[str, Any]],
        backend: Any,
    ) -> dict[str, Any] | None:
        prompt = fill(
            DIAGNOSIS_PROMPT,
            title=step.title,
            tool=tool_name,
            arguments=json.dumps(arguments, default=str)[:1500],
            result=shorten(output, 600),
            output=output[-6000:],
            attempts=json.dumps(attempts[-4:], default=str)[:1500],
        )
        try:
            response = backend.chat(
                [ChatMessage.system(SYSTEM_PROMPT), ChatMessage.user(prompt)],
                temperature=0.1,
                max_tokens=700,
                json_mode=True,
                mode="diagnose",
            )
        except LLMError:
            return None
        from codemind.llm.base import extract_json

        payload = extract_json(response.text, expect="diagnosis")
        if not isinstance(payload, dict) or "diagnosis" not in payload:
            return None
        return payload

    def _remember_failure(self, task: Task, diagnosis: dict[str, Any], step: Step, result: ToolResult, context: ToolContext) -> None:
        if not self.config.memory.learn_from_failures:
            return
        lesson = lesson_from_failure(str(diagnosis.get("signature") or "unknown"), result.error or result.output, context=step.title)
        memory_id = self.memory.learn_lesson(
            lesson["title"],
            lesson["lesson"],
            project_id=context.project_id,
            evidence={"task_id": task.id, "step": step.title, "tool": step.tool, "error": (result.error or "")[:400]},
            tags=lesson["tags"],
        )
        if memory_id:
            self.store.log_event(task.id, "lesson", f"Recorded a lesson: {lesson['title']}")

    # -- verification -----------------------------------------------------
    def _verify(self, task: Task, plan: Plan, context: ToolContext, outcome: TaskOutcome, journal: FileJournal) -> None:
        self.store.set_status(task.id, TaskStatus.VERIFYING)
        stored = self.store.get(task.id, with_steps=False)
        baseline = (stored.verify if stored else {}).get("baseline", {})
        verification: dict[str, Any] = {"baseline": baseline, "verified": False, "checks": []}

        if not journal.diffs:
            verification["checks"].append({"name": "changes", "ok": True, "detail": "No files were modified."})
            for step in self.store.steps(task.id):
                if step.tool in ("run_tests", "run_command") and step.status == StepState.DONE:
                    tests = ((step.result or {}).get("data") or {}).get("tests") or {}
                    detail = tests.get("summary") or shorten((step.result or {}).get("summary") or "", 200)
                    verification["checks"].append({"name": "tests", "ok": bool(tests.get("ok", True)), "detail": detail, "tests": tests})
                    break
            verification["verified"] = all(check["ok"] for check in verification["checks"])
            verification["note"] = "Read-only task: nothing needed to change; the commands that ran are recorded above."
            outcome.verification = verification
            return

        if self.config.agent.verify_tests:
            result = self.registry.execute("run_tests", {}, context)
            tests = (result.data or {}).get("tests", {})
            check = {
                "name": "tests",
                "ok": bool(result.ok) and bool(tests.get("ok", False)),
                "detail": tests.get("summary") or result.summary(300),
                "command": (result.data or {}).get("command", ""),
                "tests": tests,
            }
            verification["checks"].append(check)
            if baseline.get("tests"):
                before = baseline["tests"]
                check["baseline"] = {"passed": before.get("passed", 0), "failed": before.get("failed", 0)}
                check["regression"] = tests.get("failed", 0) > before.get("failed", 0)
                if check["regression"]:
                    check["detail"] += " — this is a regression compared with the baseline run."

        build = self.registry.execute("run_command", {"command": "python3 -m compileall -q .", "purpose": "verify"}, context) if (context.cwd / "*.py").parent else None
        if build is not None and (context.cwd.glob("**/*.py")):
            verification["checks"].append({"name": "compile", "ok": build.ok, "detail": build.summary(200), "command": "python3 -m compileall -q ."})

        diff_result = self.registry.execute("git_diff", {}, context)
        verification["checks"].append({"name": "diff", "ok": True, "detail": f"{journal.summary()['files']} file(s) changed, +{journal.summary()['added']}/-{journal.summary()['removed']}", "diff": diff_result.output[:20000]})

        failures = [check for check in verification["checks"] if not check["ok"]]
        verification["verified"] = not failures
        verification["failed_checks"] = [check["name"] for check in failures]
        verification["note"] = (
            "All verification checks passed."
            if verification["verified"]
            else "Verification did not pass: " + ", ".join(verification["failed_checks"])
        )
        outcome.verification = verification
        self.store.update(task.id, verify=verification)
        self.store.log_event(
            task.id,
            "verify",
            f"Verification: {verification['note']}",
            {"checks": [{k: v for k, v in check.items() if k != 'diff'} for check in verification["checks"]]},
        )

    def _decide_status(self, task: Task, outcome: TaskOutcome) -> str:
        steps = self.store.steps(task.id)
        repaired = {step.origin.split(":", 1)[1] for step in steps if step.origin.startswith("repair:") and step.status == StepState.DONE and ":" in step.origin}
        failed = [step for step in steps if step.status == StepState.FAILED and str(step.idx) not in repaired]
        needs_model = [step for step in steps if step.status == StepState.NEEDS_MODEL]
        verification_ok = bool((outcome.verification or {}).get("verified"))
        if failed:
            outcome.error = f"{len(failed)} step(s) failed: " + "; ".join(shorten(step.error or step.title, 90) for step in failed[:3])
            return TaskStatus.BLOCKED
        if needs_model:
            outcome.error = (
                "Part of this task needs a local model to author code. Everything else ran and was recorded; "
                "configure a model in Models (or split the task) to finish it."
            )
            return TaskStatus.BLOCKED
        if not verification_ok:
            outcome.error = (outcome.verification or {}).get("note", "Verification did not pass.")
            return TaskStatus.FAILED
        return TaskStatus.COMPLETED

    # -- reporting --------------------------------------------------------
    def _summarise(self, task: Task, plan: Plan, outcome: TaskOutcome, journal: FileJournal, context: ToolContext) -> str:
        steps = self.store.steps(task.id)
        completed = sum(1 for step in steps if step.status == StepState.DONE)
        diff_summary = journal.summary()
        report = {
            "task": task.title,
            "status": TaskStatus.LABELS.get(self._decide_status(task, outcome), "unknown"),
            "completed": completed,
            "duration": outcome.duration,
            "planner": plan.planner,
            "goal": plan.goal,
            "steps": [{"title": step.title, "status": step.status, "tool": step.tool, "error": step.error} for step in steps],
            "files": diff_summary,
            "verification": outcome.verification,
            "tool_calls": outcome.tool_calls,
        }
        backend, _reason = (self.models.resolve_backend() if self.models else (None, ""))
        if backend is not None and backend.kind != "heuristic":
            prompt = fill(
                SUMMARY_PROMPT,
                task=task.prompt,
                status=report["status"],
                completed=completed,
                diff_summary=json.dumps(diff_summary, indent=2)[:2000],
                commands=json.dumps([check for check in (outcome.verification or {}).get("checks", [])], indent=2, default=str)[:4000],
            )
            try:
                response = backend.chat([ChatMessage.system(SYSTEM_PROMPT), ChatMessage.user(prompt)], temperature=0.2, max_tokens=900, mode="summary")
                if response.text.strip():
                    return response.text.strip()
            except LLMError:
                pass
        return render_report(report)

    def _write_memory(self, task: Task, plan: Plan, outcome: TaskOutcome, journal: FileJournal) -> None:
        if not self.config.memory.enabled:
            return
        verification_ok = bool((outcome.verification or {}).get("verified"))
        quality = 0.9 if verification_ok and journal.diffs else 0.7 if verification_ok else 0.4
        self.memory.remember(
            "task_summary",
            f"{TaskStatus.LABELS.get(outcome.status, outcome.status)}: {task.title}",
            outcome.summary[:6000],
            tags=[outcome.status, plan.intent or "task"],
            quality=quality,
            project_id=task.project_id,
            key=f"task-{task.id}",
            source="agent",
            evidence={"task_id": task.id, "tool_calls": outcome.tool_calls, "files": journal.summary()},
        )
        if verification_ok and journal.diffs:
            self.memory.remember(
                "solution",
                f"Verified change: {task.title}",
                json.dumps(
                    {
                        "request": task.prompt,
                        "files": journal.summary()["paths"],
                        "verification": (outcome.verification or {}).get("checks", [])[:3],
                    },
                    indent=2,
                    default=str,
                )[:6000],
                tags=["verified", plan.intent or "change"] + [Path(p).name for p in journal.summary()["paths"][:3]],
                quality=0.85,
                project_id=task.project_id,
                key=f"solution-{task.id}",
                source="agent",
                evidence={"task_id": task.id, "diff": journal.combined_diff(4000)},
            )
        if outcome.status == TaskStatus.BLOCKED and outcome.error:
            self.memory.learn_lesson(
                f"Blocked: {task.title}",
                outcome.error[:1500],
                project_id=task.project_id,
                evidence={"task_id": task.id, "plan": plan.goal},
                tags=["blocked", plan.intent or "task"],
            )
        self.memory.prune(self.config.memory.max_entries)

    # -- helpers ----------------------------------------------------------
    def _check_cancel(self, task_id: str) -> None:
        if self.is_cancelled(task_id):
            raise TaskCancelled()


def _signature(tool: str, arguments: dict[str, Any]) -> str:
    return f"{tool}:{json.dumps(arguments, sort_keys=True, default=str)[:400]}"


def _arg_summary(arguments: dict[str, Any], limit: int = 100) -> str:
    text = json.dumps({k: v for k, v in arguments.items() if k != "content"}, default=str)
    return text[:limit]


def _shrink(data: Any, limit: int = 4000) -> Any:
    if not isinstance(data, dict):
        return data
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in ("stdout", "stderr") and isinstance(value, str):
            out[key] = value[-limit:]
        elif key in ("diff", "entries", "memories", "results", "symbols"):
            continue
        else:
            text = json.dumps(value, default=str)
            out[key] = value if len(text) <= limit else text[:limit]
    return out


def mechanical_edit(instruction: str, path_hint: str, context: ToolContext, task: Task) -> tuple[str, dict[str, Any]] | None:
    """Deterministic edits the agent can justify without a model.

    Only transformations whose result is checkable are attempted. Each returns a concrete
    tool call, so the normal permission, journal and verification machinery still applies.
    """
    text = f"{instruction} {path_hint}".lower()
    workspace = context.cwd

    # 1. Analysis report: the deterministic planner can produce real documentation.
    if any(word in text for word in ("analysis report", "analyse the repository", "analyze the repository", "summarise the repository", "summary report")) or (
        path_hint.endswith(".md") and "report" in path_hint.lower()
    ):
        analysis = analyze_repository(workspace, max_files=1500, collect_files=False)
        report = build_analysis_report(analysis)
        target = path_hint or "docs/CODEMIND_ANALYSIS.md"
        return "write_file", {"path": target, "content": report}

    # 2. Test scaffolding for an existing module.
    if path_hint.endswith((".py",)) and (path_hint.startswith("test_") or "/test_" in path_hint or "tests/" in path_hint):
        target = (workspace / path_hint) if not Path(path_hint).is_absolute() else Path(path_hint)
        if target.exists():
            # never overwrite a test file a human wrote
            existing = target.read_text(encoding="utf-8", errors="replace")
            if "generated by CodeMind" not in existing:
                return None
        candidate = _module_for_test(path_hint, workspace)
        if candidate:
            content = build_test_scaffold(candidate, path_hint, workspace)
            if content:
                return "write_file", {"path": path_hint, "content": content}

    # 3. Formatter/auto-fix pass when the instruction asks for style or lint fixes.
    if any(word in text for word in ("format", "lint", "style", "line length")):
        for command in ("ruff format . && ruff check --fix .", "python3 -m black .", "gofmt -w .", "npx --no-install prettier --write ."):
            name = command.split(" ")[0]
            if _command_available(name):
                return "run_command", {"command": command, "purpose": "format"}

    # 4. Missing package markers (namespace package problems).
    if "missing __init__" in text or "__init__.py" in text:
        return "run_command", {"command": "find . -type d -not -path './.*' -exec sh -c 'test -f \"$0/__init__.py\" || touch \"$0/__init__.py\"' {} \\;", "purpose": "fix"}

    return None


def _command_available(name: str) -> bool:
    import shutil

    return shutil.which(name) is not None


def _module_for_test(test_path: str, workspace: Path) -> Path | None:
    stem = Path(test_path).stem
    if stem.startswith("test_"):
        stem = stem[5:]
    elif stem.endswith("_test"):
        stem = stem[: -len("_test")]
    candidates = [workspace / f"{stem}.py"] + list(workspace.glob(f"*/{stem}.py")) + list(workspace.glob(f"*/*/{stem}.py"))
    for candidate in candidates:
        if candidate.exists() and "test" not in candidate.name:
            return candidate
    return None


def build_test_scaffold(module_path: Path, test_path: str, workspace: Path) -> str:
    """Generate a real, runnable test module for an existing file."""
    try:
        source = module_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    functions = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_")]
    classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef) and not node.name.startswith("_")]
    try:
        rel = module_path.relative_to(workspace)
    except ValueError:
        rel = module_path
    module_id = str(rel.with_suffix("")).replace("/", ".")
    lines = [
        '"""Tests generated by CodeMind from real module contents.',
        "",
        f"Module under test: {rel}",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import importlib",
        "",
        "import pytest",
        "",
        f"MODULE = {module_id!r}",
        "",
        "",
        "def _load():",
        "    return importlib.import_module(MODULE)",
        "",
        "",
        "def test_module_imports():",
        "    module = _load()",
        "    assert module is not None",
        "",
    ]
    for name in functions[:10]:
        lines += [
            "",
            f"def test_has_function_{name}():",
            "    module = _load()",
            f"    assert callable(getattr(module, {name!r}, None)), {name!r} + ' is not callable'",
        ]
    for name in classes[:5]:
        lines += [
            "",
            f"def test_has_class_{name}():",
            "    module = _load()",
            f"    assert isinstance(getattr(module, {name!r}, None), type), 'missing class {name}'".replace("{name}", name),
        ]
    if not functions and not classes:
        lines += ["", "", "def test_module_has_content():", "    module = _load()", "    assert module is not None"]
    lines.append("")
    return "\n".join(lines)


def build_analysis_report(analysis: Any) -> str:
    """A factual report built from the repository scan."""
    languages = ", ".join(f"{lang} ({count} files)" for lang, count in list(analysis.languages.items())[:8])
    lines = [
        f"# Repository analysis — {analysis.name}",
        "",
        f"_Generated by CodeMind from a real filesystem scan on {time.strftime('%Y-%m-%d %H:%M')}._",
        "",
        "## Shape",
        "",
        f"- Root: `{analysis.root}`",
        f"- Files scanned: **{analysis.total_files}** ({analysis.total_lines} lines)",
        f"- Languages: {languages or 'unknown'}",
        f"- Entry points: {', '.join(f'`{p}`' for p in analysis.entry_points[:10]) or 'none detected'}",
        "",
        "## Tooling",
        "",
        f"- Test commands: {', '.join(f'`{c}`' for c in analysis.test_commands) or 'not detected'}",
        f"- Build commands: {', '.join(f'`{c}`' for c in analysis.build_commands) or 'not detected'}",
        f"- Dependency files: {', '.join(f'`{p}`' for p in analysis.dependency_files[:8]) or 'none'}",
        "",
        "## Tests",
        "",
    ]
    if analysis.test_files:
        lines += [f"- {len(analysis.test_files)} test files, for example:"]
        lines += [f"  - `{path}`" for path in analysis.test_files[:12]]
    else:
        lines.append("- No test files matched the conventional patterns; this is the largest risk in the repository.")
    lines += ["", "## Largest files", ""]
    for entry in analysis.largest_files[:6]:
        lines.append(f"- `{entry['path']}` — {human_bytes(entry['bytes'])}")
    lines += ["", "## Dependencies", ""]
    if analysis.dependencies:
        for group, deps in list(analysis.dependencies.items())[:6]:
            lines.append(f"- **{group}**: {', '.join(deps[:12]) or '—'}")
    else:
        lines.append("- No dependency declarations were found.")
    if analysis.git:
        lines += ["", "## Version control", "", f"- Branch: `{analysis.git.get('branch', '—')}`",
                  f"- Remote: `{analysis.git.get('remote', 'none')}`",
                  f"- Uncommitted files: {analysis.git.get('dirty_files', 0)}"]
    lines += ["", "## Suggested next steps", ""]
    suggestions = []
    if not analysis.test_files:
        suggestions.append("Add a test suite: the agent can scaffold pytest files for the modules listed above.")
    if not analysis.test_commands:
        suggestions.append("Declare a test command (pytest, npm test, go test) so verification can run automatically.")
    if analysis.languages.get("Other", 0) > 10:
        suggestions.append("Check for generated or vendored files that should be ignored by analysis.")
    suggestions.append("Run a task with a concrete goal; the agent indexes symbols for faster targeting.")
    lines += [f"{index + 1}. {text}" for index, text in enumerate(suggestions)]
    lines.append("")
    return "\n".join(lines)


def render_report(report: dict[str, Any]) -> str:
    """Human-readable summary built from real recorded facts."""
    verification = report.get("verification") or {}
    files = report.get("files") or {}
    lines = [f"**{report['status']}** — {report['goal']}", ""]
    lines.append(f"Planner: {report['planner']} · tool calls: {report['tool_calls']} · steps done: {report.get('completed', 0)}")
    lines.append("")
    if files:
        lines.append(f"Files changed: {files.get('files', 0)} (+{files.get('added', 0)} / -{files.get('removed', 0)})")
        for path in files.get("paths", [])[:10]:
            lines.append(f"- {path}")
        lines.append("")
    checks = verification.get("checks") or []
    if checks:
        lines.append("Evidence:")
        for check in checks:
            marker = "PASS" if check.get("ok") else "FAIL"
            lines.append(f"- [{marker}] {check.get('name')}: {shorten(str(check.get('detail', '')), 200)}")
        lines.append("")
    else:
        lines.append(verification.get("note", "No verification was recorded."))
        lines.append("")
    failed = [step for step in report.get("steps", []) if step.get("status") == StepState.FAILED]
    needs_model = [step for step in report.get("steps", []) if step.get("status") == StepState.NEEDS_MODEL]
    if failed:
        lines.append("Unresolved problems:")
        for step in failed[:6]:
            lines.append(f"- {step['title']}: {shorten(str(step.get('error') or ''), 160)}")
        lines.append("")
    if needs_model:
        lines.append("Needs a local model (authoring step):")
        for step in needs_model[:6]:
            lines.append(f"- {step['title']}")
        lines.append("")
    lines.append(f"Total time recorded by the runner: {human_duration(report.get('duration'))}")
    return "\n".join(lines)
