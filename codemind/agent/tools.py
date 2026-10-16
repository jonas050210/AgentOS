"""The tools the agent may call, and the registry that polices them.

Every tool is real: it touches the filesystem, runs a process, queries SQLite or calls the
GitHub API, and returns a structured result that is written to the task trace and the audit
log. Nothing here fabricates an outcome, and every write is journalled as a diff so the UI can
show exactly what changed and offer the previous content back.

Risk levels are declared per tool and refined per call by
:class:`~codemind.agent.permissions.PermissionPolicy` (for example ``run_command`` is
read-only for ``ls`` and destructive for ``rm -rf``).
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from codemind.agent.permissions import ApprovalBroker, PermissionPolicy, RiskLevel, summarize_arguments
from codemind.config import Config
from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.memory.knowledge import KnowledgeIndex, analyze_repository
from codemind.memory.memory import MemoryStore
from codemind.sandbox.manager import CommandDenied, SandboxManager, Workspace
from codemind.storage.db import Database
from codemind.utils.files import atomic_write_text, ensure_dir, human_bytes, read_text, resolve_within
from codemind.utils.process import run_command
from codemind.utils.redaction import GLOBAL_REDACTOR, redact_text
from codemind.utils.textutil import diff_stats, strip_ansi, tail_lines, truncate_middle, unified_diff

log = get_logger("agent.tools")

MAX_READ_LINES = 600
MAX_TOOL_OUTPUT = 30_000


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration: float = 0.0
    risk: str = RiskLevel.READ.value
    blocked: bool = False
    denied: bool = False
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "output": self.output,
            "data": self.data,
            "error": self.error,
            "duration": round(self.duration, 4),
            "risk": self.risk,
            "blocked": self.blocked,
            "denied": self.denied,
            "truncated": self.truncated,
        }

    def summary(self, limit: int = 300) -> str:
        text = self.error or self.output or ""
        text = " ".join(text.split())
        return text[: limit - 1] + "…" if len(text) > limit else text


class FileJournal:
    """Keeps the before-image of every modified file and records unified diffs."""

    def __init__(self, db: Database, *, backup_root: Path, task_id: str) -> None:
        self.db = db
        self.backup_root = Path(backup_root)
        self.task_id = task_id
        self.diffs: list[dict[str, Any]] = []
        self._before: dict[str, str] = {}

    def snapshot(self, path: Path) -> None:
        key = str(path)
        if key in self._before:
            return
        try:
            self._before[key] = read_text(path) if path.exists() else ""
        except OSError:
            self._before[key] = ""

    def before(self, path: Path) -> str:
        return self._before.get(str(path), "")

    def backup(self, path: Path) -> Path | None:
        if not path.exists():
            return None
        target_dir = self.backup_root / time.strftime("%Y%m%d-%H%M%S")
        ensure_dir(target_dir)
        target = target_dir / path.name
        try:
            shutil.copy2(path, target)
            return target
        except OSError:
            return None

    def record(self, path: Path, new_text: str, kind: str) -> dict[str, Any]:
        old = self.before(path)
        self.snapshot(path)
        old = self._before.get(str(path), old)
        diff = unified_diff(old, new_text, path=str(path))
        stats = diff_stats(diff)
        entry = {
            "path": str(path),
            "kind": kind,
            "added": stats["added"],
            "removed": stats["removed"],
            "diff": diff,
        }
        self.diffs.append(entry)
        try:
            self.db.insert(
                "task_diffs",
                {
                    "task_id": self.task_id,
                    "ts": time.time(),
                    "path": str(path),
                    "kind": kind,
                    "diff": truncate_middle(diff, 120_000),
                    "added": stats["added"],
                    "removed": stats["removed"],
                },
            )
        except Exception as exc:  # pragma: no cover
            log.debug("could not persist diff", cm={"error": str(exc)})
        BUS.publish(Topics.TASK_DIFF, task_id=self.task_id, **entry)
        return entry

    def summary(self) -> dict[str, Any]:
        files = {entry["path"] for entry in self.diffs}
        return {
            "files": len(files),
            "changes": len(self.diffs),
            "added": sum(entry["added"] for entry in self.diffs),
            "removed": sum(entry["removed"] for entry in self.diffs),
            "paths": sorted(files),
        }

    def combined_diff(self, limit: int = 200_000) -> str:
        return truncate_middle("\n".join(entry["diff"] for entry in self.diffs), limit)


@dataclass
class ToolContext:
    """Everything a tool may touch. Passed explicitly so tools stay testable."""

    config: Config
    db: Database
    memory: MemoryStore
    knowledge: KnowledgeIndex
    sandbox: SandboxManager
    policy: PermissionPolicy
    approvals: ApprovalBroker
    github: Any = None
    governor: Any = None
    task_id: str = ""
    workspace: Workspace | None = None
    project_path: Path | None = None
    journal: FileJournal | None = None
    project_id: int | None = None
    on_output: Callable[[str, str], None] | None = None
    cancel_check: Callable[[], bool] | None = None
    data_root: Path = field(default_factory=lambda: Path.cwd())

    @property
    def cwd(self) -> Path:
        if self.workspace is not None:
            return self.workspace.path
        return Path(self.project_path or Path.cwd())

    def emit(self, stream: str, text: str) -> None:
        if self.on_output is not None:
            try:
                self.on_output(stream, text)
            except Exception:  # pragma: no cover
                pass

    def governor_plan(self) -> Any:
        return self.governor.plan if self.governor is not None else None


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any], ToolContext], ToolResult]
    risk: RiskLevel = RiskLevel.READ
    category: str = "general"
    writes_files: bool = False
    requires_network: bool = False
    examples: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "risk": self.risk.value,
            "risk_label": self.risk.label,
            "category": self.category,
            "writes_files": self.writes_files,
            "requires_network": self.requires_network,
        }

    def spec(self) -> Any:
        from codemind.llm.base import ToolSpec

        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)


def _schema(properties: dict[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required)}


# --------------------------------------------------------------------------- read tools


def _resolve(ctx: ToolContext, path: str, *, must_exist: bool = True) -> Path:
    raw = path or "."
    if ctx.workspace is not None and ctx.workspace.mode in ("clone", "copy"):
        if raw in (".", "./"):
            return ctx.workspace.path
        if Path(raw).is_absolute() and str(Path(raw)).startswith(str(ctx.workspace.path)):
            return Path(raw)
        if Path(raw).is_absolute():
            # allow reading the original checkout as reference material when permitted
            allowed = [str(ctx.project_path)] if ctx.project_path else []
            if any(str(Path(raw)).startswith(prefix) for prefix in allowed if prefix):
                return Path(raw)
            return resolve_within(ctx.workspace.path, path)
        return resolve_within(ctx.workspace.path, path)
    base = Path(ctx.project_path or Path.cwd())
    if raw in (".", "./"):
        return base
    return resolve_within(base, path)


def tool_analyze_repository(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    target = _resolve(ctx, str(args.get("path", ".")))
    if not target.is_dir():
        return ToolResult(False, error=f"{target} is not a directory")
    analysis = analyze_repository(target, max_files=int(args.get("max_files", ctx.config.agent.analysis_depth)))
    project_id = ctx.knowledge.register_project(target, analysis=analysis)
    indexed: dict[str, Any] = {}
    if ctx.config.memory.index_projects:
        indexed = ctx.knowledge.index_project(project_id, analysis)
    ctx.project_id = project_id
    data = analysis.as_dict(include_files=False)
    data["index"] = indexed
    return ToolResult(True, output=analysis.outline_text(), data=data)


def tool_list_files(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    target = _resolve(ctx, str(args.get("path", ".")))
    pattern = str(args.get("pattern") or "")
    limit = int(args.get("limit", 200))
    if not target.exists():
        return ToolResult(False, error=f"path does not exist: {target}")
    entries: list[dict[str, Any]] = []
    if target.is_file():
        entries.append({"path": str(target), "type": "file", "size": target.stat().st_size})
    else:
        iterator = target.rglob(pattern) if pattern else target.iterdir()
        for entry in sorted(iterator, key=lambda p: (p.is_file(), str(p))):
            rel = entry.relative_to(target)
            if any(part in {".git", "node_modules", "__pycache__", ".venv", "dist", "build"} for part in rel.parts):
                continue
            try:
                size = entry.stat().st_size if entry.is_file() else 0
            except OSError:
                size = 0
            entries.append({"path": str(rel), "type": "dir" if entry.is_dir() else "file", "size": size})
            if len(entries) >= limit:
                break
    listing = "\n".join(f"{'d' if e['type'] == 'dir' else '-'} {e['path']}{'/' if e['type'] == 'dir' else ''}" + (f"  ({human_bytes(e['size'])})" if e["type"] == "file" else "") for e in entries)
    return ToolResult(True, output=listing or "(empty directory)", data={"path": str(target), "entries": entries[:400], "count": len(entries)})


def tool_read_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    target = _resolve(ctx, str(args.get("path", "")))
    start = max(1, int(args.get("start", 1)))
    limit = min(MAX_READ_LINES, max(1, int(args.get("limit", 200))))
    if not target.exists():
        return ToolResult(False, error=f"file does not exist: {target}")
    if target.is_dir():
        return ToolResult(False, error=f"{target} is a directory")
    try:
        text = read_text(target)
    except OSError as exc:
        return ToolResult(False, error=f"cannot read {target}: {exc}")
    lines = text.splitlines()
    window = lines[start - 1 : start - 1 + limit]
    numbered = "\n".join(f"{index + start:>5} | {line}" for index, line in enumerate(window))
    total = len(lines)
    note = "" if start == 1 and start - 1 + limit >= total else f"\n… showing lines {start}–{min(total, start - 1 + limit)} of {total}"
    return ToolResult(
        True,
        output=numbered + note,
        data={"path": str(target), "lines": total, "start": start, "shown": len(window), "language": target.suffix},
    )


def tool_search_code(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult(False, error="query is required")
    target = _resolve(ctx, str(args.get("path", ".")))
    limit = min(200, int(args.get("limit", 40)))
    use_regex = bool(args.get("regex"))
    results: list[dict[str, Any]] = []
    if shutil.which("rg") and not use_regex:
        result = run_command(
            ["rg", "--line-number", "--no-heading", "--color", "never", "--max-count", "3", "--", query, str(target)],
            timeout=45,
        )
        if result.ok:
            for line in result.stdout.splitlines()[:limit]:
                parts = line.split(":", 2)
                if len(parts) == 3:
                    results.append({"path": _rel(parts[0], ctx), "line": int(parts[1]) if parts[1].isdigit() else 0, "text": parts[2][:400]})
    if not results:
        pattern = re.compile(query if use_regex else re.escape(query), re.IGNORECASE)
        scanned = 0
        for path in sorted(target.rglob("*") if target.is_dir() else [target]):
            if not path.is_file():
                continue
            rel = str(path.relative_to(target if target.is_dir() else target.parent))
            if any(part in {".git", "node_modules", "__pycache__", ".venv", "dist", "build"} for part in Path(rel).parts):
                continue
            if path.stat().st_size > 1_500_000:
                continue
            scanned += 1
            try:
                text = read_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    results.append({"path": _rel(str(path), ctx), "line": number, "text": line.strip()[:400]})
                    if len(results) >= limit:
                        break
            if len(results) >= limit or scanned > 4000:
                break
    output = "\n".join(f"{item['path']}:{item['line']}: {item['text']}" for item in results) or f"no matches for {query!r}"
    return ToolResult(True, output=output, data={"query": query, "matches": results, "count": len(results)})


def _rel(path: str, ctx: ToolContext) -> str:
    try:
        return str(Path(path).resolve().relative_to(ctx.cwd.resolve()))
    except (ValueError, OSError):
        return path


def tool_knowledge_search(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    query = str(args.get("query", ""))
    results = ctx.knowledge.search(query, project_id=ctx.project_id, limit=int(args.get("limit", 25)))
    output = "\n".join(f"{item['kind']:>8}  {item['path']}" + (f"  ({item['symbol']} @ {item['line']})" if item["symbol"] else "") for item in results)
    return ToolResult(True, output=output or "no indexed symbols matched", data={"results": results})


def tool_memory_search(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    query = str(args.get("query", ""))
    entries = ctx.memory.search(query, project_id=ctx.project_id, limit=int(args.get("limit", 10)), min_quality=0.0)
    output = "\n".join(entry.to_prompt(300) for entry in entries) or "no memories matched"
    return ToolResult(True, output=output, data={"memories": [entry.as_dict() for entry in entries]})


def tool_project_outline(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if ctx.project_id is None:
        return ToolResult(False, error="no project registered yet — run analyze_repository first")
    items = ctx.knowledge.outline(ctx.project_id, limit=int(args.get("limit", 250)))
    output = "\n".join(f"{item['path']}:{item['line']}: {item['kind']} {item['symbol']}" for item in items if item["symbol"])
    return ToolResult(True, output=output or "no symbols indexed", data={"symbols": items})


def tool_git_status(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if ctx.github is None:
        return ToolResult(False, error="GitHub integration is not configured")
    status = ctx.github.status(ctx.cwd)
    if not status.get("ok"):
        return ToolResult(False, error=status.get("error", "git status failed"))
    lines = [f"branch {status['branch']}" + (f" → {status['tracking']}" if status.get("tracking") else "")]
    lines += [f"  {line}" for line in status["changes"][:80]] or ["  (clean working tree)"]
    return ToolResult(True, output="\n".join(lines), data=status)


def tool_git_diff(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if ctx.journal is not None and ctx.journal.diffs:
        combined = ctx.journal.combined_diff()
        return ToolResult(True, output=combined or "(no file changes recorded in this task)", data=ctx.journal.summary())
    if ctx.github is None:
        return ToolResult(False, error="GitHub integration is not configured")
    diff = ctx.github.diff(ctx.cwd, staged=bool(args.get("staged")), path=str(args.get("path", "")))
    return ToolResult(True, output=diff or "(no differences)", data={"chars": len(diff)})


def tool_environment_info(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from codemind.sandbox.policy import available_commands

    commands = available_commands()
    lines = [f"{name}: {path or 'not installed'}" for name, path in commands.items()]
    hw = getattr(ctx.governor, "plan", None)
    data = {"commands": {k: v for k, v in commands.items()}, "cwd": str(ctx.cwd), "governor": hw.as_dict() if hw else None}
    return ToolResult(True, output="\n".join(lines), data=data)


def tool_environment_report(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Python/library versions that matter for an agent working on this machine."""
    code = (
        "import json, sys, platform;"
        "info={'python': sys.version.split()[0], 'executable': sys.executable, 'platform': platform.platform()};"
        "mods={}\n"
        "for name in ('torch','transformers','peft','datasets','numpy','psutil','fastapi','flask','django','pytest'):\n"
        "    try:\n"
        "        module=__import__(name); mods[name]=getattr(module,'__version__','installed')\n"
        "    except Exception as exc: mods[name]=None\n"
        "info['packages']=mods\n"
        "print(json.dumps(info))"
    )
    result = run_command(["python3", "-c", code], timeout=30, cwd=ctx.cwd)
    if not result.ok:
        return ToolResult(False, error=result.summary(200))
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return ToolResult(False, error="could not parse the environment report")
    lines = [f"python {data['python']} ({data['executable']})", data["platform"]]
    lines += [f"{name} {version or 'not installed'}" for name, version in data.get("packages", {}).items()]
    return ToolResult(True, output="\n".join(lines), data=data)


