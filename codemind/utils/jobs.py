"""Long-running commands: dev servers, watchers, background builds.

CodeMind can start a process that outlives a single tool call — the user's usual workflow when
a project needs a server running while the agent edits code. Jobs are real child processes with
their own log file, restart-friendly metadata in the database, and a kill switch. Output is
streamed to subscribers (the Sandbox view) and tailed from disk after a restart.
"""

from __future__ import annotations

import os
import signal
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.storage.db import Database
from codemind.utils.files import ensure_dir
from codemind.utils.process import StreamingProcess, build_env
from codemind.utils.textutil import shorten

log = get_logger("jobs")

MAX_LOG_BYTES = 4_000_000


@dataclass
class Job:
    id: str
    name: str
    argv: list[str]
    cwd: str
    log_path: str
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    exit_code: int | None = None
    pid: int | None = None
    status: str = "running"
    error: str = ""
    tail: list[str] = field(default_factory=list)
    task_id: str = ""

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def runtime(self) -> float:
        return (self.finished_at or time.time()) - self.started_at

    @property
    def command(self) -> str:
        return shorten(" ".join(self.argv), 200)

    def as_dict(self, *, lines: int = 12) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "command": self.command,
            "argv": self.argv,
            "cwd": self.cwd,
            "status": self.status,
            "running": self.running,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "runtime": f"{self.runtime:.1f}s" if self.runtime < 600 else f"{self.runtime / 60:.1f}m",
            "log_path": self.log_path,
            "error": self.error,
            "tail": self.tail[-lines:],
            "task_id": self.task_id,
        }


class JobManager:
    def __init__(self, db: Database, log_dir: Path, *, max_jobs: int = 12, max_log_bytes: int = MAX_LOG_BYTES) -> None:
        self.db = db
        self.log_dir = ensure_dir(log_dir)
        self.max_jobs = max_jobs
        self.max_log_bytes = max_log_bytes
        self._jobs: dict[str, Job] = {}
        self._processes: dict[str, StreamingProcess] = {}
        self._lock = threading.RLock()
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []

    # -- subscriptions ----------------------------------------------------
    def subscribe(self, listener: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, payload)
            except Exception:  # pragma: no cover
                pass
        BUS.publish(Topics.SANDBOX_EXEC, job_event=kind, **payload)

    # -- lifecycle --------------------------------------------------------
    def start(
        self,
        name: str,
        argv: Iterable[str],
        *,
        cwd: str | Path = ".",
        env_extra: dict[str, str] | None = None,
        task_id: str = "",
    ) -> Job:
        args = [str(a) for a in argv]
        if not args:
            raise ValueError("a command is required")
        with self._lock:
            running = [job for job in self._jobs.values() if job.running]
            if len(running) >= self.max_jobs:
                raise RuntimeError(f"{self.max_jobs} background processes are already running; stop one first")
        job_id = uuid.uuid4().hex[:10]
        log_path = self.log_dir / f"{job_id}.log"
        job = Job(
            id=job_id,
            name=name or os.path.basename(args[0]),
            argv=args,
            cwd=str(Path(cwd).expanduser().resolve()),
            log_path=str(log_path),
            task_id=task_id,
        )
        process = StreamingProcess(
            argv=args,
            cwd=job.cwd,
            env=build_env(extra=env_extra),
            on_line=lambda stream, line, jid=job_id: self._on_line(jid, stream, line),
        )
        try:
            process.start()
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = time.time()
            self._record(job)
            self._emit("job.failed", job.as_dict())
            raise
        job.pid = process.pid
        with self._lock:
            self._jobs[job_id] = job
            self._processes[job_id] = process
        threading.Thread(target=self._watch, args=(job_id, process), name=f"codemind-job-{job_id}", daemon=True).start()
        self._record(job)
        self._emit("job.started", job.as_dict())
        log.info("started background process", cm={"name": job.name, "pid": job.pid, "cwd": job.cwd})
        return job

    def _on_line(self, job_id: str, stream: str, line: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.tail.append(f"[{stream}] {line}" if stream == "stderr" else line)
        if len(job.tail) > 400:
            job.tail = job.tail[-400:]
        try:
            path = Path(job.log_path)
            # stat() on a missing file raises: without the exists() check the first line of every job
            # was swallowed and the log file never appeared at all
            if not path.exists() or path.stat().st_size < self.max_log_bytes:
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(f"{stream}: {line}\n")
        except OSError:
            pass
        self._emit("job.line", {"id": job_id, "stream": stream, "line": line})

    def _watch(self, job_id: str, process: StreamingProcess) -> None:
        code = process.wait()
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.exit_code = code
        job.finished_at = time.time()
        job.status = "stopped" if code in (0, -signal.SIGTERM, -15, None) else "failed"
        job.pid = None
        if job.status == "failed":
            job.error = f"exited with code {code}"
        self._record(job)
        self._emit("job.finished", job.as_dict())
        log.info("background process finished", cm={"name": job.name, "exit": code, "seconds": round(job.runtime, 1)})

    def stop(self, job_id: str, *, timeout: float = 5.0) -> bool:
        with self._lock:
            process = self._processes.get(job_id)
            job = self._jobs.get(job_id)
        if job is None:
            return False
        if process is not None and process.running:
            process.terminate(grace=timeout)
            return True
        return False

    def stop_all(self) -> int:
        with self._lock:
            ids = [job_id for job_id, job in self._jobs.items() if job.running]
        stopped = 0
        for job_id in ids:
            if self.stop(job_id):
                stopped += 1
        return stopped

    # -- queries ----------------------------------------------------------
    def jobs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            ordered = sorted(self._jobs.values(), key=lambda job: job.started_at, reverse=True)
        return [job.as_dict() for job in ordered[:limit]]

    def running(self) -> list[dict[str, Any]]:
        with self._lock:
            return [job.as_dict() for job in self._jobs.values() if job.running]

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def log_tail(self, job_id: str, lines: int = 200) -> str:
        job = self._jobs.get(job_id)
        if job is None:
            return ""
        try:
            text = Path(job.log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "\n".join(job.tail[-lines:])
        return "\n".join(text.splitlines()[-lines:])

    def history(self, limit: int = 40) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM executions WHERE task_id LIKE 'job:%' ORDER BY ts DESC LIMIT ?",
            (limit,),
        )
        return [
            {
                "id": row["id"],
                "ts": row["ts"],
                "command": row["argv"],
                "cwd": row["cwd"],
                "exit_code": row["exit_code"],
                "duration": row["duration"],
                "timed_out": bool(row["timed_out"]),
            }
            for row in rows
        ]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = len(self._jobs)
            running = sum(1 for job in self._jobs.values() if job.running)
            failed = sum(1 for job in self._jobs.values() if job.status == "failed")
        return {
            "total": total,
            "running": running,
            "failed": failed,
            "stopped": total - running - failed,
            "log_dir": str(self.log_dir),
            "history": self.history(20),
        }

    def shutdown(self) -> None:
        self.stop_all()

    # -- persistence ------------------------------------------------------
    def _record(self, job: Job) -> None:
        try:
            self.db.insert(
                "executions",
                {
                    "task_id": f"job:{job.id}",
                    "ts": job.started_at,
                    "argv": job.command,
                    "cwd": job.cwd,
                    "exit_code": job.exit_code,
                    "duration": job.runtime,
                    "stdout_tail": "\n".join(job.tail[-40:]),
                    "stderr_tail": job.error,
                    "policy": "background",
                    "sandbox": "",
                    "timed_out": 0,
                },
            )
        except Exception as exc:  # pragma: no cover
            log.debug("could not record job", cm={"error": str(exc)})
