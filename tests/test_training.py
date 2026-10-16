"""The training and evaluation pipeline, exercised for real.

There is no torch on this machine, so the expensive methods (LoRA/QLoRA) must report that they are
unavailable with the exact pip command needed. The two methods that *do* work everywhere — the
lightweight adapter and the retrieval index — are trained, evaluated against the benchmark and
promoted here, because a training pipeline that is never measured is worthless.
"""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from tests.support import TempDataCase


class DatasetTests(TempDataCase):
    def setUp(self) -> None:
        self.reset_storage()

    def test_seed_dataset_is_curated_and_does_not_leak_the_benchmark(self) -> None:
        from codemind.training.seed import overlaps_benchmark, seed_examples

        app = self.make_app()
        self.assertEqual(overlaps_benchmark(), set(), "the starter set must not share instructions with the benchmark")
        examples = list(seed_examples())
        self.assertGreaterEqual(len(examples), 100, "the starter set must be big enough to teach tool selection")
        names = set(app.registry.names())
        unknown = {tool for _, tool in examples if tool not in names}
        self.assertEqual(unknown, set(), f"the starter set mentions tools that do not exist: {sorted(unknown)}")

        result = app.training.install_seed_dataset()
        self.assertGreaterEqual(result.get("examples", 0), 100)
        stats = app.training.dataset_stats()
        names_in_stats = {item["name"] for item in stats["items"]}
        self.assertIn("planner_seed", names_in_stats)
        self.assertGreater(stats["examples"], 100, "the installed examples must be counted")
        seed_row = next(item for item in stats["items"] if item["name"] == "planner_seed")
        self.assertTrue(seed_row["exists"], "the dataset file must really be on disk")
        self.assertEqual(seed_row["rejected"], 0, "curated examples must all pass validation")

    def test_import_export_delete_roundtrip(self) -> None:
        app = self.make_app()
        source = self.data_root.parent / "external.jsonl"
        rows = [
            {"instruction": "Write a changelog entry for this release", "tool": "write_file"},
            {"instruction": "Show me what changed since yesterday", "tool": "git_diff"},
        ]
        source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        imported = app.training.import_dataset("external", [str(source)])
        self.assertEqual(imported["report"]["valid"], 2, "both hand-written examples must validate")
        self.assertEqual(imported["report"]["rejected"], 0)
        self.assertEqual(
            imported["examples"] + imported["validation_examples"],
            2,
            "every imported example must be used for training or for validation",
        )
        exported = self.data_root.parent / "exported.jsonl"
        app.training.export_dataset("external", str(exported))
        self.assertTrue(exported.exists())
        self.assertEqual(len(exported.read_text(encoding="utf-8").strip().splitlines()), 1)
        self.assertTrue(app.training.delete_dataset("external"))
        self.assertFalse(any(item["name"] == "external" for item in app.training.dataset_stats()["items"]))

    def test_collection_from_tasks_needs_successful_verified_work(self) -> None:
        """Memory is not training data: only verified successes may become examples."""
        app = self.make_app(background=True)
        collected = app.training.collect_from_tasks(limit=50)
        self.assertEqual(collected.get("added", 0), 0, "an empty history must not invent training examples")
        task = app.submit_task("Where is the calculator implemented?", project_path=str(self.project_root))
        finished = self.wait_for_task(task.id, timeout=180)
        self.assertTrue(finished.is_terminal)
        after = app.training.collect_from_tasks(limit=50)
        # a successful, verified task may contribute; a failed one never may
        if after.get("added"):
            self.assertEqual(finished.status, "completed", "only a completed task may produce examples")
            self.assertTrue((finished.verify or {}).get("verified"))


