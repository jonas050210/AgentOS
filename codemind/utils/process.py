"""Subprocess execution with timeouts, process-group teardown and streamed output.

Everything that shells out in CodeMind goes through :func:`run_command` or
:class:`StreamingProcess`. The contract is deliberately strict:

* the child gets its own session (``start_new_session``) so a timeout kills the whole tree;
* output is streamed line by line to a callback and always capped in memory;
* the environment is filtered (secrets from the parent are not copied blindly);
* results are structured - no exception escapes for a merely failing command.
"""

from __future__ import annotations

import os
import selectors
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

from codemind.utils.redaction import GLOBAL_REDACTOR, is_sensitive_env
from codemind.utils.textutil import strip_ansi

LineCallback = Callable[[str, str], None]  # (stream, line)


@dataclass
class CommandResult:
    """Structured outcome of a finished (or killed) command."""

    command: str
    argv: list[str]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    truncated: bool = False
    killed_by: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.error is None

    @property
    def combined(self) -> str:
        out = self.stdout
        if self.stderr:
            out = f"{out}\n{self.stderr}" if out else self.stderr
        return out

    def summary(self, limit: int = 400) -> str:
        if self.error:
            return f"error: {self.error}"
        if self.timed_out:
            return f"timed out after {self.duration:.1f}s"
        text = self.combined.strip().replace("\n", " ")
        text = text[: limit - 3] + "..." if len(text) > limit else text
        return f"exit {self.exit_code}: {text}" if text else f"exit {self.exit_code}"

    def as_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "duration": round(self.duration, 4),
            "timed_out": self.timed_out,
            "truncated": self.truncated,
            "error": self.error,
        }


def build_env(
    base: Mapping[str, str] | None = None,
    extra: Mapping[str, str] | None = None,
    *,
    drop_secrets: bool = True,
    keep: Sequence[str] = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "SHELL", "PYTHONPATH", "VIRTUAL_ENV", "DISPLAY", "XDG_RUNTIME_DIR", "CUDA_VISIBLE_DEVICES", "LD_LIBRARY_PATH"),
) -> dict[str, str]:
    source = dict(os.environ if base is None else base)
    env: dict[str, str] = {}
    for key, value in source.items():
        if key in keep:
            env[key] = value
        elif not drop_secrets and not is_sensitive_env(key):
            env[key] = value
    env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    env.setdefault("HOME", str(Path.home()))
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


def _kill_tree(process: subprocess.Popen[str], grace: float = 3.0) -> str:
    """Terminate the process group, escalating to SIGKILL. Returns the signal used."""
    try:
        pgid = os.getpgid(process.pid)
    except OSError:
        pgid = None
    used = "SIGTERM"
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=grace)
        return used
    except subprocess.TimeoutExpired:
        pass
    used = "SIGKILL"
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:  # pragma: no cover - unkillable child
        pass
    return used


def run_command(
    argv: Sequence[str] | str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float = 120.0,
    env: Mapping[str, str] | None = None,
    extra_env: Mapping[str, str] | None = None,
    shell: bool = False,
    input_text: str | None = None,
    on_line: LineCallback | None = None,
    max_output: int = 400_000,
    redact: bool = True,
    check_paths: bool = True,
    on_spawn: Callable[[subprocess.Popen], None] | None = None,
) -> CommandResult:
    """Run a command to completion with a hard timeout and bounded output capture."""
    if isinstance(argv, str):
        command = argv
        argv_list = ["/bin/bash", "-lc", argv] if (shell or True) else shlex.split(argv)
    else:
        argv_list = [str(a) for a in argv]
        command = shlex.join(argv_list)

    cwd_str = str(cwd or os.getcwd())
    if check_paths and not Path(cwd_str).exists():
        return CommandResult(
            command=command, argv=list(argv_list), cwd=cwd_str, exit_code=None, stdout="", stderr="",
            duration=0.0, error=f"working directory does not exist: {cwd_str}",
        )

    started = time.monotonic()
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    captured = {"stdout": 0, "stderr": 0}
    truncated = False

    def _make_sink(stream_name: str, parts: list[str]) -> Callable[[str], None]:
        def _sink(line: str) -> None:
            nonlocal truncated
            clean = strip_ansi(line)
            if redact:
                clean = GLOBAL_REDACTOR.redact(clean)
            remaining = max_output - captured[stream_name]
            if remaining <= 0:
                truncated = True
                return
            if len(clean) > remaining:
                clean = clean[:remaining]
                truncated = True
            captured[stream_name] += len(clean)
            parts.append(clean)
            if on_line is not None:
                try:
                    on_line(stream_name, clean)
                except Exception:  # pragma: no cover - listener isolation
                    pass

        return _sink

    stdout_sink = _make_sink("stdout", stdout_parts)
    stderr_sink = _make_sink("stderr", stderr_parts)

    try:
        process = subprocess.Popen(
            argv_list,
            cwd=cwd_str,
            env=build_env(extra=extra_env) if env is None else dict(env),
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            errors="replace",
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        return CommandResult(
            command=command, argv=list(argv_list), cwd=cwd_str, exit_code=127, stdout="", stderr="",
            duration=time.monotonic() - started, error=f"command not found: {exc.filename or argv_list[0]}",
        )
    except PermissionError as exc:
        return CommandResult(
            command=command, argv=list(argv_list), cwd=cwd_str, exit_code=126, stdout="", stderr="",
            duration=time.monotonic() - started, error=f"not executable: {exc}",
        )

    if on_spawn is not None:
        try:
            on_spawn(process)
        except Exception:  # pragma: no cover - registrar isolation
            pass

    if input_text is not None and process.stdin is not None:
        try:
            process.stdin.write(input_text)
            process.stdin.close()
        except OSError:
            pass

    timed_out = False
    killed_by: str | None = None
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, stdout_sink)
    selector.register(process.stderr, selectors.EVENT_READ, stderr_sink)
    deadline = started + timeout if timeout and timeout > 0 else None
    open_streams = 2

    try:
        while open_streams > 0:
            if deadline is not None and time.monotonic() > deadline and process.poll() is None:
                timed_out = True
                killed_by = _kill_tree(process)
                deadline = None
            remaining = 0.25 if deadline is None else max(0.01, min(0.25, deadline - time.monotonic()))
            for key, _ in selector.select(timeout=remaining):
                line = key.fileobj.readline()  # type: ignore[union-attr]
                if line:
                    key.data(line)
                else:
                    selector.unregister(key.fileobj)
                    open_streams -= 1
            if process.poll() is not None and open_streams > 0:
                # drain whatever is left buffered
                for key in list(selector.get_map().values()):
                    rest = key.fileobj.read()  # type: ignore[union-attr]
                    if rest:
                        for chunk in rest.splitlines(keepends=True):
                            key.data(chunk)
                    try:
                        selector.unregister(key.fileobj)
                    except (KeyError, ValueError):
                        pass
                open_streams = 0
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    try:
        exit_code = process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover
        killed_by = _kill_tree(process)
        exit_code = process.poll()

    if timed_out and killed_by is None:
        killed_by = "SIGTERM"

    return CommandResult(
        command=command,
        argv=list(argv_list),
        cwd=cwd_str,
        exit_code=exit_code,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
        duration=time.monotonic() - started,
        timed_out=timed_out,
        truncated=truncated,
        killed_by=killed_by,
    )


