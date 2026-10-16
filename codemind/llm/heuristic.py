"""The built-in planner: a deterministic, model-free reasoning path.

CodeMind must work on a machine that cannot host a large model — including the minimal
machine this project was developed on and any laptop without a GPU. This backend is what
makes that possible, and it is a real planner, not a stub:

* it classifies the task intent from the instruction text;
* it scores the repository's files against that intent (paths, symbols, tests, entry points);
* it emits a concrete, ordered plan whose steps carry the tool and the arguments to use;
* it diagnoses failed commands by matching real error signatures and proposes a *different*
  next action, remembering what was already tried;
* it never invents a file that analysis did not see.

When a real model is available this is still useful: it is the fallback when the model
answers with unparseable JSON, and the "compare" mode in the Training view uses it as the
baseline in the tool-selection benchmark.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from codemind.llm.base import BackendStatus, ChatMessage, LLMResponse, ToolCall, ToolSpec
from codemind.utils.textutil import shorten

# --------------------------------------------------------------------------- intents

INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "bug_fix": ("fix", "bug", "broken", "failing", "fails", "error", "crash", "regression", "traceback", "exception", "wrong", "incorrect", "not working"),
    "tests": ("test", "tests", "pytest", "unittest", "coverage", "spec", "assert", "fixture", "integration test"),
    "feature": ("add", "implement", "create", "support", "new", "introduce", "endpoint", "command", "feature", "hook", "option", "flag"),
    "refactor": ("refactor", "cleanup", "clean up", "simplify", "rename", "extract", "restructure", "deduplicate", "tidy", "move"),
    "docs": ("document", "docs", "readme", "docstring", "comment", "explain in the readme", "changelog"),
    "performance": ("slow", "performance", "optimise", "optimize", "speed up", "latency", "memory leak", "throughput", "profiling"),
    "analysis": ("analyse", "analyze", "review", "audit", "explain", "understand", "summarise", "summarize", "report", "inspect", "how does", "what does"),
    "dependency": ("install", "dependency", "dependencies", "upgrade", "pin", "requirements", "package"),
    "ci": ("ci", "pipeline", "workflow", "github actions", "build failing", "lint"),
    "security": ("security", "vulnerability", "secret", "cve", "injection", "sanitize"),
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "with", "without", "into", "from", "that", "this", "these", "those",
    "please", "make", "sure", "should", "would", "could", "when", "then", "than", "there", "here", "have",
    "has", "was", "were", "will", "can", "may", "must", "also", "but", "not", "you", "your", "our", "its",
    "it", "is", "are", "be", "to", "of", "in", "on", "at", "as", "by", "so", "if", "we", "i", "me", "my",
    "code", "file", "files", "change", "changes", "need", "needs", "want", "wants", "work", "works", "make",
}


VERIFY_PHRASES = (
    "run the test", "run tests", "run all tests", "run the suite", "run the unit test", "check the tests",
    "do the tests pass", "make sure the tests pass", "verify the tests", "test suite pass", "run pytest",
    "run npm test", "run the build", "does it pass", "check that it builds", "report what happens",
)


def classify_intent(task: str) -> str:
    text = (task or "").lower()
    authoring = any(word in text for word in ("add ", "write ", "create ", "cover ", "expand ", "generate "))
    if any(phrase in text for phrase in VERIFY_PHRASES) and not authoring:
        # "run the test suite and tell me what happens" is a verification task, not a test-writing task
        return "verify"
    scores: dict[str, int] = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        scores[intent] = sum(2 if len(word.split()) > 1 else 1 for word in keywords if word in text)
    best = max(scores.items(), key=lambda item: item[1], default=("feature", 0))
    if best[1] == 0:
        return "analysis" if "?" in task else "feature"
    # "fix the failing test" is a fix task even though it mentions tests; "add tests" is not
    if scores.get("tests", 0) >= best[1] and scores.get("tests", 0) > 0 and best[0] != "bug_fix":
        return "tests"
    return best[0]


def keywords(task: str, limit: int = 24) -> list[str]:
    words = [w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_.\-]{2,}", task or "")]
    out: list[str] = []
    for word in words:
        if word in STOPWORDS or word in out:
            continue
        out.append(word)
    return out[:limit]


def extract_references(task: str) -> dict[str, list[str]]:
    """Find concrete things the user mentioned: paths, file names, symbols, commands."""
    return {
        "paths": re.findall(r"[\w./\-]+\.[A-Za-z0-9]{1,6}\b", task or ""),
        "symbols": re.findall(r"\b([A-Z][A-Za-z0-9_]{2,}|[a-z_][a-z0-9_]{3,})\(\)", task or ""),
        "commands": re.findall(r"`([^`]+)`", task or ""),
        "backticked_paths": re.findall(r"`([\w./\-]+\.[A-Za-z0-9]{1,6})`", task or ""),
    }


# --------------------------------------------------------------------------- file scoring


@dataclass
class FileCandidate:
    path: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    language: str = ""
    is_test: bool = False
    symbols: list[str] = field(default_factory=list)


def score_files(
    task: str,
    files: Iterable[Any],
    *,
    intent: str = "feature",
    limit: int = 12,
) -> list[FileCandidate]:
    """Rank repository files by how likely they are to matter for this task."""
    tokens = set(keywords(task, 30))
    references = extract_references(task)
    named = {os.path.basename(p) for p in references["paths"] + references["backticked_paths"]}
    symbols_wanted = {s.lower() for s in references["symbols"]}
    candidates: list[FileCandidate] = []
    for entry in files:
        path = entry.path if hasattr(entry, "path") else str(entry.get("path", ""))
        language = entry.language if hasattr(entry, "language") else str(entry.get("language", ""))
        is_test = bool(entry.is_test if hasattr(entry, "is_test") else entry.get("is_test"))
        symbol_names = []
        raw_symbols = entry.symbols if hasattr(entry, "symbols") else entry.get("symbols", [])
        for symbol in raw_symbols or []:
            if isinstance(symbol, (tuple, list)) and len(symbol) >= 2:
                symbol_names.append(str(symbol[1]))
            elif isinstance(symbol, dict):
                symbol_names.append(str(symbol.get("name", "")))
        candidate = FileCandidate(path=path, language=language, is_test=is_test, symbols=symbol_names)
        lowered = path.lower()
        base = os.path.basename(lowered)
        for token in tokens:
            if token in base:
                candidate.score += 4.0
                candidate.reasons.append(f"name matches '{token}'")
            elif token in lowered:
                candidate.score += 2.0
                candidate.reasons.append(f"path matches '{token}'")
            for symbol in symbol_names:
                if token == symbol.lower():
                    candidate.score += 3.0
                    candidate.reasons.append(f"symbol {symbol}")
                    break
        if base in named or path in named:
            candidate.score += 8.0
            candidate.reasons.append("explicitly mentioned")
        for wanted in symbols_wanted:
            if any(wanted == s.lower() for s in symbol_names):
                candidate.score += 6.0
                candidate.reasons.append(f"mentioned symbol {wanted}")
        if is_test:
            candidate.score += 5.0 if intent == "tests" else 1.0
            candidate.reasons.append("test file")
        if intent == "docs" and base.startswith("readme"):
            candidate.score += 6.0
            candidate.reasons.append("documentation")
        if intent in ("bug_fix", "tests", "feature", "refactor") and not is_test and language in ("Python", "JavaScript", "TypeScript", "Go", "Rust", "Java", "C#"):
            candidate.score += 0.8
        if base in ("__init__.py",) and intent != "feature":
            candidate.score -= 2.0
        if candidates:
            pass
        candidates.append(candidate)
    candidates.sort(key=lambda c: (-c.score, len(c.path)))
    return [c for c in candidates[:limit] if c.score > 0] or candidates[: min(limit, 4)]


# --------------------------------------------------------------------------- tools info


def _tool_names(tool: Any) -> str:
    return getattr(tool, "name", None) or (tool.get("name", "") if isinstance(tool, dict) else "")


def _tool_schema(tool: Any) -> dict[str, Any]:
    return getattr(tool, "parameters", None) or (tool.get("parameters", {}) if isinstance(tool, dict) else {}) or {}


def _required(tool: Any) -> list[str]:
    return list(_tool_schema(tool).get("required") or [])


# --------------------------------------------------------------------------- planning


@dataclass
class PlanStepSpec:
    title: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    intent: str = ""
    detail: str = ""
    verify: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "tool": self.tool, "arguments": self.arguments, "intent": self.intent, "detail": self.detail, "verify": self.verify}


def build_plan(
    task: str,
    *,
    files: Sequence[Any] = (),
    repo: dict[str, Any] | None = None,
    tools: Sequence[Any] = (),
    memory: Sequence[str] = (),
    max_steps: int = 12,
    prior_lessons: Sequence[str] = (),
) -> dict[str, Any]:
    """Produce a concrete plan. Pure function: same inputs, same plan."""
    repo = repo or {}
    available = {_tool_names(tool) for tool in tools} or {"read_file", "search_code", "write_file", "run_tests"}
    intent = classify_intent(task)
    tokens = keywords(task, 8)
    references = extract_references(task)
    ranked = score_files(task, files, intent=intent, limit=6)
    test_command = (repo.get("test_commands") or [None])[0]
    build_command = (repo.get("build_commands") or [None])[0]

    steps: list[PlanStepSpec] = []

    def add(title: str, tool: str, arguments: dict[str, Any], intent_text: str, detail: str = "", verify: str = "") -> None:
        if tool in available or tool in ("analyze_repository", "noop"):
            steps.append(PlanStepSpec(title=title, tool=tool, arguments=arguments, intent=intent_text, detail=detail, verify=verify))

    # Every task starts by establishing the facts. An analysis task needs them most of all: its
    # whole deliverable is the report built from them, so the step is planned, not hidden.
    add(
        "Inspect the repository layout",
        "analyze_repository",
        {"path": "."},
        "Establish the facts: languages, test command, entry points.",
        verify="A repository summary is stored and shown in the plan.",
    )
    search_terms = " ".join(tokens[:4]) or " ".join(references["paths"][:2]) or task[:60]
    if search_terms.strip():
        add(
            "Locate the relevant code",
            "search_code",
            {"query": search_terms.strip(), "limit": 40},
            "Find the files that mention the task's key terms.",
            detail=f"Search terms: {search_terms.strip()}",
        )
    for candidate in ranked[: (3 if intent != "analysis" else 4)]:
        add(
            f"Read {candidate.path}",
            "read_file",
            {"path": candidate.path},
            "Understand the current implementation before changing it.",
            detail="; ".join(candidate.reasons[:2]),
        )
    if intent == "tests" and not any(c.is_test for c in ranked):
        for test_file in (repo.get("test_files") or [])[:2]:
            add(f"Read existing test {test_file}", "read_file", {"path": test_file}, "Match the project's testing style.")

    if intent == "bug_fix":
        add(
            "Reproduce the failure",
            "run_command",
            {"command": _reproduce_command(test_command, references), "purpose": "reproduce"},
            "See the failure for real instead of guessing.",
            verify="The command output shows the same error the user reported.",
        )
        target = ranked[0].path if ranked else references["paths"][0] if references["paths"] else ""
        if target:
            add(f"Fix the defect in {target}", "edit_file", {"path": target, "instruction": task}, "Apply the smallest correct change.", verify="The reproduction command no longer fails.")
    elif intent == "tests":
        target = next((c.path for c in ranked if not c.is_test), references["paths"][0] if references["paths"] else "")
        suggested = _suggest_test_path(target, repo)
        existing_tests = list(repo.get("test_files") or [])
        asks_to_write = any(word in task.lower() for word in ("add test", "write test", "new test", "create test", "more test", "coverage", "cover "))
        if existing_tests and not asks_to_write:
            for test_file in existing_tests[:3]:
                add(f"Read existing test {test_file}", "read_file", {"path": test_file}, "See what is already covered before adding anything.")
            add("Assess the coverage gap", "noop", {"reason": "assess which behaviour is missing from the existing suite"}, "Decide whether a new test is genuinely needed.")
        else:
            add(
                "Add focused tests",
                "write_file",
                {"path": suggested, "instruction": task, "content": ""},
                "Cover the behaviour the user asked about.",
                verify="The new tests run and pass.",
            )
    elif intent == "docs":
        target = next((c.path for c in ranked if os.path.basename(c.path).lower().startswith("readme")), "README.md")
        add("Update the documentation", "write_file", {"path": target, "instruction": task, "content": ""}, "Describe the behaviour accurately.", verify="Documentation builds/reads correctly.")
    elif intent == "refactor":
        for candidate in ranked[:2]:
            add(f"Refactor {candidate.path}", "edit_file", {"path": candidate.path, "instruction": task}, "Improve structure without changing behaviour.", verify="Tests still pass and the diff is behaviour-neutral.")
    elif intent == "dependency":
        files_hint = repo.get("dependency_files") or ["requirements.txt"]
        add("Update dependency declarations", "edit_file", {"path": files_hint[0], "instruction": task}, "Pin or add the package the user asked for.")
    elif intent in ("performance", "security"):
        for candidate in ranked[:2]:
            add(f"Review {candidate.path}", "edit_file", {"path": candidate.path, "instruction": task}, f"Apply the {intent} change in the hot path.")
    elif intent == "analysis":
        add("Summarise the findings", "noop", {"reason": "analysis only"}, "Report what the code does; no edits are made.")
    elif intent == "verify":
        pass  # verification is handled by the dedicated block below
    elif intent in ("feature", "ci"):
        target = (
            references["paths"][0]
            if references["paths"] and any(references["paths"][0].endswith(ext) for ext in (".py", ".ts", ".js", ".go", ".rs"))
            else (ranked[0].path if ranked else _suggest_new_module(task, repo))
        )
        add(
            f"Implement the change in {target}",
            "write_file" if not _file_exists(files, target) else "edit_file",
            {"path": target, "instruction": task},
            "Write the smallest change that satisfies the request.",
            verify="The new behaviour is exercised by a command or a test.",
        )

    if intent == "verify":
        add("Run the project's tests as they are", "run_tests", {}, "Report the actual outcome without changing anything.", verify="The command output is quoted in the summary.")
        add("Report the findings", "noop", {"reason": "summarise the test output"}, "Explain what passed, what failed and why.")
    if intent not in ("analysis", "docs", "verify"):
        if test_command:
            add("Run the test suite", "run_tests", {"command": test_command}, "Verify the change against the project's own tests.", verify=f"`{test_command}` reports no new failures.")
        elif build_command:
            add("Build and check", "run_command", {"command": build_command, "purpose": "verify"}, "Compile/type-check to catch mistakes.", verify=f"`{build_command}` exits 0.")
    add("Show the final diff", "git_diff", {}, "Record exactly what changed for the user.", verify="The diff matches the intended change.")

    # de-duplicate while preserving order
    seen: set[tuple[str, str]] = set()
    unique: list[PlanStepSpec] = []
    for step in steps:
        key = (step.tool, json.dumps(step.arguments, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        unique.append(step)
    unique = unique[: max(2, max_steps)]

    return {
        "goal": _goal_statement(task, intent),
        "intent": intent,
        "assumptions": _assumptions(repo, ranked, intent),
        "verification": test_command or build_command or "the diff plus a focused command",
        "planner": "heuristic",
        "keywords": tokens,
        "memory": list(memory)[:5],
        "lessons": list(prior_lessons)[:5],
        "steps": [step.as_dict() for step in unique],
    }


# A prompt that already starts with an instruction verb does not need one added in front of it
# ("Explain: Analyse this repository" reads badly).
_SELF_DESCRIBING_VERBS = {
    "add", "analyse", "analyze", "assess", "build", "check", "clean", "compare", "convert", "cover",
    "create", "debug", "delete", "describe", "document", "ensure", "explain", "extract", "find",
    "fix", "harden", "implement", "improve", "inspect", "install", "investigate", "list", "make",
    "migrate", "optimise", "optimize", "profile", "read", "reduce", "refactor", "remove", "rename",
    "repair", "report", "research", "restructure", "review", "run", "search", "show", "simplify",
    "speed", "summarise", "summarize", "test", "trace", "understand", "update", "upgrade", "verify",
    "write",
}


def _goal_statement(task: str, intent: str) -> str:
    verb = {
        "verify": "Verify",
        "bug_fix": "Fix",
        "tests": "Cover with tests",
        "docs": "Document",
        "refactor": "Restructure",
        "dependency": "Update dependencies for",
        "performance": "Make faster",
        "security": "Harden",
        "analysis": "Explain",
        "ci": "Repair the pipeline for",
        "feature": "Implement",
    }.get(intent, "Complete")
    cleaned = " ".join((task or "").split())
    if not cleaned:
        return verb
    first = re.split(r"[\s,.:;!?]+", cleaned.lower(), maxsplit=1)[0]
    if first in _SELF_DESCRIBING_VERBS:
        return shorten(cleaned[:1].upper() + cleaned[1:], 160)
    return f"{verb}: {shorten(cleaned, 160)}"


def _assumptions(repo: dict[str, Any], ranked: Sequence[FileCandidate], intent: str) -> list[str]:
    out: list[str] = []
    if repo.get("primary_language"):
        out.append(f"The project is primarily {repo['primary_language']}.")
    if repo.get("test_commands"):
        out.append(f"Tests are run with `{repo['test_commands'][0]}`.")
    else:
        out.append("No test command was detected; verification will use a build or focused command.")
    if ranked:
        out.append(f"The most likely files are {', '.join(c.path for c in ranked[:3])}.")
    if not repo.get("git"):
        out.append("This directory is not a git repository; the diff view falls back to file snapshots.")
    return out


def _file_exists(files: Sequence[Any], path: str) -> bool:
    for entry in files:
        entry_path = entry.path if hasattr(entry, "path") else str(entry.get("path", ""))
        if entry_path == path:
            return True
    return False


def _suggest_test_path(target: str, repo: dict[str, Any]) -> str:
    if not target:
        return "tests/test_new.py"
    path = Path(target)
    test_dir = "tests" if any(str(f).startswith("tests/") for f in (repo.get("test_files") or [])) else (str(Path(repo.get("test_files", ["tests/x"])[0]).parent) if repo.get("test_files") else "tests")
    return str(Path(test_dir) / f"test_{path.stem}.py")


def _suggest_new_module(task: str, repo: dict[str, Any]) -> str:
    words = [w for w in keywords(task, 3) if w.isidentifier()]
    stem = "_".join(words) or "change"
    package = ""
    entry = (repo.get("entry_points") or [""])[0]
    if entry.endswith(".py") and "/" in entry:
        package = str(Path(entry).parent) + "/"
    return f"{package}{stem}.py"


def _reproduce_command(test_command: str | None, references: dict[str, list[str]]) -> str:
    if references["commands"]:
        return references["commands"][0]
    if test_command:
        return test_command
    return "python3 -m compileall -q ."


# --------------------------------------------------------------------------- diagnosis

ERROR_SIGNATURES: list[tuple[str, str, str]] = [
    (r"ModuleNotFoundError: No module named ['\"]?([A-Za-z_][\w.]*)['\"]?", "missing_module", "A required Python module is not importable."),
    (r"No module named ['\"]?([A-Za-z_][\w.]*)['\"]?", "missing_module", "The interpreter cannot find a module the command needs."),
    (r"externally-managed-environment", "managed_python", "The system Python refuses package installs (PEP 668)."),
    (r"ImportError: cannot import name ['\"]([^'\"]+)['\"]", "bad_import", "An import name does not exist where it is expected."),
    (r"IndentationError|TabError", "indentation", "The edit produced inconsistent indentation."),
    (r"SyntaxError: (.+)", "syntax", "The file does not parse."),
    (r"AssertionError(?::\s*(.*))?", "assertion", "An expectation in the code no longer holds."),
    (r"FileNotFoundError: .*['\"]([^'\"]+)['\"]", "missing_path", "A path used by the code does not exist."),
    (r"PermissionError|EACCES", "permission", "The process is not allowed to touch that path."),
    (r"command not found|not recognized as an internal or external command", "missing_command", "The command is not installed in this environment."),
    (r"Address already in use", "port_in_use", "The port is occupied by another process."),
    (r"CUDA out of memory|OutOfMemoryError|torch\.cuda\.OutOfMemoryError", "oom", "GPU memory ran out."),
    (r"TypeError: (.+)", "type_error", "An argument or attribute has the wrong type."),
    (r"KeyError: (.+)", "key_error", "A mapping lookup used a key that is not present."),
    (r"Timeout|timed out", "timeout", "The command exceeded its time budget."),
    (r"FAILED (tests/[^\s]+|[\w./]+::[\w:]+)", "test_failure", "One or more tests failed."),
    (r"error TS\d+", "typescript", "TypeScript reported a type error."),
    (r"ruff|flake8|E501|F401|eslint", "lint", "A linter reported a style or correctness problem."),
    (r"npm ERR!|yarn error", "npm_error", "The package manager reported a failure."),
    (r"fatal: not a git repository", "no_git", "The directory is not a git repository."),
    (r"error: pathspec .* did not match", "bad_pathspec", "Git was given a path that does not exist."),
    (r"denied|403|401|authentication", "auth", "A remote service rejected the credentials."),
]


def diagnose(
    *,
    title: str,
    tool: str,
    arguments: dict[str, Any],
    output: str,
    attempts: Sequence[dict[str, Any]] = (),
    repo: dict[str, Any] | None = None,
    files: Sequence[Any] = (),
) -> dict[str, Any]:
    """Match the failure against known signatures and propose a *new* next action."""
    repo = repo or {}
    text = output or ""
    kind, explanation = "unknown", "The command failed for a reason without a known signature."
    detail = ""
    for pattern, name, note in ERROR_SIGNATURES:
        match = re.search(pattern, text)
        if match:
            kind, explanation = name, note
            detail = match.group(0)[:300]
            if match.groups():
                detail = f"{detail} (captured: {', '.join(g for g in match.groups() if g)})"
            break

    tried = {json.dumps({"tool": a.get("tool"), "args": a.get("arguments")}, sort_keys=True, default=str) for a in attempts}
    next_action: dict[str, Any] | None = None
    fix = ""

    if kind == "managed_python":
        fix = "Install into an isolated virtualenv instead of the system interpreter (CodeMind does this automatically for pip installs in a workspace)."
        next_action = {"tool": "install_package", "arguments": {"manager": "pip", "scope": "workspace"}}
    elif kind == "missing_module":
        module = (re.search(r"No module named ['\"]?([A-Za-z_][\w.]*)['\"]?", text) or [None, ""])[1]
        cli_tools = {"pytest", "black", "ruff", "flake8", "mypy", "coverage", "pylint", "isort", "tox", "nox", "vitest", "jest"}
        if module in cli_tools:
            fix = f"'{module}' is not installed for this interpreter. Installing it is the direct fix; the alternative is pointing the test command at an available runner."
            next_action = {"tool": "install_package", "arguments": {"package": module, "manager": "pip"}}
        elif tool == "run_tests":
            fix = f"The test run needs '{module}'. Install it or add it to the dependency file."
            next_action = {"tool": "run_command", "arguments": {"command": f"python3 -c \"import {module}\"", "purpose": "probe"}}
        else:
            fix = f"Add a guarded import for '{module}' or correct the module path."
            path = arguments.get("path", "")
            next_action = {"tool": "read_file", "arguments": {"path": path}} if path else {"tool": "search_code", "arguments": {"query": module, "limit": 20}}
    elif kind == "bad_import":
        name = (re.search(r"cannot import name ['\"]([^'\"]+)['\"]", text) or [None, ""])[1]
        fix = f"'{name}' moved or was renamed; find its real location and update the import."
        next_action = {"tool": "search_code", "arguments": {"query": name, "limit": 20}}
    elif kind in ("syntax", "indentation"):
        path = arguments.get("path") or _first_path_in(text)
        fix = "Re-read the file around the reported line and repair the edit."
        if path:
            next_action = {"tool": "read_file", "arguments": {"path": path, "start": 1, "limit": 200}}
    elif kind == "assertion" or kind == "test_failure":
        fix = "The implementation and the test disagree: read the failing test and the code it exercises."
        target = re.search(r"(tests?/[\w./\-]+\.py|[\w./\-]+\.py)::", text)
        if target:
            next_action = {"tool": "read_file", "arguments": {"path": target.group(1)}}
        elif arguments.get("path"):
            next_action = {"tool": "read_file", "arguments": {"path": arguments["path"]}}
    elif kind == "missing_path":
        path = (re.search(r"FileNotFoundError: .*['\"]([^'\"]+)['\"]", text) or [None, ""])[1]
        fix = f"The path '{path}' does not exist; list the directory and use the real name."
        parent = str(Path(path).parent) if path else "."
        next_action = {"tool": "list_files", "arguments": {"path": parent or "."}}
    elif kind == "permission":
        fix = "Choose a writable location inside the workspace, or explain the permission requirement."
        next_action = {"tool": "run_command", "arguments": {"command": "ls -ld . && id", "purpose": "diagnose"}}
    elif kind == "missing_command":
        command = (arguments.get("command") or "").split(" ")[0]
        candidate = (re.search(r"command not found", text) and command) or ""
        fix = f"'{candidate or 'that command'}' is not installed here; either install it or use an available alternative."
        next_action = {"tool": "run_command", "arguments": {"command": "which python3 node npm go cargo make", "purpose": "probe"}} if (repo.get("test_commands") or True) else None
    elif kind == "port_in_use":
        fix = "Stop the process holding the port or configure a different one."
        next_action = {"tool": "run_command", "arguments": {"command": "ss -ltnp 2>/dev/null | head -20", "purpose": "diagnose"}}
    elif kind == "oom":
        fix = "Reduce the batch size / sequence length, or free VRAM before retrying."
        next_action = {"tool": "noop", "arguments": {"reason": "reduce batch size in Training settings"}}
    elif kind == "type_error" or kind == "key_error":
        fix = "Inspect the call site named in the traceback and make the types/keys agree."
        path = _first_path_in(text)
        next_action = {"tool": "read_file", "arguments": {"path": path}} if path else None
    elif kind == "timeout":
        fix = "The command needs longer or must be narrowed; re-run it focused on a single file."
        command = arguments.get("command", "")
        if command and not any(x in command for x in ("-q", "--quiet", "tests/", "::")):
            narrowed = f"{command} --maxfail=1 -x" if "pytest" in command else command
            next_action = {"tool": "run_command", "arguments": {"command": narrowed, "purpose": "verify", "timeout": 900}}
    elif kind == "typescript" or kind == "lint":
        fix = "Fix the reported lines, then re-run the check."
        path = _first_path_in(text)
        next_action = {"tool": "read_file", "arguments": {"path": path}} if path else None
    elif kind == "no_git":
        fix = "Git operations are unavailable here; fall back to diffing files directly."
        next_action = {"tool": "git_diff", "arguments": {"snapshot": True}}
    elif kind == "npm_error":
        fix = "The package manager failed - usually missing dependencies or a broken lockfile. Install first."
        next_action = {"tool": "run_command", "arguments": {"command": "npm ci --no-audit --no-fund", "purpose": "install"}}
    elif kind == "auth":
        fix = "Credentials were rejected. Nothing will be retried automatically; check the token configuration."
        next_action = None

    give_up = next_action is None
    signature = json.dumps(next_action, sort_keys=True, default=str) if next_action else ""
    if signature and signature in tried and next_action is not None:
        # never propose the action that just failed
        next_action = {"tool": "search_code", "arguments": {"query": " ".join(str(detail)[:60].split()[:4]) or "error", "limit": 20}}
        fix += " The previous action was already tried, so the plan changes approach."
        if json.dumps(next_action, sort_keys=True, default=str) in tried:
            give_up = True

    return {
        "diagnosis": f"{explanation} [{kind}]{': ' + detail if detail else ''}".strip(),
        "fix": fix or "No automatic fix is available for this failure.",
        "next": next_action,
        "give_up": give_up,
        "signature": kind,
        "planner": "heuristic",
    }


def _first_path_in(text: str) -> str:
    match = re.search(r'File "([^"]+)"', text or "")
    if match:
        return match.group(1)
    match = re.search(r"\b([\w./\-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|php|cs|c|cpp|h|json|toml|yaml|yml))\b", text or "")
    return match.group(1) if match else ""


def lesson_from_failure(kind: str, message: str, *, context: str = "") -> dict[str, Any]:
    """Turn a diagnosed failure into a memory entry, so it is not repeated."""
    mapping = {
        "missing_module": ("Install or declare the dependency before running the tests", "Run `python3 -c \"import X\"` or the project install command first; add missing packages to the dependency file."),
        "missing_command": ("Check tool availability before invoking it", "Use `which <tool>` or the availability list in the plan before calling a command that may not exist."),
        "assertion": ("Read the failing test before editing the implementation", "The expectation is the spec: open the test file and the code it exercises in the same step."),
        "timeout": ("Scope long test runs", "Prefer `-x --maxfail=1` and a single file/test id over a full suite when iterating."),
        "permission": ("Stay inside the workspace for writes", "Writable paths are the sandbox workspace and the project checkout; explain rather than chmod when a path is protected."),
        "no_git": ("Do not assume a git repository", "Check `git rev-parse` first, and fall back to file snapshots for diffs."),
        "npm_error": ("Install dependencies before running npm scripts", "Run `npm ci` once per workspace, then run the script."),
        "indentation": ("Re-read the file after every edit", "Confirm the edited region parses (`python3 -m compileall`) before running the wider suite."),
        "port_in_use": ("Check for a listening process before starting a server", "Use `ss -ltnp` and pick a free port."),
    }
    title, body = mapping.get(kind, ("Command failed: capture the real error before retrying", "Quote the failing command and its stderr, then change one variable at a time."))
    return {"title": title[:80], "lesson": body, "tags": [kind, "agent"], "context": context, "error": message[:400]}


# --------------------------------------------------------------------------- backend


class HeuristicBackend:
    """Backend-compatible wrapper so the planner can be selected like any other model."""

    kind = "heuristic"
    name = "builtin"
    model = "builtin-planner"

    def __init__(self) -> None:
        self._last_plan: dict[str, Any] | None = None

    def status(self) -> BackendStatus:
        return BackendStatus(
            name=self.name,
            kind=self.kind,
            available=True,
            detail="deterministic planner (no model required)",
            models=[self.model],
            endpoint="in-process",
            latency_ms=0.0,
            version="1",
        )

    def list_models(self) -> list[str]:
        return [self.model]

    def plan(self, task: str, **kwargs: Any) -> dict[str, Any]:
        return build_plan(task, **kwargs)

    def diagnose(self, **kwargs: Any) -> dict[str, Any]:
        return diagnose(**kwargs)

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        stop: Sequence[str] | None = None,
        on_token: Callable[[str], None] | None = None,
        **extra: Any,
    ) -> LLMResponse:
        """Answer planner prompts with the deterministic planner.

        The prompts carry machine-readable JSON blocks (the repository facts, the file
        candidates and the step spec), which is exactly what the deterministic path needs -
        the same prompt engineering works for both a real model and this one.
        """
        joined = "\n\n".join(message.content for message in messages)
        context = extra.get("context") or _context_from_prompt(joined)
        task = extra.get("task") or _task_from_prompt(joined)
        payload: dict[str, Any]
        if "execution plan" in joined.lower() or extra.get("mode") == "plan":
            plan = build_plan(
                task,
                files=context.get("files", []),
                repo=context.get("repo", {}),
                tools=tools or context.get("tools", []),
                max_steps=int(context.get("max_steps", 12)),
                memory=context.get("memory", []),
            )
            self._last_plan = plan
            payload = plan
        elif "diagnose" in joined.lower() or extra.get("mode") == "diagnose":
            payload = diagnose(
                title=context.get("title", ""),
                tool=context.get("tool", ""),
                arguments=context.get("arguments", {}),
                output=context.get("output", ""),
                attempts=context.get("attempts", []),
                repo=context.get("repo", {}),
                files=context.get("files", []),
            )
        else:
            spec = context.get("step") or {}
            payload = {
                "tool": spec.get("tool", "noop"),
                "arguments": spec.get("arguments", {}),
                "reason": spec.get("detail") or "planned by the deterministic planner",
                "expect": spec.get("verify", ""),
            }
        text = json.dumps(payload, indent=2, default=str)
        if on_token:
            try:
                on_token(text)
            except Exception:  # pragma: no cover
                pass
        return LLMResponse(text=text, backend=self.name, model=self.model, duration_s=0.0, finish_reason="stop")


def _context_from_prompt(prompt: str) -> dict[str, Any]:
    """Recover the machine-readable context that the planner embeds in its prompts."""
    marker = "```codemind-context"
    if marker in prompt:
        try:
            blob = prompt.split(marker, 1)[1].split("```", 1)[0]
            return json.loads(blob)
        except (json.JSONDecodeError, IndexError):
            pass
    for block in re.findall(r"\{[\s\S]{20,}?\n\}", prompt):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and ("repo" in parsed or "files" in parsed or "step" in parsed):
            return parsed
    return {}


def _task_from_prompt(prompt: str) -> str:
    match = re.search(r"(?:^|\n)TASK\n(.+?)(?:\n\n|\nREPOSITORY)", prompt, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"TASK: (.+)", prompt)
    return match.group(1).strip() if match else prompt.strip()[:400]
