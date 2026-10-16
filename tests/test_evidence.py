"""Evidence, honesty and rollback: what CodeMind proves about its own work.

Three promises are checked here:

* **the honesty number** — ``app.quality_report()`` counts completed tasks *with* passing evidence
  and names the ones without, instead of quietly rounding them away;
* **the change passport** — a hash-chained record of one task that can be exported, re-read and
  checked for later edits;
* **the rollback** — the pre-images every write keeps are turned back into files, with refusals
  instead of surprises.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.support import TempDataCase
from tests.test_agent import tool_context

from codemind.agent.rollback import RollbackRefused, apply_rollback, plan_rollback
from codemind.report.passport import build_passport, chain_hashes, render_markdown, verify_chain, write_passport
from codemind.report.quality import honesty_report, unverified_tasks


class RollbackTests(TempDataCase):
    """Every write keeps its pre-image; a rollback puts the files back and says what it did."""

    def _task_with_edit(self, app):
        """Create a task, edit one existing file and create one new file through the real tools."""
        task = app.tasks.create("Add a helper and document it", project_path=str(self.project_root), mode="assisted")
        context = tool_context(app, task.id)
        target = context.workspace.path / "src" / "calculator.py"
        original = target.read_text(encoding="utf-8")
        edited = original + "\n\ndef subtract(a, b):\n    '''Return a - b.'''\n    return a - b\n"
        result = app.registry.execute("write_file", {"path": str(target), "content": edited}, context)
        self.assertTrue(result.ok, result.error or result.output)
        created = context.workspace.path / "docs" / "note.md"
        created.parent.mkdir(parents=True, exist_ok=True)
        result = app.registry.execute("write_file", {"path": str(created), "content": "# note\n"}, context)
        self.assertTrue(result.ok, result.error or result.output)
        # the task is over (it changed files and then stopped) — only now may it be rolled back
        app.tasks.set_status(task.id, "failed", error="the task was interrupted before verifying")
        return task, context.workspace.path, target, original, created

    def test_writes_record_a_pre_image_and_can_be_rolled_back(self) -> None:
        app = self.make_app()
        task, workspace, target, original, created = self._task_with_edit(app)

        self.assertEqual(target.read_text(encoding="utf-8").count("def subtract"), 1)
        self.assertTrue(created.exists())
        diffs = app.tasks.diffs(task.id)
        self.assertEqual(len(diffs), 2, "both writes have to be recorded as diffs")
        self.assertIn("src/calculator.py", diffs[0]["path"])
        self.assertGreater(diffs[0]["added"], 0)

        plan = plan_rollback(app, task.id)
        self.assertEqual(plan["count"], 2)
        self.assertEqual(plan["restores"], 1, "the edited file already existed")
        self.assertEqual(plan["deletes"], 1, "the created file did not exist before")

        report = apply_rollback(app, task.id)
        self.assertEqual(report["count"], 2)
        self.assertEqual(report["skipped"], [])
        self.assertEqual(target.read_text(encoding="utf-8"), original, "the file must be byte-identical again")
        self.assertFalse(created.exists(), "a file that did not exist before must be removed again")
        kinds = [event["kind"] for event in app.tasks.events(task.id)]
        self.assertIn("rollback", kinds, "the rollback must show up in the task history")

    def test_app_level_rollback_and_plan_agree(self) -> None:
        app = self.make_app()
        task, _workspace, target, original, _created = self._task_with_edit(app)
        plan = app.rollback_plan(task.id)
        self.assertEqual(plan["task_id"], task.id)
        result = app.rollback_task(task.id)
        self.assertEqual(result["count"], plan["count"])
        self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_rollback_refuses_while_a_task_is_running(self) -> None:
        app = self.make_app()
        task, _workspace, _target, _original, _created = self._task_with_edit(app)
        app.tasks.set_status(task.id, "running")
        with self.assertRaises(RollbackRefused) as caught:
            apply_rollback(app, task.id)
        self.assertIn("still running", str(caught.exception))
        with self.assertRaises(RollbackRefused):
            plan_rollback(app, "no-such-task")

    def test_rollback_of_a_read_only_task_is_refused_with_a_reason(self) -> None:
        app = self.make_app()
        task = app.tasks.create("Just look at the repository", project_path=str(self.project_root), mode="read_only")
        app.tasks.set_status(task.id, "completed", summary="nothing changed")
        with self.assertRaises(RollbackRefused) as caught:
            apply_rollback(app, task.id)
        self.assertIn("never modified a file", str(caught.exception))


class PassportTests(TempDataCase):
    """The change passport: complete, ordered, hash-chained and re-checkable."""

    def _finished_task(self, app):
        submitted = app.submit_task(
            "Analyse this repository and report its structure", project_path=self.project_root, mode="read_only"
        )
        task = self.wait_for_task(submitted.id, timeout=300)
        self.assertIsNotNone(task)
        self.assertTrue(task.is_terminal, f"the task did not finish: {task.status}")
        return task

    def test_passport_contains_the_evidence_and_verifies(self) -> None:
        app = self.make_app(background=True)
        task = self._finished_task(app)
        passport = build_passport(app, task.id)

        self.assertEqual(passport["task_id"], task.id)
        self.assertTrue(passport["prompt"])
        self.assertTrue(passport["steps"], "a task that ran must list its steps")
        self.assertTrue(passport["entries"], "the chain must have entries")
        self.assertTrue(passport["models"]["name"], "the passport has to name the backend that planned it")
        self.assertTrue(passport["checks"], "the verification checks belong in the passport")
        self.assertIn("duration", task.as_dict())

        ok, note = verify_chain(passport)
        self.assertTrue(ok, note)
        self.assertEqual(passport["hashes"][-1], passport["root"])
        self.assertEqual(len(passport["hashes"]), len(passport["entries"]))
        self.assertEqual(chain_hashes([]), [])

    def test_a_tampered_passport_is_detected(self) -> None:
        app = self.make_app(background=True)
        task = self._finished_task(app)
        passport = build_passport(app, task.id)

        passport["entries"][0]["title"] = "a nicer story"
        ok, note = verify_chain(passport)
        self.assertFalse(ok, "a modified passport must be reported")
        self.assertIn("modified", note)

        passport["hashes"] = passport["hashes"][:-1]
        ok, note = verify_chain(passport)
        self.assertFalse(ok)

    def test_passport_is_written_as_json_and_markdown(self) -> None:
        app = self.make_app(background=True)
        task = self._finished_task(app)
        result = write_passport(app, task.id)
        self.assertTrue(result["chain_ok"], result)
        for fmt in ("json", "md"):
            path = Path(result["files"][fmt])
            self.assertTrue(path.exists(), f"{fmt} passport missing")
            self.assertGreater(path.stat().st_size, 200)
        payload = json.loads(Path(result["files"]["json"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["task_id"], task.id)
        ok, _note = verify_chain(payload)
        self.assertTrue(ok, "the exported JSON must verify")
        text = Path(result["files"]["md"]).read_text(encoding="utf-8")
        self.assertIn("# Change passport", text)
        self.assertIn(task.id, text)
        self.assertIn("## Verification", text)
        self.assertIn("## Integrity", text)
        self.assertIn(payload["root"], text)
        self.assertTrue(Path(result["latest"]).exists(), "the latest copy makes the file easy to find")
        # the app-level helper and the module function must produce the same thing
        again = app.write_passport(task.id)
        self.assertTrue(again["chain_ok"])
        self.assertEqual(render_markdown(payload).count("# Change passport"), 1)

    def test_passport_of_an_unknown_task_is_an_error(self) -> None:
        app = self.make_app()
        with self.assertRaises(ValueError):
            build_passport(app, "does-not-exist")


class HonestyReportTests(TempDataCase):
    """The number that keeps the project honest: proven work vs. claimed work."""

    def test_verified_work_counts_and_claims_are_named(self) -> None:
        app = self.make_app(background=True)
        submitted = app.submit_task(
            "Analyse this repository and report its structure", project_path=self.project_root, mode="read_only"
        )
        task = self.wait_for_task(submitted.id, timeout=300)
        self.assertEqual(task.status, "completed")
        report = honesty_report(app)
        self.assertGreaterEqual(report["completed"], 1)
        self.assertEqual(report["unverified"], 0, f"a completed task must carry evidence: {report}")
        self.assertEqual(report["verified_rate"], 1.0)
        self.assertEqual(unverified_tasks(app), [])

        # a completed task without passing checks is exactly the case the report has to surface
        app.tasks.update(task.id, verify={"verified": False, "checks": [{"name": "tests", "ok": False, "detail": "boom"}]})
        report = honesty_report(app)
        self.assertEqual(report["unverified"], 1)
        self.assertLess(report["verified_rate"], 1.0)
        claims = unverified_tasks(app)
        self.assertEqual(claims[0]["task_id"], task.id)
        self.assertEqual(claims[0]["checks"][0]["name"], "tests")


class SnapshotExposureTests(TempDataCase):
    """The shell polls one snapshot: it has to carry the honesty number and the resource state."""

    def test_snapshot_exposes_quality_resources_and_runner_state(self) -> None:
        app = self.make_app()
        snapshot = app.overview_snapshot()
        self.assertIn("quality", snapshot)
        self.assertIn("verified_rate", snapshot["quality"])
        self.assertIn("unverified_tasks", snapshot["quality"])
        self.assertIn("cpu_percent", snapshot["resources"])
        self.assertIn("ram_used_mb", snapshot["resources"])
        self.assertIn("active", snapshot["agent"]["runner"])
        self.assertIn("active_count", snapshot["agent"]["runner"])
        self.assertIn("queued", snapshot["agent"]["runner"])
        self.assertTrue(snapshot["app"]["data_root"])
        self.assertIn("hardware", snapshot)
        self.assertIn("governor", snapshot)


if __name__ == "__main__":
    unittest.main()