class TrainingRunTests(TempDataCase):
    """A real run: train, evaluate, promote. Everything here is measured, nothing is simulated."""

    def setUp(self) -> None:
        self.reset_storage()

    def wait_for_run(self, run_id: str, timeout: float = 300.0) -> dict:
        deadline = time.time() + timeout
        detail = self.app.training.run_detail(run_id)
        while time.time() < deadline:
            detail = self.app.training.run_detail(run_id)
            if detail["status"] in ("completed", "failed", "cancelled"):
                return detail
            time.sleep(0.4)
        return detail

    def test_methods_report_availability_honestly(self) -> None:
        app = self.make_app()
        methods = {item["name"]: item for item in app.training.methods()}
        self.assertIn("adapter_lite", methods)
        self.assertTrue(methods["adapter_lite"]["available"], "the lightweight adapter works without any framework")
        for method in ("lora", "qlora"):
            self.assertIn(method, methods, "both heavyweight methods must be reported, available or not")
            if not methods[method]["available"]:
                requirements = " ".join(methods[method].get("requirements") or [])
                self.assertTrue(requirements, f"{method} must say what is missing (e.g. the pip command)")

    def test_adapter_trains_improves_and_is_promoted(self) -> None:
        app = self.make_app(background=True)
        app.training.install_seed_dataset()
        run = app.training.start_run(name="unit run", method="adapter_lite", dataset="planner_seed")
        run_id = run["id"]
        detail = self.wait_for_run(run_id)
        self.assertEqual(detail["status"], "completed", detail.get("error") or detail.get("summary"))
        metrics = detail["metrics"]
        self.assertGreater(metrics["examples"], 50)
        self.assertGreater(metrics["validation_examples"], 0, "a run without a validation split cannot be trusted")
        self.assertIsNotNone(metrics["final_loss"])
        self.assertTrue(detail["artifacts"].get("adapter"), "a completed run must leave an artefact on disk")
        self.assertTrue(Path(detail["artifacts"]["adapter"]).exists())
        self.assertTrue(detail["events"], "a run must record what it did")

        evaluation = app.training.evaluate_run(run_id, suite="tool_use")
        candidate = evaluation["candidate"]["score"]
        baseline = evaluation["baseline"]["score"]
        self.assertEqual(evaluation["candidate"]["metrics"]["items"], 21)
        self.assertGreater(candidate, baseline, "the trained adapter must beat the untrained baseline")
        self.assertTrue(evaluation["decision"]["promote"], evaluation["decision"]["reason"])
        self.assertNotEqual(evaluation["decision"]["candidate_score"], None)

        promoted = app.training.promote(run_id, note="unit test")
        self.assertEqual(promoted["status"], "active")
        active = app.training.active_version()
        self.assertIsNotNone(active)
        self.assertEqual(active["version"], run_id)
        self.assertTrue(app.training.advisor, "the promoted adapter must be loaded for the planner")
        suggestion = app.training.suggest_tool("Where is the database schema defined?")
        self.assertIsNotNone(suggestion, "an active adapter must be able to answer tool questions")
        self.assertIn(suggestion[0], set(app.registry.names()))

    def test_promoted_model_survives_a_restart(self) -> None:
        app = self.make_app(background=True)
        app.training.install_seed_dataset()
        run_id = app.training.start_run(name="persist run", method="adapter_lite", dataset="planner_seed")["id"]
        self.assertEqual(self.wait_for_run(run_id)["status"], "completed")
        app.training.evaluate_run(run_id, suite="tool_use")
        app.training.promote(run_id, note="persistence")
        app.shutdown()

        self.__class__.app = None
        second = self.make_app(background=True)
        active = second.training.active_version()
        self.assertIsNotNone(active, "the promoted adapter must still be active after a restart")
        self.assertEqual(active["version"], run_id)
        adopted = second.training.reconcile_artifacts()
        self.assertEqual(adopted, [], "nothing should need re-adopting when the database is intact")
        runs = {item["id"] for item in second.training.runs()}
        self.assertIn(run_id, runs)
        evaluations = second.training.evaluations(run_id=run_id)
        self.assertTrue(evaluations, "evaluation results must be stored, not just printed")

    def test_rejecting_a_run_leaves_the_baseline_in_place(self) -> None:
        app = self.make_app(background=True)
        app.training.install_seed_dataset()
        run_id = app.training.start_run(name="reject me", method="adapter_lite", dataset="planner_seed")["id"]
        self.assertEqual(self.wait_for_run(run_id)["status"], "completed")
        result = app.training.reject(run_id, reason="unit test")
        self.assertEqual(result["status"], "rejected")
        active = app.training.active_version() or {}
        self.assertNotEqual(active.get("version"), run_id, "a rejected run must never become active")


if __name__ == "__main__":
    unittest.main()
