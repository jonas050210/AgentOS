"""The training facade used by the GUI and the agent.

Responsibilities:

* report which methods can actually run here, and why not when they cannot;
* manage datasets (build from traces, import, export, delete) and runs (start, pause, resume,
  cancel, checkpoints, logs);
* evaluate a finished run against the active baseline and decide promotion from the numbers;
* expose the *active adapter* so the planner can consult it — this is what makes a promoted
  model change the agent's behaviour rather than sitting in a folder.
"""

from __future__ import annotations

import json
import random
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from codemind.config import Config
from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.storage.db import Database
from codemind.storage.layout import StorageLayout
from codemind.system.hardware import HardwareProfile
from codemind.training.collector import TaskTraceCollector
from codemind.training.datasets import DatasetBuilder, Example
from codemind.training.engine import TrainingEngine, TrainingRun
from codemind.training.eval import Evaluator, PromotionDecision, Scorecard
from codemind.training.methods import collect_env_report, evaluate_methods, method_info, resolve_auto_method
from codemind.training.nn import AdapterModel
from codemind.utils.files import atomic_write_text, ensure_dir, human_bytes, read_json

log = get_logger("training.manager")

ADAPTER_MODEL_ID = "codemind-adapter"
LORA_MODEL_ID = "codemind-lora"


@dataclass
class ModelVersion:
    id: int
    model_id: str
    version: str
    base_model: str
    path: str
    kind: str
    status: str
    created_at: float
    metrics: dict[str, Any]
    notes: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model_id": self.model_id,
            "version": self.version,
            "base_model": self.base_model,
            "path": self.path,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "metrics": self.metrics,
            "notes": self.notes,
        }


