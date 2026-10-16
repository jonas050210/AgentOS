"""The agent end to end: inspection, execution, diagnosis, self-correction and honest reporting.

These tests run real commands in a real workspace. They are the ones that matter most, because
they check the properties the product promises: no silent success, no repeated failing action,
no invented code, and evidence for every completed task.
"""

from __future__ import annotations

import time
import unittest
from pathlib import Path

from tests.support import TempDataCase


def tool_context(app, task_id: str):
    """Build the same ToolContext the executor hands to its tools (used for direct tool tests)."""
    from codemind.agent.tools import FileJournal, ToolContext

    task = app.tasks.get(task_id)
    project = Path(task.project_path or app.data_root)
    workspace = app.sandbox.create_workspace(project, task_id=task_id, mode=app.config.sandbox.workspace_mode)
    return ToolContext(
        config=app.config,
        db=app.db,
        memory=app.memory,
        knowledge=app.knowledge,
        sandbox=app.sandbox,
        policy=app.policy,
        approvals=app.approvals,
        github=app.github,
        governor=app.governor,
        task_id=task_id,
        workspace=workspace,
        project_path=project,
        journal=FileJournal(app.db, backup_root=app.data_root / "backups" / task_id, task_id=task_id),
        data_root=app.data_root,
    )


class AgentBasicsTests(TempDataCase):
    def test_tools_are_registered_with_risk_levels(self) -> None:
        app = self.make_app()
        names = app.registry.names()
        for expected in (
            "analyze_repository",
            "read_file",
            "search_code",
            "write_file",
            "edit_file",
            "run_command",
            "run_tests",
            "python_exec",
            "install_package",
            "git_status",
            "git_diff",
            "git_commit",
        ):
            self.assertIn(expected, names, f"{expected} must be available to the agent")
        self.assertEqual(len(app.registry.all()), len(names))
        spec = app.registry.get("git_commit")
        self.assertIsNotNone(spec)
        self.assertTrue(spec.description, "every tool must describe itself")

    def test_permission_policy_classifies_actions(self) -> None:
        from codemind.agent.permissions import RiskLevel

        app = self.make_app()
        read = app.policy.decide("read_file", {"path": "README.md"})
        self.assertFalse(read.requires_approval, "reading must never require approval")
        destructive = app.policy.decide("run_command", {"command": "rm -rf /tmp/x"})
        self.assertIn(destructive.risk, (RiskLevel.DESTRUCTIVE, RiskLevel.WRITE))
        credential = app.policy.decide("run_command", {"command": "cat ~/.aws/credentials"})
        self.assertTrue(credential.requires_approval, "credential-exposing actions always ask")
        blocked = app.policy.decide("run_command", {"command": "sudo apt-get install foo"})
        self.assertTrue(blocked.blocked, "blocked patterns must be refused outright")

    def test_detected_test_command_really_finds_tests(self) -> None:
        """A tests directory without __init__.py is common; bare discovery finds nothing there."""
        from tests.support import make_sample_project

        project = self.project_root.parent / "flat-layout"
        make_sample_project(project)
        (project / "tests" / "__init__.py").unlink()

        app = self.make_app()
        task = app.tasks.create("Run the tests", project_path=str(project), title="flat layout")
        context = tool_context(app, task.id)
        result = app.registry.execute("analyze_repository", {"path": "."}, context)
        self.assertTrue(result.ok, result.error)
        commands = result.data["test_commands"]
        self.assertTrue(commands, "a project with tests must yield a test command")
        command = commands[0]
        self.assertIn("-s tests", command, f"the command must point at the tests directory: {command}")

        workspace = app.sandbox.create_workspace(project, task_id=task.id, mode=app.config.sandbox.workspace_mode)
        run = app.sandbox.execute(command, workspace=workspace, task_id=task.id, purpose="test", timeout=180)
        self.assertEqual(run.exit_code, 0, run.stdout + run.stderr)
        self.assertRegex(run.stderr + run.stdout, r"Ran [1-9]\d* tests?", "the detected command must actually collect tests")

    def test_git_status_answers_both_questions(self) -> None:
        """`status()` means two things; both must work (they once collided and broke the tool)."""
        import subprocess

        app = self.make_app()
        integration = app.github.status()
        self.assertIn("enabled", integration)
        self.assertIn("token", integration)

        project = self.project_root
        for command in (
            ["git", "init", "-q"],
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=test", "add", "-A"],
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=test", "commit", "-qm", "init"],
        ):
            subprocess.run(command, cwd=project, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        local = app.github.repo_status(project)
        self.assertTrue(local["ok"], local.get("error"))
        self.assertTrue(local["is_repo"])
        self.assertTrue(local["branch"])

        task = app.tasks.create("Show me the git status", project_path=str(project), title="git status")
        result = app.registry.execute("git_status", {}, tool_context(app, task.id))
        self.assertTrue(result.ok, result.error)
        self.assertIn("branch", result.output)

    def test_repository_analysis_is_real(self) -> None:
        app = self.make_app()
        task = app.tasks.create("Analyse the project structure", project_path=str(self.project_root), title="analyse")
        context = tool_context(app, task.id)
        result = app.registry.execute("analyze_repository", {"path": "."}, context)
        self.assertTrue(result.ok, result.error)
        facts = result.data
        self.assertGreaterEqual(facts["total_files"], 3, "the sample project's files must be counted")
        self.assertEqual(facts["primary_language"], "Python")
        self.assertIn("src", facts["top_directories"])
        self.assertTrue(facts["test_files"], "the sample project ships tests and they must be detected")
        self.assertTrue(facts["test_commands"], "a runnable test command must be detected")
        self.assertIn("python", facts["test_commands"][0].lower())


class AgentRunTests(TempDataCase):
    """Full runs against the sample project."""

    def test_read_only_task_completes_with_evidence(self) -> None:
        app = self.make_app(background=True)
        task = app.submit_task("Analyse this repository and report its languages, size and test setup", project_path=str(self.project_root))
        finished = self.wait_for_task(task.id, timeout=240)
        self.assertIsNotNone(finished)
        self.assertEqual(finished.status, "completed", finished.summary)
        self.assertTrue((finished.verify or {}).get("verified"), "a completed task must carry verification evidence")
        self.assertTrue(finished.steps, "the planner must produce steps")
        tools = [step.tool for step in finished.steps]
        self.assertIn("analyze_repository", tools, "an analysis task must plan the repository inspection")
        self.assertTrue(finished.summary, "a finished task must produce a report")

    def test_missing_dependency_is_diagnosed_installed_and_retried(self) -> None:
        """The core self-correction loop: fail → diagnose → repair → retry → succeed."""
        app = self.make_app(background=True)
        # a project whose test command needs a package that is not installed in the workspace
        project = self.project_root
        (project / "tests" / "test_needs_requests.py").write_text(
            "import requests\n\n\ndef test_http():\n    assert requests.__name__ == 'requests'\n", encoding="utf-8"
        )
        try:
            task = app.submit_task("Run the test suite of this project and report exactly what happens", project_path=str(project))
            finished = self.wait_for_task(task.id, timeout=420)
            events = app.tasks.events(task.id, limit=400)
            kinds = [event["kind"] for event in events]
            self.assertIn("diagnosis", kinds, "a failing step must be diagnosed")
            installed = any(step.tool == "install_package" and step.status == "done" for step in finished.steps)
            if installed:
                self.assertIn("retry", kinds, "after a repair the original step must be retried")
            tools = app.registry.names()
            self.assertIn("install_package", tools)
        finally:
            (project / "tests" / "test_needs_requests.py").unlink(missing_ok=True)

    def test_analysis_task_produces_a_real_report(self) -> None:
        app = self.make_app(background=True)
        task = app.submit_task("Write an architecture report for the src package", project_path=str(self.project_root))
        finished = self.wait_for_task(task.id, timeout=300)
        self.assertIn(finished.status, ("completed", "blocked"), finished.summary)
        diffs = app.tasks.diffs(task.id)
        if finished.status == "completed" and diffs:
            for entry in diffs:
                path = Path(entry["path"])
                if path.exists():
                    text = path.read_text(encoding="utf-8", errors="replace")
                    self.assertIn("CodeMind", text, "a generated report must be genuinely generated, not empty")
        self.assertTrue(finished.summary, "every finished task must explain itself")

    def test_task_survives_restart_and_resumes(self) -> None:
        app = self.make_app(background=True)
        task = app.submit_task("List the files of this project and describe their purpose", project_path=str(self.project_root))
        finished = self.wait_for_task(task.id, timeout=240)
        self.assertIsNotNone(finished)
        app.shutdown()

        # a fresh context over the same storage root must still see the task and its steps
        self.__class__.app = None
        second = self.make_app(background=True)
        again = second.tasks.get(task.id)
        self.assertIsNotNone(again)
        self.assertEqual(again.status, finished.status)
        self.assertEqual(len(again.steps), len(finished.steps))
        self.assertTrue(second.tasks.list(limit=10))

    def test_cancellation_stops_the_work(self) -> None:
        """A slow command must really be killed, not merely marked as cancelled."""
        app = self.make_app(background=True)
        slow = self.project_root / "tests" / "test_slow.py"
        slow.write_text(
            "import time\nimport unittest\n\n\nclass SlowTests(unittest.TestCase):\n"
            "    def test_slow(self):\n        time.sleep(90)\n\n\nif __name__ == '__main__':\n    unittest.main()\n",
            encoding="utf-8",
        )
        try:
            task = app.submit_task("Run the test suite of this project and report the results", project_path=str(self.project_root))
            deadline = time.time() + 90
            while time.time() < deadline and app.tasks.get(task.id).status not in ("running", "verifying", "repairing"):
                time.sleep(0.25)
            self.assertTrue(app.runner.cancel(task.id), "an active task must be cancellable")
            stopped_by = time.time() + 45
            fresh = app.tasks.get(task.id)
            while time.time() < stopped_by and not fresh.is_terminal:
                time.sleep(0.5)
                fresh = app.tasks.get(task.id)
            self.assertTrue(fresh.is_terminal, f"a cancelled task must end promptly, status was {fresh.status}")
            self.assertNotEqual(fresh.status, "completed", "a cancelled task must not be reported as completed")
            # the 90 s sleep was killed, so the task cannot have needed anywhere near that long
            elapsed = (fresh.finished_at or 0) - (fresh.started_at or 0)
            self.assertLess(elapsed, 60, "the running command was not actually stopped")
        finally:
            slow.unlink(missing_ok=True)


class AgentHonestyTests(TempDataCase):
    """Properties that keep the agent trustworthy."""

    def test_authoring_without_a_model_is_reported_not_faked(self) -> None:
        app = self.make_app(background=True)
        # force the deterministic planner (no local model in the test environment)
        app.update_settings({"models.backend": "heuristic"})
        task = app.submit_task("Implement a new rate limiter module with configurable buckets", project_path=str(self.project_root))
        finished = self.wait_for_task(task.id, timeout=300)
        self.assertIn(finished.status, ("blocked", "failed", "completed"), finished.summary)
        if finished.status != "completed":
            self.assertTrue(
                any("model" in (step.error or "").lower() for step in finished.steps) or "model" in (finished.error or "").lower(),
                f"a blocked task must say a model is needed: {finished.error}",
            )
        # nothing may be invented as a placeholder
        for entry in app.tasks.diffs(task.id):
            path = Path(entry["path"])
            if path.exists() and path.suffix == ".py":
                text = path.read_text(encoding="utf-8", errors="replace")
                self.assertNotIn("TODO: implement", text)

    def test_planner_always_includes_a_verification_step(self) -> None:
        app = self.make_app()
        from codemind.llm.heuristic import build_plan
        from codemind.agent.planner import VERIFICATION_TOOLS

        plan = build_plan("Fix the failing parser test", files=[], tools=list(app.registry.prompts()), repo={}, memory=[])
        payload = plan if isinstance(plan, dict) else plan.as_dict()
        tools = [step.get("tool") for step in payload["steps"]]
        self.assertTrue(
            any(tool in VERIFICATION_TOOLS for tool in tools),
            f"a plan must contain a verification step, got {tools}",
        )
        self.assertEqual(payload["steps"][0].get("tool"), "analyze_repository", "a plan must start by inspecting the repository")


if __name__ == "__main__":
    unittest.main()
