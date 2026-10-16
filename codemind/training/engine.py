"""Training run orchestration: start, monitor, pause, resume, checkpoint, cancel.

Runs are real jobs. ``adapter_lite`` trains in-process on a worker thread (so progress is
streamed directly), while LoRA/QLoRA runs a child process whose stdout is a JSONL progress
stream. Both write checkpoints to disk and both survive a restart: on startup any run that was
in flight is marked ``interrupted`` and can be resumed from its last checkpoint.

The engine also honours the resource governor: when the plan says training must yield to
coding tasks (or memory is exhausted), the run pauses at the next checkpoint and resumes when
the machine is free again.
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
from typing import Any, Callable, Iterable, Sequence

from codemind.config import Config
from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.storage.db import Database, _loads
from codemind.storage.layout import StorageLayout
from codemind.system.hardware import HardwareProfile
from codemind.training.datasets import DatasetBuilder, Example
from codemind.training.methods import evaluate_methods, method_info
from codemind.training.nn import AdapterModel, TrainMetrics, train_adapter
from codemind.utils.files import atomic_write_json, ensure_dir, human_bytes, read_json
from codemind.utils.process import StreamingProcess, build_env
from codemind.utils.textutil import tail_lines

log = get_logger("training.engine")

ACTIVE_STATUSES = ("running", "pausing", "starting")
TERMINAL_STATUSES = ("completed", "failed", "cancelled", "interrupted", "paused")


@dataclass
class TrainingRun:
    id: str
    name: str
    method: str
    dataset: str
    base_model: str = ""
    status: str = "queued"
    config: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    log_path: str = ""
    pid: int | None = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    resumed_from: str = ""

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def as_dict(self) -> dict[str, Any]:
        elapsed = (self.finished_at or time.time()) - (self.started_at or self.created_at)
        return {
            "id": self.id,
            "name": self.name,
            "method": self.method,
            "dataset": self.dataset,
            "base_model": self.base_model,
            "status": self.status,
            "config": self.config,
            "progress": self.progress,
            "metrics": self.metrics,
            "artifacts": self.artifacts,
            "checkpoint": self.checkpoint,
            "log_path": self.log_path,
            "pid": self.pid,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": round(max(0.0, elapsed), 1),
            "resumed_from": self.resumed_from,
            "is_active": self.is_active,
        }

    @classmethod
    def from_row(cls, row: Any) -> "TrainingRun":
        return cls(
            id=row["id"],
            name=row["name"],
            method=row["method"],
            dataset=row["dataset"] or "",
            base_model=row["base_model"] or "",
            status=row["status"],
            config=_loads(row["config"], {}) or {},
            progress=_loads(row["progress"], {}) or {},
            metrics=_loads(row["metrics"], {}) or {},
            artifacts=_loads(row["artifacts"], {}) or {},
            checkpoint=_loads(row["checkpoint"], {}) or {},
            log_path=row["log_path"] or "",
            pid=row["pid"],
            error=row["error"] or "",
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            resumed_from=row["resumed_from"] or "",
        )


class TrainingEngine:
    """Owns the lifecycle of training runs."""

    def __init__(
        self,
        config: Config,
        db: Database,
        layout: StorageLayout,
        hardware: HardwareProfile,
        *,
        datasets: DatasetBuilder,
        governor: Any = None,
        sampler: Any = None,
        on_finished: Callable[[TrainingRun], None] | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.layout = layout
        self.hardware = hardware
        self.datasets = datasets
        self.governor = governor
        self.sampler = sampler
        self.on_finished = on_finished
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._stop_flags: dict[str, str] = {}  # run_id -> "cancel" | "pause"
        self._processes: dict[str, StreamingProcess] = {}
        self._callbacks: list[Callable[[TrainingRun], None]] = []
        self.restore()

    # -- config -----------------------------------------------------------
    def update_config(self, config: Config) -> None:
        self.config = config

    def update_hardware(self, hardware: HardwareProfile) -> None:
        self.hardware = hardware

    # -- persistence ------------------------------------------------------
    def _persist(self, run: TrainingRun) -> None:
        self.db.upsert(
            "training_runs",
            {
                "id": run.id,
                "name": run.name,
                "method": run.method,
                "base_model": run.base_model,
                "dataset": run.dataset,
                "status": run.status,
                "created_at": run.created_at,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "config": json.dumps(run.config, default=str),
                "progress": json.dumps(run.progress, default=str),
                "metrics": json.dumps(run.metrics, default=str),
                "artifacts": json.dumps(run.artifacts, default=str),
                "checkpoint": json.dumps(run.checkpoint, default=str),
                "log_path": run.log_path,
                "pid": run.pid,
                "priority": self.config.training.background_priority,
                "error": run.error,
                "resumed_from": run.resumed_from,
            },
            ["id"],
        )

    def _publish(self, run: TrainingRun, event: str = "updated") -> None:
        payload = run.as_dict()
        payload["event"] = event
        if self.sampler is not None:
            payload["resources"] = self.sampler.latest.as_dict()
        BUS.publish(Topics.TRAINING_PROGRESS, **payload)
        for callback in list(self._callbacks):
            try:
                callback(run)
            except Exception:  # pragma: no cover
                pass

    # -- queries ----------------------------------------------------------
    def get(self, run_id: str) -> TrainingRun | None:
        row = self.db.query_one("SELECT * FROM training_runs WHERE id = ?", (run_id,))
        return TrainingRun.from_row(row) if row else None

    def list(self, limit: int = 50) -> list[TrainingRun]:
        rows = self.db.query("SELECT * FROM training_runs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [TrainingRun.from_row(row) for row in rows]

    def active(self) -> list[TrainingRun]:
        rows = self.db.query(
            f"SELECT * FROM training_runs WHERE status IN ({','.join('?' for _ in ACTIVE_STATUSES)})",
            ACTIVE_STATUSES,
        )
        return [TrainingRun.from_row(row) for row in rows]

    def checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM checkpoints WHERE run_id = ? ORDER BY step", (run_id,))
        return [
            {"step": row["step"], "path": row["path"], "loss": row["loss"], "created_at": row["created_at"], "metrics": _loads(row["metrics"], {}) or {}}
            for row in rows
        ]

    def events(self, run_id: str, limit: int = 400) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM training_events WHERE run_id = ? ORDER BY ts DESC LIMIT ?", (run_id, limit))
        return [dict(row) | {"payload": _loads(row["payload"], {}) or {}} for row in reversed(rows)]

    def log_tail(self, run_id: str, lines: int = 60) -> str:
        run = self.get(run_id)
        if run is None or not run.log_path:
            return ""
        path = Path(run.log_path)
        if not path.exists():
            return ""
        try:
            return tail_lines(path.read_text(encoding="utf-8", errors="replace"), lines)
        except OSError:
            return ""

    def _record_event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        try:
            self.db.insert("training_events", {"run_id": run_id, "ts": time.time(), "kind": kind, "payload": json.dumps(payload, default=str)[:8000]})
        except Exception:  # pragma: no cover
            pass

    # -- lifecycle --------------------------------------------------------
    def restore(self) -> list[str]:
        """Mark runs interrupted by a restart and expose them for resume."""
        rows = self.db.query(f"SELECT id, name FROM training_runs WHERE status IN ({','.join('?' for _ in ACTIVE_STATUSES)})", ACTIVE_STATUSES)
        recovered: list[str] = []
        for row in rows:
            self.db.execute(
                "UPDATE training_runs SET status = 'interrupted', error = ? WHERE id = ?",
                ("CodeMind stopped while this run was in flight. Resume it to continue from its last checkpoint.", row["id"]),
            )
            self._record_event(row["id"], "interrupted", {"reason": "process restart"})
            recovered.append(row["id"])
        if recovered:
            log.info("training runs marked interrupted", cm={"count": len(recovered)})
        return recovered

    def start(
        self,
        *,
        name: str = "",
        method: str = "auto",
        dataset: str = "",
        base_model: str = "",
        config_overrides: dict[str, Any] | None = None,
        resume_from: str = "",
        origin: str = "user",
    ) -> TrainingRun:
        if not self.config.training.enabled:
            raise RuntimeError("training is disabled in Settings → Training")
        if method == "auto":
            method = self._auto_method()
        info = method_info(method, self.config, self.hardware)
        if info is None:
            raise RuntimeError(f"unknown training method {method!r}")
        if not info.available:
            raise RuntimeError(f"{info.title} is not available here: " + "; ".join(info.requirements))
        dataset_path = self._resolve_dataset(dataset, method)
        run = TrainingRun(
            id=uuid.uuid4().hex[:12],
            name=name or f"{method} run {time.strftime('%H:%M')}",
            method=method,
            dataset=dataset_path,
            base_model=base_model or self._default_base_model(method),
            status="starting",
            config={
                "origin": origin,
                **{f"training.{key}": value for key, value in (config_overrides or {}).items() if not key.startswith("training.")},
                **(config_overrides or {}),
            },
        )
        run.config.setdefault("eval_suite", self.config.training.eval_suite)
        run.config.setdefault("precision", self.config.training.precision if self.config.training.precision != "auto" else ("4bit" if method == "qlora" else "bf16"))
        log_dir = self.layout.resolve("logs") / "training"
        ensure_dir(log_dir)
        run.log_path = str(log_dir / f"{run.id}.jsonl")
        run.resumed_from = resume_from
        self._persist(run)
        self._record_event(run.id, "created", {"method": method, "dataset": dataset_path, "origin": origin})
        self._publish(run, "created")
        thread = threading.Thread(target=self._run_worker, args=(run.id,), name=f"codemind-train-{run.id[:6]}", daemon=True)
        with self._lock:
            self._threads[run.id] = thread
        thread.start()
        return run

    def _auto_method(self) -> str:
        from codemind.training.methods import resolve_auto_method

        return resolve_auto_method(self.config, self.hardware)

    def _resolve_dataset(self, dataset: str, method: str) -> str:
        if dataset:
            candidate = Path(dataset).expanduser()
            if candidate.exists():
                return str(candidate)
            registered = self.db.query_one("SELECT path FROM datasets WHERE name = ?", (dataset,))
            if registered:
                return str(registered["path"])
            raise RuntimeError(f"dataset {dataset!r} does not exist")
        if method == "retrieval_index":
            return ""
        rows = self.db.query("SELECT path, examples FROM datasets ORDER BY updated_at DESC LIMIT 1")
        if not rows:
            raise RuntimeError(
                "no dataset available yet. Finish a few verified tasks (or import a JSONL file in Training → Datasets), "
                "then CodeMind can export the traces it collected."
            )
        return str(rows[0]["path"])

    def _default_base_model(self, method: str) -> str:
        if method == "adapter_lite":
            return "built-in adapter (no base model)"
        if self.config.training.base_model:
            return self.config.training.base_model
        if self.config.models.active_model and Path(self.config.models.active_model).exists():
            return self.config.models.active_model
        plan = self.hardware.training_plan()
        return plan.get("model", "Qwen2.5-Coder-1.5B-Instruct")

    def cancel(self, run_id: str, *, pause: bool = False) -> bool:
        run = self.get(run_id)
        if run is None or not run.is_active:
            return False
        with self._lock:
            self._stop_flags[run_id] = "pause" if pause else "cancel"
        process = self._processes.get(run_id)
        if process is not None:
            process.terminate()
        run.status = "pausing" if pause else "cancelling"
        self._persist(run)
        self._publish(run, "pausing" if pause else "cancelling")
        return True

    def resume(self, run_id: str) -> TrainingRun:
        run = self.get(run_id)
        if run is None:
            raise RuntimeError(f"unknown run {run_id}")
        if run.is_active:
            raise RuntimeError("run is already active")
        checkpoint = run.checkpoint.get("path") if run.checkpoint else ""
        return self.start(
            name=f"{run.name} (resumed)",
            method=run.method,
            dataset=run.dataset,
            base_model=run.base_model,
            config_overrides={k: v for k, v in (run.config or {}).items() if "." not in k},
            resume_from=checkpoint or run.checkpoint.get("adapter", "") or "",
            origin=run.config.get("origin", "user") if isinstance(run.config, dict) else "user",
        )

    # -- workers ----------------------------------------------------------
    def _run_worker(self, run_id: str) -> None:
        run = self.get(run_id)
        if run is None:
            return
        run.started_at = time.time()
        run.status = "running"
        self._persist(run)
        self._publish(run, "started")
        BUS.publish(Topics.TRAINING_RUN, run_id=run.id, status="running", method=run.method, name=run.name)
        try:
            if run.method == "adapter_lite":
                self._run_adapter(run)
            elif run.method == "retrieval_index":
                self._run_retrieval(run)
            else:
                self._run_peft(run)
        except Exception as exc:
            log.exception("training run failed", cm={"run": run.id})
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished_at = time.time()
            self._persist(run)
            self._record_event(run.id, "error", {"message": run.error})
            self._publish(run, "failed")
        finally:
            with self._lock:
                self._threads.pop(run_id, None)
                self._stop_flags.pop(run_id, None)
                self._processes.pop(run_id, None)
            if self.on_finished is not None:
                try:
                    self.on_finished(self.get(run_id) or run)
                except Exception as exc:  # pragma: no cover
                    log.warning("training finish hook failed", cm={"error": str(exc)})

    # -- adapter_lite -----------------------------------------------------
    def _run_adapter(self, run: TrainingRun) -> None:
        examples = self.datasets.load(run.dataset)
        train_pairs: list[tuple[str, str]] = []
        validation_pairs: list[tuple[str, str]] = []
        for example in examples:
            label = None
            if example.action:
                label = str(example.action.get("tool") or example.action.get("label") or "")
            elif example.output and example.output.strip().startswith("{"):
                try:
                    payload = json.loads(example.output)
                    label = str(payload.get("tool") or "")
                except json.JSONDecodeError:
                    label = None
            if not label:
                continue
            text = f"{example.instruction} {example.context}".strip()
            train_pairs.append((text, label))
        validation_items = self.datasets._dataset_validation_path(run.dataset)  # type: ignore[attr-defined]
        if validation_items and Path(validation_items).exists():
            for example in self.datasets.load(validation_items):
                label = None
                if example.action:
                    label = str(example.action.get("tool") or "")
                elif example.output and example.output.strip().startswith("{"):
                    try:
                        label = str(json.loads(example.output).get("tool") or "")
                    except json.JSONDecodeError:
                        label = None
                if label:
                    validation_pairs.append((f"{example.instruction} {example.context}".strip(), label))
        minimum = max(1, int(self.config.training.min_tool_examples))
        if len(train_pairs) < minimum:
            raise RuntimeError(
                f"only {len(train_pairs)} usable tool-selection examples in {Path(run.dataset).name} "
                f"(a run needs at least {minimum}). Finish more verified tasks, import a dataset that has "
                "action labels, or use the retrieval_index method, which works from unlabelled material."
            )
        checkpoint_dir = self.layout.resolve("checkpoints") / run.id
        ensure_dir(checkpoint_dir)
        # Small curated datasets need several passes to converge; the defaults scale with size.
        n = len(train_pairs)
        default_epochs = 20 if n < 64 else (12 if n < 512 else 5)
        epochs = int(run.config.get("epochs") or (default_epochs if self.config.training.epochs <= 1 else self.config.training.epochs))
        batch = int(run.config.get("batch_size") or 8)
        lr = float(run.config.get("learning_rate") or 0.25)
        checkpoint_every = int(self.config.training.checkpoint_every / 10) or 5

        def should_stop() -> bool:
            flag = self._stop_flags.get(run.id)
            if flag:
                return True
            plan = self.governor.plan if self.governor is not None else None
            if plan is not None and plan.pause_training:
                self._stop_flags[run.id] = "pause"
                self._record_event(run.id, "governor-pause", {"reason": plan.pause_reason})
                return True
            return False

        def on_step(metrics: TrainMetrics) -> None:
            payload = metrics.as_dict()
            run.progress = {
                "step": metrics.step,
                "epoch": payload["epoch"],
                "loss": payload["loss"],
                "accuracy": payload["accuracy"],
                "grad_norm": payload["grad_norm"],
                "examples_seen": metrics.examples_seen,
                "elapsed": payload["seconds"],
                "throughput": round(metrics.examples_seen / metrics.seconds, 1) if metrics.seconds else 0.0,
            }
            self._persist(run)
            self._publish(run, "progress")
            self._append_log(run, {"event": "step", **payload})

        resume_path = Path(run.resumed_from) if run.resumed_from and Path(run.resumed_from).exists() else None
        self._record_event(run.id, "start", {"examples": len(train_pairs), "validation": len(validation_pairs), "epochs": epochs})
        model, history = train_adapter(
            train_pairs,
            validation_pairs,
            epochs=epochs,
            batch_size=batch,
            lr=lr,
            on_step=on_step,
            should_stop=should_stop,
            checkpoint_every=checkpoint_every,
            checkpoint_dir=checkpoint_dir,
            resume_from=resume_path,
            task="tool_choice",
        )
        flag = self._stop_flags.get(run.id)
        if flag:
            target = checkpoint_dir / f"step-{model.step}.json"
            model.save(target)
            run.checkpoint = {"step": model.step, "path": str(target)}
            run.status = "paused" if flag == "pause" else "cancelled"
            run.finished_at = time.time()
            self._persist(run)
            self._record_checkpoint(run, model.step, target, None, {})
            self._publish(run, run.status)
            return
        models_dir = self.layout.resolve("models") / "adapter" / run.id
        ensure_dir(models_dir)
        artifact = model.save(models_dir / "adapter.json")
        final_loss, accuracy = model.evaluate(validation_pairs or train_pairs[: min(200, len(train_pairs))])
        run.metrics = {
            "examples": len(train_pairs),
            "validation_examples": len(validation_pairs),
            "labels": model.labels,
            "final_loss": round(final_loss, 4),
            "accuracy": round(accuracy, 4),
            "steps": model.step,
            "history": [metrics.as_dict() for metrics in history][-200:],
            "trainable_params": len(model.labels) * model.hidden + model.hidden,
        }
        run.artifacts = {"adapter": str(artifact), "kind": "adapter_lite", "labels": model.labels}
        run.status = "completed"
        run.finished_at = time.time()
        self._record_checkpoint(run, model.step, artifact, final_loss, run.metrics)
        self._persist(run)
        self._append_log(run, {"event": "done", "artifacts": run.artifacts, "metrics": run.metrics})
        self._record_event(run.id, "completed", {"accuracy": run.metrics["accuracy"], "examples": len(train_pairs)})
        self._publish(run, "completed")

    # -- retrieval --------------------------------------------------------
    def _run_retrieval(self, run: TrainingRun) -> None:
        from codemind.training.retrieval import build_retrieval_index

        documents: list[dict[str, Any]] = []
        for row in self.db.query("SELECT title, body, tags FROM memories ORDER BY updated_at DESC LIMIT 2000"):
            documents.append({"id": f"memory:{row['title'][:40]}", "text": f"{row['title']} {row['body']} {row['tags'] or ''}"})
        for row in self.db.query("SELECT path, IFNULL(symbol,'') AS symbol, summary FROM knowledge WHERE kind IN ('file','class','function') LIMIT 4000"):
            documents.append({"id": f"code:{row['path']}:{row['symbol']}", "text": f"{row['path']} {row['symbol']} {row['summary'] or ''}"})
        if len(documents) < 10:
            raise RuntimeError("not enough indexed knowledge yet: analyse a repository and store a few memories first")

        def on_step(step: dict[str, Any]) -> None:
            run.progress = step
            self._persist(run)
            self._publish(run, "progress")

        result = build_retrieval_index(documents, on_step=on_step, should_stop=lambda: bool(self._stop_flags.get(run.id)))
        models_dir = self.layout.resolve("models") / "retrieval" / run.id
        ensure_dir(models_dir)
        artifact = models_dir / "index.json"
        atomic_write_json(artifact, result["index"])
        run.metrics = result["metrics"]
        run.artifacts = {"index": str(artifact), "kind": "retrieval_index", "documents": len(documents)}
        run.checkpoint = {"step": 1, "path": str(artifact)}
        run.status = "completed"
        run.finished_at = time.time()
        self._record_checkpoint(run, 1, artifact, run.metrics.get("best_avg_ndcg"), run.metrics)
        self._persist(run)
        self._record_event(run.id, "completed", run.metrics)
        self._publish(run, "completed")

    # -- LoRA / QLoRA -----------------------------------------------------
    def _run_peft(self, run: TrainingRun) -> None:
        from codemind.utils.process import run_command

        info = method_info(run.method, self.config, self.hardware)
        if info is None or not info.available:
            raise RuntimeError("this method is not available on this machine: " + "; ".join(info.requirements if info else []))
        output_dir = self.layout.resolve("checkpoints") / "peft" / run.id
        ensure_dir(output_dir)
        checkpoint_dir = output_dir / "checkpoints"
        ensure_dir(checkpoint_dir)
        validation = self.datasets._dataset_validation_path(run.dataset)  # type: ignore[attr-defined]
        worker_config = {
            "base_model": run.base_model,
            "dataset": run.dataset,
            "validation_dataset": validation,
            "output_dir": str(output_dir),
            "checkpoint_dir": str(checkpoint_dir),
            "precision": run.config.get("precision", "4bit"),
            "lora_rank": int(run.config.get("lora_rank") or self.config.training.lora_rank),
            "lora_alpha": int(run.config.get("lora_alpha") or self.config.training.lora_alpha),
            "lora_dropout": float(run.config.get("lora_dropout") or self.config.training.lora_dropout),
            "learning_rate": float(run.config.get("learning_rate") or self.config.training.learning_rate),
            "batch_size": int(run.config.get("batch_size") or self.config.training.batch_size),
            "grad_accum": int(run.config.get("grad_accum") or self.config.training.grad_accum),
            "epochs": int(run.config.get("epochs") or self.config.training.epochs),
            "max_steps": int(self.config.training.max_steps),
            "checkpoint_every": int(self.config.training.checkpoint_every),
            "max_seq_len": int(run.config.get("max_seq_len") or self.hardware.training_plan().get("max_seq_len", 1024)),
            "resume_from": run.resumed_from or None,
            "require_cuda": run.method == "qlora",
        }
        config_path = output_dir / "run.json"
        atomic_write_json(config_path, worker_config)
        argv = [self._python(), "-m", "codemind.training.worker", "--config", str(config_path)]
        ensure_dir(Path(run.log_path).parent)
        process = StreamingProcess(argv=argv, cwd=str(self._project_root()), env=build_env(), on_line=lambda stream, line: self._handle_worker_line(run, stream, line))
        with self._lock:
            self._processes[run.id] = process
        process.start()
        run.pid = process.pid
        self._persist(run)
        self._publish(run, "worker-started")
        exit_code = process.wait(timeout=None)
        if self._stop_flags.get(run.id) == "pause":
            run.status = "paused"
            run.finished_at = time.time()
            run.error = "paused to give priority to coding tasks; resume when the machine is free"
        elif self._stop_flags.get(run.id) == "cancel":
            run.status = "cancelled"
            run.finished_at = time.time()
        elif exit_code == 0 and run.status not in ("completed",):
            adapter = output_dir / "adapter"
            if adapter.exists():
                run.artifacts = {"adapter": str(adapter), "kind": run.method, "base_model": run.base_model}
                run.status = "completed"
            else:
                run.status = "failed"
                run.error = "the worker finished without writing an adapter"
            run.finished_at = time.time()
        elif run.status != "completed":
            run.status = "failed"
            run.error = run.error or f"worker exited with code {exit_code}"
            run.finished_at = time.time()
        self._persist(run)
        self._record_event(run.id, run.status, {"exit_code": exit_code, "error": run.error})
        self._publish(run, run.status)

    def _handle_worker_line(self, run: TrainingRun, stream: str, line: str) -> None:
        self._append_log(run, {"stream": stream, "line": line})
        if stream != "stdout" or not line.startswith("{"):
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        event = payload.get("event")
        if event == "step":
            run.progress = {
                "step": payload.get("step"),
                "loss": payload.get("loss"),
                "epoch": payload.get("epoch"),
                "learning_rate": payload.get("lr"),
                "elapsed": payload.get("elapsed"),
                "examples_seen": payload.get("examples"),
                "throughput": round((payload.get("examples") or 0) / max(0.1, payload.get("elapsed") or 0.1), 2),
            }
            self._persist(run)
            self._publish(run, "progress")
        elif event == "start":
            run.progress = {"step": 0, "total_steps": payload.get("steps"), "device": payload.get("device"),
                            "trainable_params": payload.get("trainable_params"), "trainable_percent": payload.get("trainable_percent")}
            self._persist(run)
            self._publish(run, "started-training")
        elif event == "checkpoint":
            path = Path(payload.get("path", ""))
            run.checkpoint = {"step": payload.get("step"), "path": str(path / "training_state.pt") if path.exists() else str(path)}
            self._record_checkpoint(run, int(payload.get("step") or 0), path, payload.get("metrics", {}).get("eval_loss") if payload.get("metrics") else None, payload.get("metrics") or {})
            self._persist(run)
            self._publish(run, "checkpoint")
        elif event == "eval":
            metrics = dict(run.metrics)
            metrics["eval"] = {"step": payload.get("step"), "loss": payload.get("eval_loss"), "perplexity": payload.get("perplexity")}
            run.metrics = metrics
            BUS.publish(Topics.TRAINING_EVAL, run_id=run.id, **payload)
            self._persist(run)
        elif event == "done":
            run.metrics = {**run.metrics, **(payload.get("metrics") or {})}
            run.artifacts = {"adapter": payload.get("adapter"), "kind": run.method, "base_model": run.base_model}
            run.status = "completed"
            run.finished_at = time.time()
            self._persist(run)
        elif event == "error":
            run.error = str(payload.get("message"))
            run.status = "failed"
            run.finished_at = time.time()
            self._persist(run)
            self._publish(run, "failed")

    def _append_log(self, run: TrainingRun, payload: dict[str, Any]) -> None:
        if not run.log_path:
            return
        try:
            with open(run.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"ts": time.time(), **payload}, default=str) + "\n")
        except OSError:
            pass

    def _record_checkpoint(self, run: TrainingRun, step: int, path: Path, loss: float | None, metrics: dict[str, Any]) -> None:
        try:
            self.db.insert(
                "checkpoints",
                {"run_id": run.id, "step": step, "path": str(path), "loss": loss, "created_at": time.time(), "metrics": json.dumps(metrics, default=str)},
            )
        except Exception:  # pragma: no cover
            pass

    # -- helpers ----------------------------------------------------------
    def _python(self) -> str:
        return os.environ.get("CODEMIND_PYTHON", "python3")

    def _project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def stats(self) -> dict[str, Any]:
        total = int(self.db.scalar("SELECT COUNT(*) FROM training_runs", default=0) or 0)
        completed = int(self.db.scalar("SELECT COUNT(*) FROM training_runs WHERE status = 'completed'", default=0) or 0)
        failed = int(self.db.scalar("SELECT COUNT(*) FROM training_runs WHERE status IN ('failed','interrupted')", default=0) or 0)
        checkpoints = int(self.db.scalar("SELECT COUNT(*) FROM checkpoints", default=0) or 0)
        bytes_total = 0
        checkpoint_root = self.layout.resolve("checkpoints")
        if checkpoint_root.exists():
            for path in checkpoint_root.rglob("*"):
                try:
                    if path.is_file():
                        bytes_total += path.stat().st_size
                except OSError:
                    continue
        return {
            "runs": total,
            "completed": completed,
            "failed": failed,
            "active": len(self.active()),
            "checkpoints": checkpoints,
            "artifact_bytes": bytes_total,
            "artifact_size": human_bytes(bytes_total),
        }

    def subscribe(self, callback: Callable[[TrainingRun], None]) -> Callable[[], None]:
        self._callbacks.append(callback)
        return lambda: self._callbacks.remove(callback) if callback in self._callbacks else None

    def shutdown(self) -> None:
        for run_id in list(self._processes):
            self.cancel(run_id)
        for thread in list(self._threads.values()):
            thread.join(timeout=3.0)