class TrainingManager:
    """Public API for training, evaluation and model versions."""

    def __init__(
        self,
        config: Config,
        db: Database,
        layout: StorageLayout,
        hardware: HardwareProfile,
        *,
        governor: Any = None,
        sampler: Any = None,
    ) -> None:
        self.config = config
        self.db = db
        self.layout = layout
        self.hardware = hardware
        self.datasets = DatasetBuilder(db, layout.paths("datasets"))
        self.collector = TaskTraceCollector(config, db, self.datasets)
        self.engine = TrainingEngine(
            config,
            db,
            layout,
            hardware,
            datasets=self.datasets,
            governor=governor,
            sampler=sampler,
            on_finished=self._on_run_finished,
        )
        self.evaluator = Evaluator(db, min_gain=config.training.promote_min_gain)
        self._advisor: AdapterModel | None = None
        self._advisor_path: Path | None = None
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self.load_active_adapter()

    # -- configuration ----------------------------------------------------
    def update_config(self, config: Config) -> None:
        self.config = config
        self.collector.update_config(config)
        self.engine.update_config(config)
        self.evaluator.min_gain = config.training.promote_min_gain

    def update_hardware(self, hardware: HardwareProfile) -> None:
        self.hardware = hardware
        self.engine.update_hardware(hardware)

    # -- methods & environment -------------------------------------------
    def methods(self) -> list[dict[str, Any]]:
        return [info.as_dict() for info in evaluate_methods(self.config, self.hardware)]

    def environment(self) -> dict[str, Any]:
        return {
            "toolchain": collect_env_report(),
            "hardware": self.hardware.as_dict(),
            "training_plan": self.hardware.training_plan(),
            "auto_method": resolve_auto_method(self.config, self.hardware),
            "curated_only": self.config.training.curated_only,
            "quality_threshold": self.config.training.quality_threshold,
        }

    # -- datasets ---------------------------------------------------------
    def install_seed_dataset(self, name: str = "planner_seed", *, merge: bool = True) -> dict[str, Any]:
        """Install the built-in curated starter set so a first training run is meaningful.

        These examples are hand-written for this application's tool inventory; they are not the
        evaluation benchmark, so measuring a candidate against the benchmark stays honest.
        """
        from codemind.training.seed import overlaps_benchmark, seed_examples

        leaked = overlaps_benchmark()
        if leaked:  # pragma: no cover - a guard against editing the seed carelessly
            raise RuntimeError(f"the starter dataset shares instructions with the evaluation benchmark: {sorted(leaked)}")
        examples = [
            Example(instruction=instruction, action={"tool": tool}, quality=0.95, source="seed", meta={"kind": "tool_selection"})
            for instruction, tool in seed_examples()
        ]
        result = self.datasets.from_examples(name, examples, notes=f"{len(examples)} curated starter examples", merge=merge)
        return result

    def reconcile_artifacts(self) -> list[str]:
        """Re-register promoted adapters found on disk that the database does not know about.

        The database is the source of truth for which version is active, but a storage folder can
        outlive it (a restored backup, a moved USB drive). Rather than silently ignoring a trained
        model that is sitting right there, CodeMind adopts it and says so in the log.
        """
        adopted: list[str] = []
        active_dir = self.layout.resolve("models") / "active"
        if not active_dir.exists():
            return adopted
        for model_dir in sorted(active_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            artefacts = sorted(model_dir.glob("*.json"))
            if not artefacts:
                continue
            model_id = model_dir.name
            if self.active_version(model_id) is not None:
                continue
            try:
                adapter = AdapterModel.load(artefacts[0])
            except Exception as exc:  # pragma: no cover
                log.warning("unreadable adapter artefact", cm={"path": str(artefacts[0]), "error": str(exc)})
                continue
            version = model_id if model_id != ADAPTER_MODEL_ID else f"{model_id}-{int(time.time())}"
            self.db.insert(
                "model_versions",
                {
                    "model_id": model_id,
                    "version": version,
                    "base_model": "built-in adapter (recovered from disk)",
                    "path": str(artefacts[0]),
                    "kind": "adapter_lite",
                    "status": "active",
                    "metrics": json.dumps({"labels": adapter.labels, "recovered": True}, default=str),
                    "notes": "recovered from models/active at startup",
                    "created_at": time.time(),
                },
            )
            adopted.append(str(artefacts[0]))
            log.info("recovered promoted adapter", cm={"path": str(artefacts[0]), "labels": len(adapter.labels)})
        if adopted:
            self.load_active_adapter(force=True)
        return adopted

    def dataset_stats(self, limit: int = 50) -> dict[str, Any]:
        stats = self.datasets.stats()
        stats["collector"] = self.collector.stats()
        stats["recent_examples"] = self.collector.recent_examples(10)
        stats["items"] = stats["items"][:limit]
        return stats

    def import_dataset(self, name: str, paths: Sequence[str], *, merge: bool = True) -> dict[str, Any]:
        """Copy external JSONL/JSON/CSV files into a managed dataset.

        Merging is the default: importing into a dataset that already has content adds to it
        rather than replacing it, and a source that is already part of the dataset is ignored.
        """
        if not name:
            raise ValueError("a dataset name is required")
        result = self.datasets.from_files(name, paths, merge=merge)
        BUS.publish(Topics.TRAINING_PROGRESS, run_id="", event="dataset.imported", dataset=name, examples=result.get("examples", 0))
        return result

    def export_dataset(self, name: str, target: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT path FROM datasets WHERE name = ?", (name,))
        if not row:
            raise ValueError(f"unknown dataset {name!r}")
        source = Path(row["path"])
        target_path = Path(target).expanduser()
        ensure_dir(target_path.parent)
        shutil.copy2(source, target_path)
        return {"name": name, "target": str(target_path), "bytes": target_path.stat().st_size}

    def delete_dataset(self, name: str) -> bool:
        return self.datasets.delete(name)

    def collect_from_tasks(self, limit: int = 200) -> dict[str, Any]:
        """Re-collect traces from stored, verified tasks (useful after importing old history)."""
        accepted = rejected = 0
        rows = self.db.query(
            "SELECT id FROM tasks WHERE status = 'completed' ORDER BY finished_at DESC LIMIT ?",
            (limit,),
        )
        from codemind.agent.task import TaskStore

        store = TaskStore(self.db, recovery=False)
        for row in rows:
            task = store.get(row["id"])
            if task is None:
                continue
            outcome = type("Outcome", (), {"status": task.status, "verification": task.verify, "tool_calls": task.tool_calls})()
            report = self.collector.collect(task, outcome)
            accepted += report.accepted
            rejected += report.rejected
        return {"accepted": accepted, "rejected": rejected, "tasks": len(rows), "dataset": self.collector.stats()}

    # -- runs -------------------------------------------------------------
    def start_run(
        self,
        *,
        name: str = "",
        method: str = "auto",
        dataset: str = "",
        base_model: str = "",
        overrides: dict[str, Any] | None = None,
        resume_from: str = "",
        origin: str = "user",
    ) -> dict[str, Any]:
        run = self.engine.start(
            name=name, method=method, dataset=dataset, base_model=base_model,
            config_overrides=overrides, resume_from=resume_from, origin=origin,
        )
        return run.as_dict()

    def runs(self, limit: int = 40) -> list[dict[str, Any]]:
        return [run.as_dict() for run in self.engine.list(limit)]

    def run_detail(self, run_id: str) -> dict[str, Any]:
        run = self.engine.get(run_id)
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        payload = run.as_dict()
        payload["checkpoints"] = self.engine.checkpoints(run_id)
        payload["events"] = self.engine.events(run_id, limit=200)
        payload["log_tail"] = self.engine.log_tail(run_id, 80)
        payload["evaluations"] = self.evaluations(run_id=run_id)
        return payload

    def cancel_run(self, run_id: str, pause: bool = False) -> bool:
        return self.engine.cancel(run_id, pause=pause)

    def resume_run(self, run_id: str) -> dict[str, Any]:
        return self.engine.resume(run_id).as_dict()

    def run_stats(self) -> dict[str, Any]:
        stats = self.engine.stats()
        stats["active_runs"] = [run.as_dict() for run in self.engine.active()]
        return stats

    # -- evaluation & promotion -------------------------------------------
    def evaluate_run(self, run_id: str, suite: str = "") -> dict[str, Any]:
        run = self.engine.get(run_id)
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        suite = suite or str(run.config.get("eval_suite") or self.config.training.eval_suite)
        if suite not in ("all", "coding_core", "tool_use", "regression", "instruction_following"):
            suite = "coding_core"
        adapter, version = self._adapter_for_run(run)
        regression_examples = self._regression_examples()
        baseline_card = self.baseline_scorecard(suite)
        candidate_card = self.evaluator.evaluate(
            adapter=adapter,
            version=run_id,
            suite=suite,
            regression_examples=regression_examples,
        )
        # LoRA runs also carry the held-out loss reported by the worker
        if run.method in ("lora", "qlora") and run.metrics.get("eval"):
            candidate_card.metrics["held_out_loss"] = run.metrics["eval"]
        decision = self.evaluator.compare(baseline_card, candidate_card, min_gain=self.config.training.promote_min_gain)
        if run.method in ("lora", "qlora"):
            decision = self._adjust_for_held_out_loss(run, baseline_card, candidate_card, decision)
        self.db.insert(
            "evaluations",
            {
                "run_id": run_id,
                "model_version": run_id,
                "suite": candidate_card.suite,
                "created_at": time.time(),
                "score": candidate_card.score,
                "baseline_score": baseline_card.score if baseline_card else None,
                "decision": "promote" if decision.promote else "keep",
                "reason": decision.reason,
                "promoted": 0,
                "results": json.dumps({"candidate": candidate_card.as_dict(), "baseline": baseline_card.as_dict() if baseline_card else None, "decision": decision.as_dict()}, default=str)[:200000],
            },
        )
        BUS.publish(Topics.TRAINING_EVAL, run_id=run_id, suite=suite, score=candidate_card.score, decision=decision.as_dict())
        return {
            "run_id": run_id,
            "suite": candidate_card.suite,
            "candidate": candidate_card.as_dict(),
            "baseline": baseline_card.as_dict() if baseline_card else None,
            "decision": decision.as_dict(),
            "adapter_version": version,
        }

    def _adjust_for_held_out_loss(self, run: TrainingRun, baseline: Scorecard | None, candidate: Scorecard, decision: PromotionDecision) -> PromotionDecision:
        eval_metrics = run.metrics.get("eval") or {}
        perplexity = eval_metrics.get("perplexity")
        if perplexity is None:
            return PromotionDecision(False, "The run has no held-out loss recorded, so it cannot be promoted on evidence.", candidate.score, baseline.score if baseline else 0.0, decision.regressions)
        if perplexity > 12:
            return PromotionDecision(False, f"Held-out perplexity {perplexity:.2f} is too high to claim an improvement.", candidate.score, baseline.score if baseline else 0.0, decision.regressions)
        return decision

    def baseline_scorecard(self, suite: str) -> Scorecard | None:
        """The scorecard of the active version, or the untrained baseline."""
        active = self.active_version()
        if active is None:
            row = self.db.query_one(
                "SELECT id FROM evaluations WHERE suite = ? AND model_version LIKE 'baseline%' ORDER BY created_at DESC LIMIT 1",
                (suite,),
            )
            if row:
                stored = self.db.query_one("SELECT results FROM evaluations WHERE id = ?", (row["id"],))
                if stored:
                    payload = json.loads(stored["results"] or "{}")
                    if "candidate" in payload:
                        card = payload["candidate"]
                        return Scorecard(card.get("suite", suite), card.get("score", 0.0), card.get("metrics", {}), card.get("per_item", []), version="baseline")
            card = self.evaluator.evaluate(adapter=None, version="baseline", suite=suite, regression_examples=self._regression_examples())
            self.db.insert(
                "evaluations",
                {
                    "run_id": None,
                    "model_version": "baseline",
                    "suite": card.suite,
                    "created_at": card.created_at,
                    "score": card.score,
                    "results": json.dumps({"candidate": card.as_dict()}, default=str)[:200000],
                },
            )
            return card
        adapter, version = self._load_adapter(active)
        return self.evaluator.evaluate(adapter=adapter, version=version, suite=suite, regression_examples=self._regression_examples())

    def _regression_examples(self) -> list[tuple[str, str]]:
        """Examples the current active version already handles: the regression suite."""
        rows = self.db.query("SELECT results FROM evaluations ORDER BY created_at DESC LIMIT 4")
        examples: list[tuple[str, str]] = []
        for row in rows:
            payload = json.loads(row["results"] or "{}")
            card = payload.get("candidate") or {}
            for item in card.get("per_item", []):
                if item.get("ok") and item.get("instruction") and item.get("expected"):
                    examples.append((item["instruction"], item["expected"]))
        return examples[:80]

    def promote(self, run_id: str, *, note: str = "") -> dict[str, Any]:
        run = self.engine.get(run_id)
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        artifact = (run.artifacts or {}).get("adapter") or (run.artifacts or {}).get("index")
        if not artifact or not Path(artifact).exists():
            raise ValueError("this run has no usable artefact to promote")
        model_id = ADAPTER_MODEL_ID if run.method in ("adapter_lite", "retrieval_index") else LORA_MODEL_ID
        # demote the previous active version
        self.db.execute("UPDATE model_versions SET status = 'superseded' WHERE model_id = ? AND status = 'active'", (model_id,))
        version_dir = self.layout.resolve("models") / "active" / model_id
        ensure_dir(version_dir)
        target = version_dir / Path(artifact).name
        if Path(artifact).is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(artifact, target)
        else:
            shutil.copy2(artifact, target)
        existing = self.db.query_one("SELECT id FROM model_versions WHERE model_id = ? AND version = ?", (model_id, run_id))
        payload = {
            "model_id": model_id,
            "version": run_id,
            "base_model": run.base_model,
            "path": str(target),
            "kind": run.method,
            "status": "active",
            "created_at": time.time(),
            "metrics": json.dumps(run.metrics, default=str)[:20000],
            "notes": note or f"Promoted from run {run.name}",
        }
        if existing:
            self.db.update("model_versions", payload, "id = ?", (existing["id"],))
        else:
            self.db.insert("model_versions", payload)
        self.db.execute("UPDATE evaluations SET promoted = 1 WHERE model_version = ?", (run_id,))
        self.load_active_adapter(force=True)
        self._emit("promoted", {"run_id": run_id, "model_id": model_id, "path": str(target)})
        log.info("model version promoted", cm={"run": run_id, "model": model_id, "path": str(target)})
        return {"model_id": model_id, "version": run_id, "path": str(target), "status": "active"}

    def reject(self, run_id: str, reason: str = "") -> dict[str, Any]:
        run = self.engine.get(run_id)
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        model_id = ADAPTER_MODEL_ID if run.method in ("adapter_lite", "retrieval_index") else LORA_MODEL_ID
        existing = self.db.query_one("SELECT id FROM model_versions WHERE model_id = ? AND version = ?", (model_id, run_id))
        payload = {
            "model_id": model_id,
            "version": run_id,
            "base_model": run.base_model,
            "path": (run.artifacts or {}).get("adapter", ""),
            "kind": run.method,
            "status": "rejected",
            "created_at": time.time(),
            "metrics": json.dumps(run.metrics, default=str)[:20000],
            "notes": reason or "Rejected by the user",
        }
        if existing:
            self.db.update("model_versions", payload, "id = ?", (existing["id"],))
        else:
            self.db.insert("model_versions", payload)
        self._emit("rejected", {"run_id": run_id, "model_id": model_id, "reason": reason})
        return {"model_id": model_id, "version": run_id, "status": "rejected"}

    # -- versions ---------------------------------------------------------
    def versions(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM model_versions ORDER BY created_at DESC LIMIT 100")
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                ModelVersion(
                    id=row["id"],
                    model_id=row["model_id"],
                    version=row["version"],
                    base_model=row["base_model"] or "",
                    path=row["path"] or "",
                    kind=row["kind"] or "",
                    status=row["status"],
                    created_at=row["created_at"],
                    metrics=json.loads(row["metrics"] or "{}"),
                    notes=row["notes"] or "",
                ).as_dict()
            )
        return out

    def active_version(self, model_id: str = ADAPTER_MODEL_ID) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM model_versions WHERE model_id = ? AND status = 'active'", (model_id,))
        if row is None:
            return None
        return ModelVersion(
            id=row["id"], model_id=row["model_id"], version=row["version"], base_model=row["base_model"] or "",
            path=row["path"] or "", kind=row["kind"] or "", status=row["status"], created_at=row["created_at"],
            metrics=json.loads(row["metrics"] or "{}"), notes=row["notes"] or "",
        ).as_dict()

    def evaluations(self, run_id: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
        if run_id:
            rows = self.db.query("SELECT * FROM evaluations WHERE run_id = ? ORDER BY created_at DESC LIMIT ?", (run_id, limit))
        else:
            rows = self.db.query("SELECT * FROM evaluations ORDER BY created_at DESC LIMIT ?", (limit,))
        out = []
        for row in rows:
            payload = json.loads(row["results"] or "{}")
            out.append(
                {
                    "id": row["id"],
                    "run_id": row["run_id"],
                    "model_version": row["model_version"],
                    "suite": row["suite"],
                    "created_at": row["created_at"],
                    "score": row["score"],
                    "baseline_score": row["baseline_score"],
                    "decision": row["decision"],
                    "reason": row["reason"],
                    "promoted": bool(row["promoted"]),
                    "candidate": (payload.get("candidate") or {}),
                    "baseline": payload.get("baseline"),
                }
            )
        return out

    def comparison(self, run_id: str) -> dict[str, Any] | None:
        rows = self.evaluations(run_id=run_id, limit=1)
        if not rows:
            return None
        entry = rows[0]
        baseline = entry.get("baseline") or {}
        candidate = entry.get("candidate") or {}
        if not baseline and entry.get("baseline_score") is not None:
            baseline = {"suite": entry["suite"], "score": entry["baseline_score"]}
        metrics: list[dict[str, Any]] = []
        base_components = (baseline.get("metrics") or {}).get("components") or {baseline.get("suite", "score"): baseline.get("score", 0.0)}
        cand_components = (candidate.get("metrics") or {}).get("components") or {candidate.get("suite", "score"): candidate.get("score", 0.0)}
        for name in sorted(set(base_components) | set(cand_components)):
            before = float(base_components.get(name, 0.0) or 0.0)
            after = float(cand_components.get(name, 0.0) or 0.0)
            metrics.append({"metric": name, "baseline": round(before, 4), "candidate": round(after, 4), "delta": round(after - before, 4)})
        return {
            "run_id": run_id,
            "suite": entry["suite"],
            "baseline_score": entry.get("baseline_score"),
            "candidate_score": entry.get("score"),
            "decision": entry.get("decision"),
            "reason": entry.get("reason"),
            "metrics": metrics,
            "promoted": entry.get("promoted"),
        }

    # -- advisor ----------------------------------------------------------
    def load_active_adapter(self, force: bool = False) -> AdapterModel | None:
        active = self.active_version()
        if active is None:
            self._advisor = None
            self._advisor_path = None
            return None
        path = Path(active["path"])
        if path.is_dir():
            candidates = sorted(path.glob("*.json"))
            path = candidates[0] if candidates else path
        if not path.exists():
            self._advisor = None
            return None
        if not force and self._advisor is not None and self._advisor_path == path:
            return self._advisor
        try:
            self._advisor = AdapterModel.load(path)
            self._advisor_path = path
            log.info("active adapter loaded", cm={"path": str(path), "labels": len(self._advisor.labels)})
        except Exception as exc:
            log.warning("could not load the active adapter", cm={"path": str(path), "error": str(exc)})
            self._advisor = None
        return self._advisor

    @property
    def advisor(self) -> AdapterModel | None:
        if self._advisor is None:
            self.load_active_adapter()
        return self._advisor

    @property
    def seed_dataset_name(self) -> str:
        return "planner_seed"

    def suggest_tool(self, instruction: str, candidates: Sequence[str] = ()) -> tuple[str, float] | None:
        """Ask the promoted adapter which tool fits an instruction (if it knows one)."""
        adapter = self.advisor
        if adapter is None:
            return None
        tool, confidence, _probs = adapter.predict(instruction)
        if not tool:
            return None
        if candidates and tool not in candidates:
            # fall back to the best known label that is actually available
            probs = dict(zip(adapter.labels, _probs))
            ranked = sorted((label for label in candidates if label in probs), key=lambda label: -probs[label])
            if not ranked:
                return None
            return ranked[0], float(probs[ranked[0]])
        return tool, float(confidence)

    # -- housekeeping -----------------------------------------------------
    def _adapter_for_run(self, run: TrainingRun) -> tuple[AdapterModel | None, str]:
        artifact = (run.artifacts or {}).get("adapter")
        if artifact and Path(artifact).exists():
            if Path(artifact).is_dir():
                candidates = sorted(Path(artifact).glob("*.json"))
                if candidates:
                    try:
                        return AdapterModel.load(candidates[0]), run.id
                    except Exception:
                        return None, run.id
                return None, run.id
            try:
                return AdapterModel.load(Path(artifact)), run.id
            except Exception:
                return None, run.id
        return None, run.id

    def _load_adapter(self, version: dict[str, Any]) -> tuple[AdapterModel | None, str]:
        path = Path(version.get("path", ""))
        if path.is_dir():
            candidates = sorted(path.glob("*.json"))
            path = candidates[0] if candidates else path
        try:
            return AdapterModel.load(path), version.get("version", "")
        except Exception:
            return None, version.get("version", "")

    def _on_run_finished(self, run: TrainingRun) -> None:
        """After a run completes: evaluate it, then auto-promote when configured and justified."""
        if run is None or run.status != "completed":
            self._emit("run-finished", {"run_id": run.id if run else "", "status": run.status if run else "unknown"})
            return
        try:
            evaluation = self.evaluate_run(run.id)
        except Exception as exc:  # pragma: no cover
            log.warning("automatic evaluation failed", cm={"run": run.id, "error": str(exc)})
            self._emit("run-finished", {"run_id": run.id, "status": run.status, "evaluation_error": str(exc)})
            return
        decision = evaluation["decision"]
        payload = {"run_id": run.id, "status": run.status, "evaluation": evaluation}
        if self.config.training.auto_promote and decision.get("promote"):
            try:
                self.promote(run.id, note=f"Auto-promoted: {decision.get('reason')}")
                payload["promoted"] = True
            except Exception as exc:  # pragma: no cover
                payload["promotion_error"] = str(exc)
        self._emit("run-finished", payload)

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, payload)
            except Exception:  # pragma: no cover
                pass
        BUS.publish(Topics.TRAINING_FINISHED, kind=kind, **payload)

    def subscribe(self, listener: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def overview(self) -> dict[str, Any]:
        return {
            "methods": self.methods(),
            "runs": self.run_stats(),
            "datasets": self.dataset_stats(),
            "versions": self.versions(),
            "active_adapter": (self.active_version() or {}),
            "advisor_loaded": self.advisor is not None,
            "environment": self.environment(),
        }

    def shutdown(self) -> None:
        self.engine.shutdown()
