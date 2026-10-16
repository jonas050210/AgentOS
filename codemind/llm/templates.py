"""Prompt templates for the coding agent.

Kept in one place so the planner, the verifier and the summariser agree on the contract the
model is asked to follow. Every template demands evidence-oriented output: what was changed,
what was run, what the result was.
"""

from __future__ import annotations

from typing import Any, Sequence

SYSTEM_PROMPT = """You are CodeMind, an autonomous software engineer working directly on the user's machine.

Rules of engagement:
1. Inspect before you modify. Read the relevant files and the repository facts you are given.
2. Prefer the smallest correct change. Match the existing style of the codebase.
3. After every change, verify it: run the project's tests or a focused command and read the output.
4. If a command fails, read the error, form a hypothesis, change one thing, and try again.
   Never repeat an identical failed action.
5. Never claim success without evidence. Quote the command and its result.
6. Ask for approval only when an action is destructive, irreversible, or touches credentials.
7. When you are done, state exactly what changed, what you ran, and what remains.

Respond with JSON when the task asks for JSON. No markdown fences inside JSON values."""

PLANNER_PROMPT = """Create an execution plan for the task below.

TASK
{task}

REPOSITORY FACTS
{facts}

RELEVANT FILES
{files}

MEMORY FROM PREVIOUS SESSIONS
{memory}

AVAILABLE TOOLS
{tools}

Return JSON only, in this exact shape:
{{
  "goal": "one sentence describing the finished state",
  "assumptions": ["..."],
  "verification": "the command that will prove the work is correct",
  "steps": [
    {{"title": "short imperative title", "tool": "one of the tool names",
      "intent": "what this step does", "detail": "concrete arguments or approach",
      "verify": "how you will know it worked"}}
  ]
}}

Constraints: at most {max_steps} steps; start with inspection steps; include at least one
verification step that runs tests or a command; do not invent files that do not exist."""

STEP_PROMPT = """You are executing step {index} of {total}: {title}

INTENT: {intent}
DETAIL: {detail}
VERIFY BY: {verify}

CURRENT WORKSPACE STATE
{state}

RECENT OUTPUT
{recent}

Choose the single best tool call for this step. Return JSON only:
{{"tool": "tool name", "arguments": {{...}}, "reason": "one sentence", "expect": "what a successful result looks like"}}
If the step is already complete given the state above, return {{"tool": "noop", "reason": "why"}}."""

DIAGNOSIS_PROMPT = """A step failed. Diagnose it and propose the next action.

STEP: {title}
TOOL CALLED: {tool}
ARGUMENTS: {arguments}
EXIT/RESULT: {result}

STDOUT/STDERR (tail)
{output}

PREVIOUS ATTEMPTS (do not repeat these)
{attempts}

Return JSON only:
{{"diagnosis": "what actually went wrong",
  "fix": "what to change",
  "next": {{"tool": "tool name", "arguments": {{...}}}},
  "give_up": false}}"""

SUMMARY_PROMPT = """Summarise the task for the user's history.

TASK: {task}
STATUS: {status}
PLAN STEPS COMPLETED: {completed}

CHANGES
{diff_summary}

COMMANDS RUN AND RESULTS
{commands}

Write a concise report with these sections: What I did, Evidence, Files changed, Follow-ups.
Be specific and quote real command output. If something failed or is unverified, say so."""

REVIEW_PROMPT = """Review this diff for defects before it is accepted.

DIFF
{diff}

CONTEXT
{facts}

Return JSON only:
{{"issues": [{{"severity": "high|medium|low", "file": "...", "detail": "...", "suggestion": "..."}}],
  "verdict": "accept|revise", "reason": "one sentence"}}"""

LESSON_PROMPT = """Turn this failure into a short, reusable lesson.

FAILED ACTION: {action}
ERROR: {error}
CONTEXT: {context}

Return JSON only: {{"title": "under 70 characters", "lesson": "what to do differently next time", "tags": ["..."]}}"""


def render_tools(tools: Sequence[Any]) -> str:
    """Compact tool list for prompts: name, parameters, one-line purpose."""
    lines: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", None) or tool.get("name", "")
        description = getattr(tool, "description", None) or tool.get("description", "")
        schema = getattr(tool, "parameters", None) or tool.get("parameters", {}) or {}
        props = ", ".join((schema.get("properties") or {}).keys())
        lines.append(f"- {name}({props}): {description}")
    return "\n".join(lines) or "(no tools registered)"


def render_state(state: dict[str, Any]) -> str:
    import json

    return json.dumps(state, indent=2, default=str)[:4000]


def fill(template: str, **values: Any) -> str:
    """Format a template, leaving unknown placeholders untouched instead of raising."""
    safe = {key: ("" if value is None else value) for key, value in values.items()}
    try:
        return template.format(**safe)
    except KeyError:
        return template