@dataclass
class StreamingProcess:
    """A long-running child process with live output and explicit lifecycle control."""

    argv: Sequence[str]
    cwd: str
    env: dict[str, str] | None = None
    on_line: LineCallback | None = None
    max_lines: int = 4000

    process: subprocess.Popen[str] | None = None
    lines: list[tuple[str, str]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    exit_code: int | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _threads: list[threading.Thread] = field(default_factory=list)

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process else None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> "StreamingProcess":
        self.process = subprocess.Popen(
            [str(a) for a in self.argv],
            cwd=self.cwd,
            env=self.env or build_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            errors="replace",
            start_new_session=True,
        )
        for stream in (self.process.stdout, self.process.stderr):
            if stream is None:
                continue
            thread = threading.Thread(target=self._pump, args=(stream, stream is self.process.stderr and "stderr" or "stdout"), daemon=True)
            thread.start()
            self._threads.append(thread)
        return self

    def _pump(self, stream, name: str) -> None:  # type: ignore[no-untyped-def]
        try:
            for raw in stream:
                line = GLOBAL_REDACTOR.redact(strip_ansi(raw.rstrip("\n")))
                with self._lock:
                    self.lines.append((name, line))
                    if len(self.lines) > self.max_lines:
                        del self.lines[: len(self.lines) - self.max_lines]
                if self.on_line is not None:
                    try:
                        self.on_line(name, line)
                    except Exception:  # pragma: no cover
                        pass
        except (ValueError, OSError):  # stream closed under us
            return

    def wait(self, timeout: float | None = None) -> int | None:
        if self.process is None:
            return None
        try:
            self.exit_code = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        self.finished_at = time.time()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._close_streams()
        return self.exit_code

    def _close_streams(self) -> None:
        """Release the pipes once the process is gone (they are pumped by threads)."""
        if self.process is None:
            return
        for stream in (self.process.stdout, self.process.stderr):
            try:
                if stream is not None and not stream.closed:
                    stream.close()
            except OSError:  # pragma: no cover
                pass

    def __del__(self) -> None:  # pragma: no cover - safety net
        try:
            self._close_streams()
        except Exception:
            pass

    def terminate(self, grace: float = 3.0) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        _kill_tree(self.process, grace=grace)
        self.exit_code = self.process.poll()
        self.finished_at = time.time()
        for thread in self._threads:
            thread.join(timeout=1.0)
        self._close_streams()

    def tail(self, lines: int = 50) -> str:
        with self._lock:
            return "\n".join(text for _stream, text in self.lines[-lines:])

    def info(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "argv": [str(a) for a in self.argv],
            "cwd": self.cwd,
            "running": self.running,
            "exit_code": self.exit_code if self.exit_code is not None else self.process.poll() if self.process else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "lines": len(self.lines),
        }


def which(name: str) -> str | None:
    from shutil import which as _which

    return _which(name)


def command_available(name: str) -> bool:
    return which(name) is not None