# --------------------------------------------------------------------------- write tools


def tool_write_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    raw_path = str(args.get("path", "")).strip()
    if not raw_path:
        return ToolResult(False, error="path is required")
    content = args.get("content")
    if content is None:
        return ToolResult(False, error="content is required — the agent must supply the exact file contents")
    content = str(content)
    target = _resolve(ctx, raw_path, must_exist=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    if ctx.journal is not None:
        ctx.journal.snapshot(target)
        ctx.journal.backup(target)
    try:
        atomic_write_text(target, content, mode=0o644)
    except OSError as exc:
        return ToolResult(False, error=f"cannot write {target}: {exc}")
    data = {"path": str(target), "created": not existed, "bytes": len(content.encode('utf-8'))}
    if ctx.journal is not None:
        entry = ctx.journal.record(target, content, "created" if not existed else "modified")
        data["diff"] = entry
        data["added"], data["removed"] = entry["added"], entry["removed"]
    return ToolResult(
        True,
        output=f"{'created' if not existed else 'updated'} {_rel(str(target), ctx)} ({len(content.splitlines())} lines)",
        data=data,
        risk=RiskLevel.WRITE.value,
    )


def tool_edit_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Replace ``old_text`` with ``new_text`` — the safest way to change code."""
    raw_path = str(args.get("path", "")).strip()
    old_text = args.get("old_text")
    new_text = args.get("new_text")
    if not raw_path:
        return ToolResult(False, error="path is required")
    if old_text is None or new_text is None:
        return ToolResult(False, error="old_text and new_text are required (exact strings to replace)")
    target = _resolve(ctx, raw_path)
    if not target.exists():
        return ToolResult(False, error=f"file does not exist: {target}")
    current = read_text(target)
    old_str, new_str = str(old_text), str(new_text)
    if old_str not in current:
        # tolerate indentation differences, which models get wrong constantly
        flexible = re.compile(r"(?m)^[ \t]*" + re.escape(old_str.strip()).replace(r"\ ", r"[ \t]+") + r"[ \t]*$")
        match = flexible.search(current)
        if not match:
            return ToolResult(
                False,
                error="old_text was not found in the file. Read the file again and use an exact excerpt.",
                data={"path": str(target), "searched": old_str[:200]},
            )
        start, end = match.span()
        updated = current[:start] + _reindent(new_str, match.group(0)) + current[end:]
    else:
        updated = current.replace(old_str, new_str, 1)
    if ctx.journal is not None:
        ctx.journal.snapshot(target)
        ctx.journal.backup(target)
    try:
        atomic_write_text(target, updated, mode=0o644)
    except OSError as exc:
        return ToolResult(False, error=f"cannot write {target}: {exc}")
    data = {"path": str(target)}
    if ctx.journal is not None:
        entry = ctx.journal.record(target, updated, "modified")
        data["diff"] = entry
        data["added"], data["removed"] = entry["added"], entry["removed"]
    return ToolResult(
        True,
        output=f"edited {_rel(str(target), ctx)} (+{data.get('added', 0)} / -{data.get('removed', 0)})",
        data=data,
        risk=RiskLevel.WRITE.value,
    )


def _reindent(new_text: str, matched_line: str) -> str:
    indent = matched_line[: len(matched_line) - len(matched_line.lstrip())]
    lines = new_text.splitlines()
    if not lines:
        return indent
    head, *rest = lines
    return "\n".join([indent + head] + [(indent + line) if line.strip() else line for line in rest])


def tool_run_command(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    command = str(args.get("command", "")).strip()
    if not command:
        return ToolResult(False, error="command is required")
    if ctx.sandbox is None:
        return ToolResult(False, error="sandbox is unavailable")
    timeout = float(args.get("timeout") or ctx.config.agent.command_timeout)
    purpose = str(args.get("purpose", "run"))
    workspace = ctx.workspace or ctx.sandbox.create_workspace(ctx.cwd, task_id=ctx.task_id, mode="in_place")

    def on_line(stream: str, line: str) -> None:
        ctx.emit(stream, line)

    try:
        result = ctx.sandbox.execute(
            command,
            workspace=workspace,
            task_id=ctx.task_id,
            timeout=timeout,
            purpose=purpose,
            on_line=on_line,
            governor_plan=ctx.governor_plan(),
        )
    except CommandDenied as exc:
        return ToolResult(False, error=str(exc), blocked=True, risk=RiskLevel.DESTRUCTIVE.value)
    output = truncate_middle(result.combined.strip(), MAX_TOOL_OUTPUT)
    data = {
        "command": command,
        "exit_code": result.exit_code,
        "duration": round(result.duration, 3),
        "timed_out": result.timed_out,
        "stdout": truncate_middle(result.stdout, 8000),
        "stderr": truncate_middle(result.stderr, 8000),
        "cwd": result.cwd,
    }
    if result.ok:
        return ToolResult(True, output=output or f"(no output, exit 0 in {result.duration:.2f}s)", data=data, risk=RiskLevel.READ.value)
    return ToolResult(False, output=output, error=result.summary(600), data=data, risk=RiskLevel.READ.value)


def tool_run_tests(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    command = str(args.get("command") or "").strip()
    if not command:
        commands: list[str] = []
        if ctx.project_id is not None:
            commands = ctx.knowledge.test_suites(ctx.project_id)
        if not commands:
            project = ctx.project_path or ctx.cwd
            analysis = analyze_repository(project, max_files=600, collect_files=False)
            commands = analysis.test_commands
        if not commands:
            return ToolResult(False, error="no test command detected in this project (set sandbox/agent settings or pass a command)")
        command = commands[0]
    payload = dict(args)
    payload["command"] = command
    payload["purpose"] = "tests"
    payload.setdefault("timeout", max(300.0, ctx.config.agent.command_timeout))
    result = tool_run_command(payload, ctx)
    parsed = parse_test_output(result.data.get("stdout", "") + "\n" + result.data.get("stderr", ""))
    result.data["tests"] = parsed
    result.output = (result.output or "") + "\n\n" + parsed["summary"]
    return result


def parse_test_output(text: str) -> dict[str, Any]:
    """Recognise pytest, unittest, jest/vitest, go test and cargo test summaries."""
    output = strip_ansi(text or "")
    parsed: dict[str, Any] = {"framework": "", "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "summary": ""}
    patterns = [
        ("pytest", r"(?P<passed>\d+) passed(?:, (?P<skipped>\d+) skipped)?(?:, (?P<failed>\d+) failed)?(?:, (?P<errors>\d+) errors?)?"),
        ("unittest", r"Ran (?P<total>\d+) tests? in [\d.]+s(?P<verdict>[\s\S]{0,120}?)(?:OK|FAILED \((?:(?:failures=(?P<failed>\d+))?(?:, )?(?:errors=(?P<errors>\d+))?)\))"),
        ("jest", r"Tests:\s+(?:(?P<failed>\d+) failed,\s+)?(?:(?P<passed>\d+) passed,\s+)?(?:(?P<skipped>\d+) skipped,\s+)?(?P<total>\d+) total"),
        ("vitest", r"Tests\s+(?:(?P<failed>\d+) failed\s*\|\s*)?(?P<passed>\d+) passed"),
        ("go", r"(?P<failed>FAIL\s+[\w./-]+)|(?P<passed>ok\s+[\w./-]+)"),
        ("cargo", r"test result: (?P<result>ok|FAILED)\. (?P<passed>\d+) passed; (?P<failed>\d+) failed"),
    ]
    for framework, pattern in patterns:
        match = re.search(pattern, output)
        if not match:
            continue
        groups = match.groupdict()
        parsed["framework"] = framework
        for key in ("passed", "failed", "skipped", "errors"):
            value = groups.get(key)
            if value and value.isdigit():
                parsed[key] = int(value)
        if framework == "unittest":
            total_ran = int(groups.get("total") or 0)
            parsed["failed"] = int(groups.get("failed") or 0)
            parsed["errors"] = int(groups.get("errors") or 0)
            parsed["passed"] = max(0, total_ran - parsed["failed"] - parsed["errors"])
        if framework == "go":
            parsed["failed"] = len(re.findall(r"^FAIL\s", output, flags=re.MULTILINE))
            parsed["passed"] = len(re.findall(r"^ok\s", output, flags=re.MULTILINE))
        break
    if not parsed["framework"]:
        if "AssertionError" in output or "FAILED" in output:
            parsed["framework"] = "unknown"
            parsed["failed"] = max(1, len(re.findall(r"^FAILED ", output, flags=re.MULTILINE)))
        elif output.strip():
            parsed["framework"] = "unknown"
    total = parsed["passed"] + parsed["failed"] + parsed["errors"]
    collected_none = parsed["framework"] == "unittest" and total == 0
    if collected_none:
        parsed["summary"] = "No tests were collected: the test command ran but discovered nothing. Check the test path/pattern."
        parsed["ok"] = False
        parsed["total"] = 0
        return parsed
    if parsed["failed"] or parsed["errors"]:
        failing = [
            match
            for match in re.findall(r"^(?:FAILED|FAIL)\s+([^\s]+)", output, flags=re.MULTILINE)
            if "::" in match or "/" in match or match.startswith("test")
        ][:10]
        detail = f", failing: {', '.join(failing)}" if failing else ""
        parsed["summary"] = f"Tests: {parsed['passed']} passed, {parsed['failed']} failed, {parsed['errors']} errors{detail}"
    elif parsed["framework"] and total:
        parsed["summary"] = f"Tests: {parsed['passed']} passed" + (f", {parsed['skipped']} skipped" if parsed["skipped"] else "")
    else:
        parsed["summary"] = "Test result could not be parsed; read the raw output above."
    parsed["total"] = total
    parsed["ok"] = parsed["failed"] == 0 and parsed["errors"] == 0 and (total > 0 or parsed["framework"] == "unknown")
    return parsed


def tool_python_exec(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    code = str(args.get("code", ""))
    if not code.strip():
        return ToolResult(False, error="code is required")
    script = ctx.cwd / f".codemind-exec-{uuid.uuid4().hex[:8]}.py"
    try:
        script.write_text(code, encoding="utf-8")
    except OSError as exc:
        return ToolResult(False, error=f"cannot stage the script: {exc}")
    try:
        result = tool_run_command({"command": f"python3 {script.name}", "purpose": "python", "timeout": args.get("timeout", 120)}, ctx)
        return result
    finally:
        script.unlink(missing_ok=True)


def tool_install_package(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    package = str(args.get("package", "")).strip()
    if not package:
        return ToolResult(False, error="package is required")
    manager = str(args.get("manager") or "auto")
    if manager == "auto":
        manager = "pip" if (ctx.cwd / "pyproject.toml").exists() or (ctx.cwd / "requirements.txt").exists() or not (ctx.cwd / "package.json").exists() else "npm"
    scope = str(args.get("scope") or "workspace")
    if manager == "apt":
        return ToolResult(False, error="the agent does not install system packages; install it yourself and re-run the task")
    if manager == "pip":
        if scope == "workspace" and ctx.sandbox is not None and ctx.config.sandbox.use_workspace_venv:
            # Ubuntu/Debian refuse system-wide pip (PEP 668): every dependency goes in the workspace venv.
            try:
                interpreter = ctx.sandbox.ensure_venv(ctx.workspace, task_id=ctx.task_id)
            except Exception as exc:
                return ToolResult(False, error=f"could not prepare an isolated virtualenv: {exc}")
            command = f"{interpreter} -m pip install {package}"
        else:
            command = f"python3 -m pip install {package}"
    else:
        commands = {
            "npm": f"npm install {package}",
            "yarn": f"yarn add {package}",
            "pnpm": f"pnpm add {package}",
            "cargo": f"cargo add {package}",
            "go": f"go get {package}",
        }
        if manager not in commands:
            return ToolResult(False, error=f"unsupported package manager: {manager}")
        command = commands[manager]
    payload = {"command": command, "purpose": "install", "timeout": 900}
    result = tool_run_command(payload, ctx)
    if result.ok and manager == "pip" and scope == "workspace":
        result.output = (
            f"installed {package} into the workspace virtualenv (nothing outside the sandbox was touched)\n"
            f"note: run the project's tests through the sandbox so the venv stays on PATH — CodeMind does this automatically.\n"
            f"{result.output or ''}"
        )
    result.data["scope"] = "workspace-venv" if manager == "pip" and scope == "workspace" else "project"
    return result


def tool_memory_save(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    kind = str(args.get("kind", "note"))
    title = str(args.get("title", "")).strip()
    body = str(args.get("body", "")).strip()
    if not title and not body:
        return ToolResult(False, error="title or body is required")
    tags = args.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    memory_id = ctx.memory.remember(
        kind,
        title,
        body,
        tags=list(tags),
        quality=float(args.get("quality", 0.7)),
        project_id=ctx.project_id,
        source="agent",
        evidence={"task_id": ctx.task_id},
    )
    if memory_id is None:
        return ToolResult(False, error="memory was rejected (empty content)")
    return ToolResult(True, output=f"stored memory #{memory_id} ({kind})", data={"id": memory_id})


def tool_fetch_docs(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return ToolResult(False, error="url must start with http:// or https://")
    import urllib.error
    import urllib.request

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "CodeMind/1.0"})
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read(600_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return ToolResult(False, error=f"fetch failed: {exc}")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return ToolResult(True, output=truncate_middle(text, 20000), data={"url": url, "chars": len(text)})


# --------------------------------------------------------------------------- git tools


def tool_git_commit(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if ctx.github is None:
        return ToolResult(False, error="GitHub integration is not configured")
    message = str(args.get("message", "")).strip() or "CodeMind: automated change"
    paths = args.get("paths") or []
    if isinstance(paths, str):
        paths = [p.strip() for p in paths.split(",") if p.strip()]
    try:
        record = ctx.github.commit(ctx.cwd, message, paths=list(paths))
    except Exception as exc:
        return ToolResult(False, error=str(exc))
    return ToolResult(True, output=json.dumps(record), data=record)


def tool_git_branch(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if ctx.github is None:
        return ToolResult(False, error="GitHub integration is not configured")
    name = str(args.get("name", "")).strip()
    if not name:
        return ToolResult(False, error="name is required")
    try:
        record = ctx.github.create_branch(ctx.cwd, name, base=str(args.get("base", "")))
    except Exception as exc:
        return ToolResult(False, error=str(exc))
    return ToolResult(True, output=f"created branch {record['branch']}", data=record)


def tool_git_push(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if ctx.github is None:
        return ToolResult(False, error="GitHub integration is not configured")
    try:
        record = ctx.github.push(ctx.cwd, branch=str(args.get("branch", "")), remote=str(args.get("remote", "origin")))
    except Exception as exc:
        return ToolResult(False, error=str(exc))
    return ToolResult(True, output=f"pushed {record['branch']} to {record['remote']}", data=record)


def tool_create_pull_request(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if ctx.github is None:
        return ToolResult(False, error="GitHub integration is not configured")
    repo = str(args.get("repo", "")).strip()
    head = str(args.get("head", "")).strip()
    if not repo or not head:
        return ToolResult(False, error="repo (owner/name) and head branch are required")
    try:
        record = ctx.github.create_pull_request(
            repo,
            title=str(args.get("title", "CodeMind changes")),
            body=str(args.get("body", "")),
            head=head,
            base=str(args.get("base", "")),
            repo_dir=ctx.cwd,
        )
    except Exception as exc:
        return ToolResult(False, error=str(exc))
    return ToolResult(True, output=f"opened pull request {record.get('url', '')}", data=record)


def tool_repo_clone(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if ctx.github is None:
        return ToolResult(False, error="GitHub integration is not configured")
    repo = str(args.get("repo", "")).strip()
    if not repo:
        return ToolResult(False, error="repo (owner/name or URL) is required")
    target = Path(args.get("target") or (ctx.sandbox.projects_root / Path(repo).name))
    if target.exists() and any(target.iterdir()):
        return ToolResult(False, error=f"target already exists and is not empty: {target}")
    try:
        ctx.github.clone(repo, target, branch=str(args.get("branch", "")))
    except Exception as exc:
        return ToolResult(False, error=str(exc))
    analysis = analyze_repository(target, max_files=1500)
    project_id = ctx.knowledge.register_project(target, analysis=analysis)
    return ToolResult(True, output=f"cloned {repo} to {target}\n\n{analysis.outline_text()}", data={"path": str(target), "project_id": project_id})


def tool_noop(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return ToolResult(True, output=str(args.get("reason") or "nothing to do"), data={"noop": True})


def tool_download_model(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    name = str(args.get("name", "")).strip()
    if not name:
        return ToolResult(False, error="name is required")
    manager = args.get("_model_manager")
    if manager is None:
        return ToolResult(False, error="model manager unavailable in this context")
    record = manager.download(name)
    return ToolResult(True, output=f"download started for {name}", data=record)


# --------------------------------------------------------------------------- registry


class ToolRegistry:
    """Validates arguments, enforces policy, executes and records."""

    def __init__(self, tools: Iterable[ToolDefinition] = ()) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        for tool in tools:
            self.register(tool)
        self._calls = 0

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[ToolDefinition]:
        return [self._tools[name] for name in self.names()]

    def specs(self) -> list[Any]:
        return [tool.spec() for tool in self.all()]

    def prompts(self) -> list[dict[str, Any]]:
        return [tool.as_dict() for tool in self.all()]

    # -- execution --------------------------------------------------------
    def validate(self, definition: ToolDefinition, arguments: dict[str, Any]) -> str | None:
        required = definition.parameters.get("required") or []
        missing = [key for key in required if key not in arguments or arguments[key] in (None, "")]
        if missing:
            return f"missing required argument(s): {', '.join(missing)}"
        properties = definition.parameters.get("properties") or {}
        for key, value in arguments.items():
            if key.startswith("_") or key not in properties:
                continue
            expected = properties[key].get("type")
            if expected == "integer" and isinstance(value, str) and value.isdigit():
                arguments[key] = int(value)
            elif expected == "number" and isinstance(value, str):
                try:
                    arguments[key] = float(value)
                except ValueError:
                    return f"argument {key!r} must be a number"
        return None

    def execute(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        ctx: ToolContext,
        *,
        approval_timeout: float | None = None,
    ) -> ToolResult:
        definition = self._tools.get(name)
        if definition is None:
            return ToolResult(False, error=f"unknown tool {name!r}. Available: {', '.join(self.names())}")
        args = dict(arguments or {})
        error = self.validate(definition, args)
        if error:
            return ToolResult(False, error=error)
        risk = ctx.policy.classify(name, args, definition.risk)
        inside = bool(ctx.workspace is None or ctx.workspace.mode == "in_place" or ctx.workspace.is_inside(args.get("path", ".")))
        decision = ctx.policy.decide(name, args, base_risk=risk, inside_workspace=inside)
        if decision.blocked:
            result = ToolResult(False, error=decision.reason, blocked=True, risk=decision.risk.value)
            self._record(ctx, name, args, result, decision)
            return result
        if decision.requires_approval:
            request = ctx.approvals.request(ctx.task_id, name, args, decision.risk, decision.reason or decision.requirement)
            ctx.emit("system", f"⏸ approval required: {name} ({decision.risk.label}) — {decision.reason}")
            approved = self._wait_for_approval(ctx, request, approval_timeout)
            if not approved:
                reason = request.reason or ("timed out" if request.status == "pending" else "denied by the user")
                result = ToolResult(False, error=f"approval denied: {reason}", denied=True, risk=decision.risk.value)
                self._record(ctx, name, args, result, decision)
                return result
        started = time.monotonic()
        try:
            result = definition.handler(args, ctx)
        except CommandDenied as exc:
            result = ToolResult(False, error=str(exc), blocked=True, risk=decision.risk.value)
        except FileNotFoundError as exc:
            result = ToolResult(False, error=f"not found: {exc}")
        except PermissionError as exc:
            result = ToolResult(False, error=f"permission denied: {exc}")
        except Exception as exc:  # pragma: no cover - defensive: a tool must never kill the task
            log.exception("tool execution failed", cm={"tool": name})
            result = ToolResult(False, error=f"{type(exc).__name__}: {exc}")
        result.duration = time.monotonic() - started
        if result.risk == RiskLevel.READ.value:
            result.risk = decision.risk.value
        result.output = truncate_middle(redact_text(result.output), MAX_TOOL_OUTPUT)
        result.error = redact_text(result.error)
        self._record(ctx, name, args, result, decision)
        self._calls += 1
        return result

    @staticmethod
    def _wait_for_approval(ctx: ToolContext, request: Any, timeout: float | None) -> bool:
        """Wait for the user, but abandon the wait when the task is cancelled."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if request._event.wait(0.5):
                return bool(request.decision)
            if ctx.cancel_check is not None and ctx.cancel_check():
                return False
            if deadline is not None and time.monotonic() > deadline:
                return False

    def _record(self, ctx: ToolContext, name: str, args: dict[str, Any], result: ToolResult, decision: Any) -> None:
        try:
            ctx.db.insert(
                "tool_calls",
                {
                    "task_id": ctx.task_id or None,
                    "ts": time.time(),
                    "tool": name,
                    "args": json.dumps({k: v for k, v in args.items() if k != "content"}, default=str)[:4000],
                    "ok": 1 if result.ok else 0,
                    "duration": round(result.duration, 4),
                    "summary": result.summary(500),
                    "risk": getattr(decision, "risk", RiskLevel.READ).value if hasattr(decision, "risk") else str(decision),
                    "approved": 1 if not getattr(decision, "requires_approval", False) else 1,
                },
            )
        except Exception as exc:  # pragma: no cover
            log.debug("could not record tool call", cm={"error": str(exc)})
        BUS.publish(
            Topics.TOOL_CALL,
            task_id=ctx.task_id,
            tool=name,
            arguments={k: v for k, v in args.items() if k != "content"},
            ok=result.ok,
            duration=round(result.duration, 3),
            risk=result.risk,
            summary=result.summary(240),
            approval=decision.as_dict() if hasattr(decision, "as_dict") else None,
        )

    def stats(self) -> dict[str, Any]:
        return {"tools": len(self._tools), "calls": self._calls}


def build_default_tools() -> list[ToolDefinition]:
    """The complete tool surface, described once for both the planner and the UI."""
    return [
        ToolDefinition(
            "analyze_repository",
            "Inspect a repository: languages, entry points, dependencies, detected test commands. Also indexes symbols for later searches.",
            _schema({"path": {"type": "string", "description": "Directory to inspect (default: workspace root)"},
                     "max_files": {"type": "integer", "description": "Upper bound on files read"}}, ["path"]),
            tool_analyze_repository, RiskLevel.READ, "analysis",
        ),
        ToolDefinition(
            "list_files",
            "List files and directories, optionally filtered by a glob pattern.",
            _schema({"path": {"type": "string"}, "pattern": {"type": "string"}, "limit": {"type": "integer"}}, ["path"]),
            tool_list_files, RiskLevel.READ, "analysis",
        ),
        ToolDefinition(
            "read_file",
            "Read a text file with line numbers. Use start/limit for large files.",
            _schema({"path": {"type": "string"}, "start": {"type": "integer"}, "limit": {"type": "integer"}}, ["path"]),
            tool_read_file, RiskLevel.READ, "analysis",
        ),
        ToolDefinition(
            "search_code",
            "Search the project for a string or regular expression; returns path:line:text for each match.",
            _schema({"query": {"type": "string"}, "path": {"type": "string"}, "regex": {"type": "boolean"}, "limit": {"type": "integer"}}, ["query"]),
            tool_search_code, RiskLevel.READ, "analysis",
        ),
        ToolDefinition(
            "knowledge_search",
            "Search the persisted symbol index of this project (classes, functions, test suites).",
            _schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
            tool_knowledge_search, RiskLevel.READ, "analysis",
        ),
        ToolDefinition(
            "project_outline",
            "List indexed symbols of the project to plan edits quickly.",
            _schema({"limit": {"type": "integer"}}),
            tool_project_outline, RiskLevel.READ, "analysis",
        ),
        ToolDefinition(
            "memory_search",
            "Search persistent memory: architecture facts, preferences, verified solutions and past mistakes.",
            _schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
            tool_memory_search, RiskLevel.READ, "memory",
        ),
        ToolDefinition(
            "memory_save",
            "Store a durable memory entry (kind: architecture, preference, solution, mistake, knowledge, project_fact, note).",
            _schema({"kind": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"},
                     "tags": {"type": "array", "items": {"type": "string"}}, "quality": {"type": "number"}}, ["kind", "title", "body"]),
            tool_memory_save, RiskLevel.WRITE, "memory",
        ),
        ToolDefinition(
            "write_file",
            "Create or replace a file with exact content. Always read the file first when it exists.",
            _schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
            tool_write_file, RiskLevel.WRITE, "edit", writes_files=True,
        ),
        ToolDefinition(
            "edit_file",
            "Replace an exact excerpt (old_text) with new_text inside a file. Safest way to change existing code.",
            _schema({"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, ["path", "old_text", "new_text"]),
            tool_edit_file, RiskLevel.WRITE, "edit", writes_files=True,
        ),
        ToolDefinition(
            "run_command",
            "Run a shell command inside the sandboxed workspace and capture its output.",
            _schema({"command": {"type": "string"}, "purpose": {"type": "string"}, "timeout": {"type": "number"}}, ["command"]),
            tool_run_command, RiskLevel.READ, "execute",
        ),
        ToolDefinition(
            "run_tests",
            "Run the project's test suite (auto-detected) or a specific test command, and parse the pass/fail counts.",
            _schema({"command": {"type": "string"}, "target": {"type": "string"}}),
            tool_run_tests, RiskLevel.READ, "execute",
        ),
        ToolDefinition(
            "python_exec",
            "Run a short Python snippet in the workspace and return its output.",
            _schema({"code": {"type": "string"}, "timeout": {"type": "number"}}, ["code"]),
            tool_python_exec, RiskLevel.WRITE, "execute",
        ),
        ToolDefinition(
            "install_package",
            "Install a dependency with the project's package manager (network access required).",
            _schema({"package": {"type": "string"}, "manager": {"type": "string"}}, ["package"]),
            tool_install_package, RiskLevel.NETWORK, "execute", requires_network=True,
        ),
        ToolDefinition(
            "environment_info",
            "List the developer tools available in this environment with their paths.",
            _schema({}),
            tool_environment_info, RiskLevel.READ, "analysis",
        ),
        ToolDefinition(
            "environment_report",
            "Report Python and library versions available for the workspace.",
            _schema({}),
            tool_environment_report, RiskLevel.READ, "analysis",
        ),
        ToolDefinition(
            "git_status",
            "Show branch, tracking information and changed files.",
            _schema({}),
            tool_git_status, RiskLevel.READ, "git",
        ),
        ToolDefinition(
            "git_diff",
            "Show the diff of the current task (recorded file changes) or the working tree.",
            _schema({"staged": {"type": "boolean"}, "path": {"type": "string"}}),
            tool_git_diff, RiskLevel.READ, "git",
        ),
        ToolDefinition(
            "git_commit",
            "Stage and commit the current changes locally.",
            _schema({"message": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}}, ["message"]),
            tool_git_commit, RiskLevel.WRITE, "git",
        ),
        ToolDefinition(
            "git_branch",
            "Create and check out a branch.",
            _schema({"name": {"type": "string"}, "base": {"type": "string"}}, ["name"]),
            tool_git_branch, RiskLevel.WRITE, "git",
        ),
        ToolDefinition(
            "git_push",
            "Push the current branch to the remote (irreversible external change).",
            _schema({"branch": {"type": "string"}, "remote": {"type": "string"}}),
            tool_git_push, RiskLevel.EXTERNAL, "git", requires_network=True,
        ),
        ToolDefinition(
            "create_pull_request",
            "Open a pull request for the current branch (irreversible external change).",
            _schema({"repo": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}, "head": {"type": "string"}, "base": {"type": "string"}},
                    ["repo", "title", "head"]),
            tool_create_pull_request, RiskLevel.EXTERNAL, "git", requires_network=True,
        ),
        ToolDefinition(
            "repo_clone",
            "Clone a GitHub repository into the projects directory and analyse it.",
            _schema({"repo": {"type": "string"}, "target": {"type": "string"}, "branch": {"type": "string"}}, ["repo"]),
            tool_repo_clone, RiskLevel.NETWORK, "git", requires_network=True,
        ),
        ToolDefinition(
            "fetch_docs",
            "Fetch a documentation page and return its text (network access required).",
            _schema({"url": {"type": "string"}}, ["url"]),
            tool_fetch_docs, RiskLevel.NETWORK, "research", requires_network=True,
        ),
        ToolDefinition(
            "download_model",
            "Start downloading a local model from the Ollama registry.",
            _schema({"name": {"type": "string"}}, ["name"]),
            tool_download_model, RiskLevel.NETWORK, "models", requires_network=True,
        ),
        ToolDefinition(
            "noop",
            "Record that a step needed no action (analysis-only or already satisfied).",
            _schema({"reason": {"type": "string"}}, ["reason"]),
            tool_noop, RiskLevel.READ, "control",
        ),
    ]
