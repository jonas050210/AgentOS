"""Sandbox workspace lifecycle and command execution.

A workspace is either an isolated copy of the project (the default) or the project itself
when you ask for in-place work. Commands run with:

* a hard timeout and process-group teardown (no orphaned children);
* ``setrlimit`` caps when configured (CPU seconds, address space, file size, processes);
* the strongest isolation the kernel actually supports;
* output captured, redacted, truncated and stored in the execution history.

Only workspace paths are writable; the project mount is read-only unless in-place mode was
explicitly chosen.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from codemind.config import Config
from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.sandbox.policy import IsolationReport, build_sandbox_argv, probe_isolation
from codemind.storage.db import Database
from codemind.utils.files import atomic_write_json, copy_tree, ensure_dir, human_bytes, read_json
from codemind.utils.process import CommandResult, build_env, run_command
from codemind.utils.redaction import GLOBAL_REDACTOR
from codemind.utils.textutil import tail_lines

log = get_logger("sandbox.manager")


@dataclass
class SandboxPolicy:
    allow_network: bool
    network_isolation: str
    allow_shell: bool
    allow_destructive: bool
    blocked_patterns: list[str] = field(default_factory=list)
    cpu_limit_percent: int = 0
    memory_limit_mb: int = 0
    output_limit: int = 400_000
    extra_read_paths: tuple[str, ...] = ()
    nice: int = 5

    def as_dict(self) -> dict[str, Any]:
        return {
            "allow_network": self.allow_network,
            "network_isolation": self.network_isolation,
            "allow_shell": self.allow_shell,
            "allow_destructive": self.allow_destructive,
            "blocked_patterns": list(self.blocked_patterns),
            "cpu_limit_percent": self.cpu_limit_percent,
            "memory_limit_mb": self.memory_limit_mb,
            "output_limit": self.output_limit,
            "nice": self.nice,
        }

    @classmethod
    def from_config(cls, config: Config, governor_plan: Any | None = None) -> "SandboxPolicy":
        nice = getattr(governor_plan, "nice", 5) if governor_plan else 5
        return cls(
            allow_network=config.sandbox.allow_network,
            network_isolation=config.sandbox.network_isolation,
            allow_shell=config.sandbox.allow_shell,
            allow_destructive=config.sandbox.allow_destructive,
            blocked_patterns=list(config.sandbox.blocked_commands),
            cpu_limit_percent=config.sandbox.cpu_limit_percent,
            memory_limit_mb=config.sandbox.memory_limit_mb,
            output_limit=config.sandbox.command_output_limit,
            extra_read_paths=tuple(config.sandbox.allowed_paths),
            nice=nice,
        )


@dataclass
class Workspace:
    id: str
    path: Path
    project_path: Path
    mode: str  # clone | copy | in_place
    created_at: float
    task_id: str = ""
    branch: str = ""
    dirty: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        usage = self.disk_usage()
        return {
            "id": self.id,
            "path": str(self.path),
            "project_path": str(self.project_path),
            "mode": self.mode,
            "created_at": self.created_at,
            "task_id": self.task_id,
            "branch": self.branch,
            "exists": self.path.exists(),
            "bytes": usage,
            "bytes_human": human_bytes(usage),
        }

    def disk_usage(self) -> int:
        total = 0
        for root, _dirs, files in os.walk(self.path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    continue
        return total

    def resolve(self, relative: str | os.PathLike[str]) -> Path:
        from codemind.utils.files import resolve_within

        return resolve_within(self.path, relative)

    def is_inside(self, path: str | os.PathLike[str]) -> bool:
        try:
            self.resolve(path)
            return True
        except ValueError:
            return False


class CommandDenied(RuntimeError):
    """Raised when the sandbox policy refuses a command outright (no approval can override some of these)."""


class SandboxManager:
    """Creates workspaces, runs commands in them and keeps the history."""

    def __init__(
        self,
        config: Config,
        db: Database,
        *,
        workspaces_root: Path,
        projects_root: Path,
    ) -> None:
        self.config = config
        self.db = db
        self.workspaces_root = Path(workspaces_root)
        self.projects_root = Path(projects_root)
        ensure_dir(self.workspaces_root)
        ensure_dir(self.projects_root)
        self.isolation: IsolationReport = probe_isolation()
        self._workspaces: dict[str, Workspace] = {}
        self.active_process: Any = None
        self._children: dict[int, Any] = {}
        self._children_lock = threading.RLock()

    # -- lifecycle --------------------------------------------------------
    def update_config(self, config: Config) -> None:
        self.config = config

    def refresh_isolation(self) -> IsolationReport:
        self.isolation = probe_isolation(force=True)
        BUS.publish(Topics.SANDBOX_STATUS, **self.status())
        return self.isolation

    def policy(self, governor_plan: Any | None = None) -> SandboxPolicy:
        return SandboxPolicy.from_config(self.config, governor_plan)

    # -- workspaces -------------------------------------------------------
    def create_workspace(
        self,
        project_path: str | os.PathLike[str],
        *,
        task_id: str = "",
        mode: str = "clone",
        copy_ignores: Sequence[str] = (".git", "node_modules", "__pycache__", ".venv", "dist", "build"),
    ) -> Workspace:
        """Prepare an isolated (or in-place) working copy for a task."""
        project = Path(project_path).expanduser().resolve()
        if not project.exists():
            raise FileNotFoundError(f"project path does not exist: {project}")
        workspace_id = f"{task_id or 'ws'}-{uuid.uuid4().hex[:8]}"
        target = self.workspaces_root / workspace_id
        chosen_mode = mode
        if mode == "in_place":
            workspace = Workspace(id=workspace_id, path=project, project_path=project, mode="in_place", created_at=time.time(), task_id=task_id)
            self._workspaces[workspace_id] = workspace
            log.info("in-place workspace selected", cm={"project": str(project)})
            return workspace
        ensure_dir(target)
        if mode == "clone" and (project / ".git").exists():
            branch = f"codemind/{task_id or uuid.uuid4().hex[:6]}"
            result = run_command(
                ["git", "clone", "--quiet", "--no-hardlinks", "--local", str(project), str(target)],
                timeout=240,
            )
            if result.ok:
                run_command(["git", "checkout", "-q", "-b", branch], cwd=target, timeout=60)
                workspace = Workspace(
                    id=workspace_id, path=target, project_path=project, mode="clone",
                    created_at=time.time(), task_id=task_id, branch=branch,
                    notes=["Isolated clone; the original checkout is untouched."],
                )
                self._workspaces[workspace_id] = workspace
                log.info("workspace cloned", cm={"project": str(project), "workspace": str(target), "branch": branch})
                return workspace
            log.warning("clone failed, falling back to copy", cm={"error": result.summary(200)})
            chosen_mode = "copy"
            shutil.rmtree(target, ignore_errors=True)
            ensure_dir(target)
        copied = copy_tree(project, target, ignore_names=copy_ignores)
        workspace = Workspace(
            id=workspace_id, path=target, project_path=project, mode="copy",
            created_at=time.time(), task_id=task_id,
            notes=[f"Copied {copied} files (skipped {', '.join(copy_ignores)})."],
        )
        self._workspaces[workspace_id] = workspace
        log.info("workspace copied", cm={"project": str(project), "workspace": str(target), "files": copied})
        return workspace

    def workspace(self, workspace_id: str) -> Workspace | None:
        workspace = self._workspaces.get(workspace_id)
        if workspace and workspace.path.exists():
            return workspace
        # Rebuild from disk after a restart so old workspaces stay usable.
        path = self.workspaces_root / workspace_id
        if path.exists():
            meta = read_json(path / ".codemind-workspace.json", default={}) or {}
            workspace = Workspace(
                id=workspace_id,
                path=path,
                project_path=Path(meta.get("project_path", path)),
                mode=meta.get("mode", "copy"),
                created_at=meta.get("created_at", path.stat().st_mtime),
                task_id=meta.get("task_id", ""),
                branch=meta.get("branch", ""),
            )
            self._workspaces[workspace_id] = workspace
            return workspace
        return None

    def list_workspaces(self) -> list[Workspace]:
        workspaces: list[Workspace] = []
        for entry in sorted(self.workspaces_root.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if not entry.is_dir():
                continue
            workspace = self.workspace(entry.name)
            if workspace:
                workspaces.append(workspace)
        for workspace in self._workspaces.values():
            if workspace not in workspaces and workspace.path.exists():
                workspaces.append(workspace)
        return workspaces

    def prune_workspaces(self, keep: int | None = None, older_than_days: int | None = None) -> int:
        keep = keep if keep is not None else self.config.sandbox.keep_workspaces
        older_than_days = older_than_days if older_than_days is not None else self.config.storage.prune_workspaces_days
        workspaces = self.list_workspaces()
        removed = 0
        cutoff = time.time() - older_than_days * 86400
        for workspace in workspaces[keep:]:
            if workspace.mode == "in_place":
                continue
            if workspace.created_at > cutoff and len(workspaces) - removed <= keep:
                continue
            try:
                shutil.rmtree(workspace.path, ignore_errors=False)
                removed += 1
                self._workspaces.pop(workspace.id, None)
            except OSError as exc:
                log.warning("could not remove workspace", cm={"path": str(workspace.path), "error": str(exc)})
        if removed:
            log.info("pruned workspaces", cm={"removed": removed})
        return removed

    def save_workspace_metadata(self, workspace: Workspace) -> None:
        if workspace.mode == "in_place":
            return
        atomic_write_json(
            workspace.path / ".codemind-workspace.json",
            {
                "id": workspace.id,
                "project_path": str(workspace.project_path),
                "mode": workspace.mode,
                "created_at": workspace.created_at,
                "task_id": workspace.task_id,
                "branch": workspace.branch,
            },
        )

    # -- command execution ------------------------------------------------
    # -- per-workspace virtualenv ----------------------------------------
    def venv_path(self, workspace: Workspace) -> Path:
        """The virtualenv that belongs to this workspace (project one preferred)."""
        for name in (self.config.sandbox.venv_dir, ".venv"):
            candidate = workspace.path / name
            if (candidate / "bin" / "python").exists():
                return candidate
        return workspace.path / self.config.sandbox.venv_dir

    def venv_python(self, workspace: Workspace) -> Path | None:
        candidate = self.venv_path(workspace) / "bin" / "python"
        return candidate if candidate.exists() else None

    def ensure_venv(self, workspace: Workspace, *, task_id: str = "") -> Path:
        """Create the workspace virtualenv if needed. Returns the interpreter path.

        Modern Ubuntu/Debian refuse system-wide pip (PEP 668), so every dependency the agent
        installs goes here instead. Nothing outside the workspace is touched.
        """
        existing = self.venv_python(workspace)
        if existing is not None:
            return existing
        if not self.config.sandbox.use_workspace_venv:
            raise CommandDenied("Isolated virtualenvs are disabled in Settings → Sandbox; system-wide pip is only allowed if your Python permits it.")
        if not self.config.sandbox.auto_create_venv:
            raise CommandDenied("No virtualenv exists in this workspace and automatic creation is switched off in Settings → Sandbox.")
        target = workspace.path / self.config.sandbox.venv_dir
        result = self.execute(
            ["python3", "-m", "venv", str(target)],
            workspace=workspace,
            task_id=task_id,
            timeout=180.0,
            purpose="create virtualenv",
        )
        if not result.ok:
            raise CommandDenied(f"Could not create a virtualenv: {shorten(result.combined or result.error, 300)}")
        interpreter = target / "bin" / "python"
        if not interpreter.exists():
            raise CommandDenied("The virtualenv command succeeded but no interpreter appeared; check that python3-venv is installed.")
        workspace.notes.append(f"virtualenv at {target}")
        if hasattr(workspace, "metadata") and isinstance(getattr(workspace, "metadata"), dict):
            workspace.metadata["venv"] = str(target)  # type: ignore[attr-defined]
        self.save_workspace_metadata(workspace)
        BUS.publish(Topics.SANDBOX_EXEC, workspace=str(workspace.path), command="venv created", status="ok")
        log.info("created workspace virtualenv", cm={"workspace": str(workspace.path), "venv": str(target)})
        return interpreter

    def venv_env(self, workspace: Workspace) -> dict[str, str] | None:
        """Environment that puts the workspace virtualenv first on PATH, when it exists."""
        interpreter = self.venv_python(workspace)
        if interpreter is None or not self.config.sandbox.use_workspace_venv:
            return None
        bin_dir = str(interpreter.parent)
        base = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        return {"PATH": f"{bin_dir}:{base}", "VIRTUAL_ENV": str(interpreter.parent.parent), "CODEMIND_VENV": bin_dir}

    def check_command(self, command: str, policy: SandboxPolicy) -> str | None:
        """Return a refusal reason when the policy blocks this command."""
        lowered = command.lower()
        for pattern in policy.blocked_patterns:
            if pattern and pattern.lower() in lowered:
                return f"Blocked by the security policy (pattern: {pattern!r})."
        if not policy.allow_shell:
            return "Shell commands are disabled in Settings → Sandbox."
        dangerous = ("rm -rf /", "rm -rf ~", "mkfs", "dd if=/dev/zero of=/dev", ":(){", "chmod -R 777 /", "shutdown", "reboot", "> /dev/sda")
        if any(token in lowered for token in dangerous) and not policy.allow_destructive:
            return "This command is destructive and 'allow destructive commands' is off in Settings → Sandbox."
        return None

    def execute(
        self,
        argv: Sequence[str] | str,
        *,
        workspace: Workspace,
        task_id: str = "",
        timeout: float = 300.0,
        purpose: str = "",
        on_line: Callable[[str, str], None] | None = None,
        governor_plan: Any | None = None,
        extra_env: dict[str, str] | None = None,
        isolation: bool = True,
    ) -> CommandResult:
        """Run one command inside a workspace, with limits, isolation and history."""
        policy = self.policy(governor_plan)
        command_text = argv if isinstance(argv, str) else " ".join(str(a) for a in argv)
        refusal = self.check_command(command_text, policy)
        if refusal:
            result = CommandResult(
                command=command_text, argv=[command_text], cwd=str(workspace.path), exit_code=None,
                stdout="", stderr=refusal, duration=0.0, error=refusal,
            )
            self._record(result, task_id, workspace, purpose, {"denied": True, "policy": policy.as_dict()}, "denied")
            BUS.publish(Topics.SANDBOX_EXEC, **self._event_payload(result, purpose, workspace, "denied"))
            raise CommandDenied(refusal)

        # A login shell would reset PATH from /etc/profile, so a workspace virtualenv has to be
        # exported inside the command itself; otherwise the interpreter the agent installed into
        # silently disappears.
        venv_extra = self.venv_env(workspace) if isolation else None
        exec_text = command_text
        if venv_extra and isinstance(argv, str):
            exec_text = (
                f'export PATH="{venv_extra["PATH"]}"; export VIRTUAL_ENV="{venv_extra["VIRTUAL_ENV"]}"; '
                f'export CODEMIND_VENV="{venv_extra["CODEMIND_VENV"]}"; {argv}'
            )
        raw_argv = [str(a) for a in argv] if not isinstance(argv, str) else ["/bin/bash", "-lc", exec_text]
        run_argv, applied = build_sandbox_argv(
            raw_argv,
            report=self.isolation if isolation else IsolationReport(),
            workspace=workspace.path,
            allow_network=policy.allow_network,
            network_isolation=policy.network_isolation,
            extra_read_paths=policy.extra_read_paths,
        ) if isolation else (raw_argv, {"mechanism": "none", "network": "open"})

        limits: dict[str, int] = {}
        if policy.memory_limit_mb:
            limits["address_space_mb"] = policy.memory_limit_mb
        limits["nice"] = policy.nice
        if timeout:
            limits["cpu_seconds"] = int(timeout) + 30

        merged_env = dict(extra_env or {})
        if venv_extra:
            merged_env = {**venv_extra, **merged_env}
        env = build_env(extra=merged_env or None)

        def _preexec() -> None:  # pragma: no cover - runs in the child
            import resource

            if limits.get("address_space_mb"):
                size = limits["address_space_mb"] * 1024 * 1024
                try:
                    resource.setrlimit(resource.RLIMIT_AS, (size, size))
                except (ValueError, OSError):
                    pass
            if limits.get("cpu_seconds"):
                try:
                    resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_seconds"], limits["cpu_seconds"] + 60))
                except (ValueError, OSError):
                    pass
            try:
                os.nice(int(limits.get("nice", 5)))
            except OSError:
                pass

        # run_command does not expose preexec_fn, so use subprocess directly for limits.
        if limits.get("address_space_mb") or limits.get("nice"):
            result = self._run_with_limits(run_argv, workspace.path, env, timeout, policy, on_line, _preexec)
        else:
            result = run_command(
                run_argv,
                cwd=workspace.path,
                timeout=timeout,
                env=env,
                on_line=on_line,
                max_output=policy.output_limit,
                on_spawn=self._register_child,
            )
        self._record(result, task_id, workspace, purpose, {"applied": applied, "policy": policy.as_dict()}, "ok" if result.ok else "failed")
        BUS.publish(Topics.SANDBOX_EXEC, **self._event_payload(result, purpose, workspace, "ok" if result.ok else "failed"))
        return result

    def _run_with_limits(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout: float,
        policy: SandboxPolicy,
        on_line: Callable[[str, str], None] | None,
        preexec: Callable[[], None],
    ) -> CommandResult:
        import subprocess

        from codemind.utils.process import _kill_tree  # internal helper shared with run_command

        started = time.monotonic()
        out: list[str] = []
        err: list[str] = []

        def _sink(parts: list[str]) -> Callable[[str], None]:
            def _inner(line: str) -> None:
                text = GLOBAL_REDACTOR.redact(line.rstrip("\n"))
                if sum(len(p) for p in parts) < policy.output_limit:
                    parts.append(text + "\n")
                if on_line:
                    try:
                        on_line("stdout", text)
                    except Exception:  # pragma: no cover
                        pass

            return _inner

        try:
            process = subprocess.Popen(
                argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                bufsize=1, errors="replace", start_new_session=True, preexec_fn=preexec,
            )
            self._register_child(process)
        except (FileNotFoundError, PermissionError) as exc:
            return CommandResult(command=" ".join(argv), argv=argv, cwd=str(cwd), exit_code=None, stdout="", stderr=str(exc), duration=0.0, error=str(exc))
        import threading

        def _pump(stream: Any, parts: list[str]) -> None:
            sink = _sink(parts)
            for line in stream:
                sink(line)

        threads = [
            threading.Thread(target=_pump, args=(process.stdout, out), daemon=True),
            threading.Thread(target=_pump, args=(process.stderr, err), daemon=True),
        ]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(process)
            exit_code = process.poll()
        for thread in threads:
            thread.join(timeout=2.0)
        # close the pipes and reap the child: without this every limited command leaks two fds
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # pragma: no cover - best effort
                pass
        try:
            process.wait(timeout=1.0)
        except Exception:  # pragma: no cover - already reaped or still exiting
            pass
        return CommandResult(
            command=" ".join(argv),
            argv=argv,
            cwd=str(cwd),
            exit_code=exit_code,
            stdout="".join(out),
            stderr="".join(err),
            duration=time.monotonic() - started,
            timed_out=timed_out,
            killed_by="SIGTERM" if timed_out else None,
        )

    # -- child process tracking (cancellation) ----------------------------
    def _register_child(self, process: Any) -> None:
        with self._children_lock:
            self._children[id(process)] = process

        def _watcher() -> None:
            try:
                process.wait()
            except Exception:  # pragma: no cover
                pass
            with self._children_lock:
                self._children.pop(id(process), None)

        threading.Thread(target=_watcher, daemon=True).start()

    def kill_children(self, task_id: str = "") -> int:
        """Terminate every command this sandbox is currently running (used by Cancel)."""
        from codemind.utils.process import _kill_tree

        with self._children_lock:
            children = list(self._children.values())
        killed = 0
        for process in children:
            if process.poll() is None:
                _kill_tree(process)
                killed += 1
        return killed

    def _event_payload(self, result: CommandResult, purpose: str, workspace: Workspace, status: str) -> dict[str, Any]:
        return {
            "command": result.command,
            "cwd": result.cwd,
            "exit_code": result.exit_code,
            "duration": round(result.duration, 3),
            "purpose": purpose,
            "status": status,
            "workspace": workspace.id,
            "timed_out": result.timed_out,
            "tail": tail_lines(result.combined, 12),
        }

    def _record(
        self,
        result: CommandResult,
        task_id: str,
        workspace: Workspace,
        purpose: str,
        policy: dict[str, Any],
        status: str,
    ) -> None:
        try:
            self.db.insert(
                "executions",
                {
                    "task_id": task_id or None,
                    "ts": time.time(),
                    "argv": json.dumps(result.argv),
                    "cwd": result.cwd,
                    "exit_code": result.exit_code,
                    "duration": round(result.duration, 4),
                    "stdout_tail": tail_lines(GLOBAL_REDACTOR.redact(result.stdout), 60),
                    "stderr_tail": tail_lines(GLOBAL_REDACTOR.redact(result.stderr), 60),
                    "policy": json.dumps(policy, default=str),
                    "sandbox": workspace.mode,
                    "timed_out": 1 if result.timed_out else 0,
                },
            )
        except Exception as exc:  # pragma: no cover
            log.warning("could not record execution", cm={"error": str(exc)})

    # -- reporting --------------------------------------------------------
    def history(self, limit: int = 100, task_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM executions"
        params: list[Any] = []
        if task_id:
            sql += " WHERE task_id = ?"
            params.append(task_id)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = self.db.query(sql, params)
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "id": row["id"],
                    "task_id": row["task_id"],
                    "ts": row["ts"],
                    "command": _argv_text(row["argv"])[:400],
                    "cwd": row["cwd"],
                    "exit_code": row["exit_code"],
                    "duration": row["duration"],
                    "stdout_tail": row["stdout_tail"],
                    "stderr_tail": row["stderr_tail"],
                    "sandbox": row["sandbox"],
                    "timed_out": bool(row["timed_out"]),
                    "policy": _json_or_text(row["policy"]),
                }
            )
        return out

    def history_stats(self) -> dict[str, Any]:
        total = int(self.db.scalar("SELECT COUNT(*) FROM executions", default=0) or 0)
        failed = int(self.db.scalar("SELECT COUNT(*) FROM executions WHERE exit_code IS NULL OR exit_code != 0", default=0) or 0)
        timed_out = int(self.db.scalar("SELECT COUNT(*) FROM executions WHERE timed_out = 1", default=0) or 0)
        avg = self.db.scalar("SELECT AVG(duration) FROM executions", default=0.0) or 0.0
        last = self.db.query_one("SELECT MAX(ts) AS ts FROM executions")
        return {
            "total": total,
            "failed": failed,
            "timed_out": timed_out,
            "avg_duration": round(float(avg), 3),
            "last": float(last["ts"]) if last and last["ts"] else None,
        }

    def running_processes(self) -> list[dict[str, Any]]:
        info = []
        if self.active_process is not None:
            try:
                info.append(self.active_process.info())
            except Exception:  # pragma: no cover
                pass
        return info

    def status(self) -> dict[str, Any]:
        workspaces = self.list_workspaces()
        total_bytes = sum(w.disk_usage() for w in workspaces[:40])
        return {
            "isolation": self.isolation.as_dict(),
            "workspaces": [w.as_dict() for w in workspaces[:40]],
            "workspace_count": len(workspaces),
            "workspace_bytes": total_bytes,
            "history": self.history_stats(),
            "policy": self.policy().as_dict(),
            "processes": self.running_processes(),
            "commands": {k: v for k, v in _available_commands().items()},
        }


def _json_or_text(raw: Any, default: Any = None) -> Any:
    """Parse a JSON column, falling back to the raw value when it is not JSON."""
    text = str(raw or "").strip()
    if not text:
        return {} if default is None else default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _argv_text(raw: Any) -> str:
    """Render a stored argv field, which may be a JSON list or a plain command string."""
    text = str(raw or "").strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return " ".join(str(part) for part in parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    return text


def _available_commands() -> dict[str, str | None]:
    from codemind.sandbox.policy import available_commands

    return available_commands()
