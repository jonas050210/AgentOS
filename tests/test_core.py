"""Storage, configuration, hardware, sandbox policy and the event bus."""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from tests.support import TempDataCase


class StorageTests(TempDataCase):
    def test_database_roundtrip_and_integrity(self) -> None:
        app = self.make_app()
        self.assertEqual(app.db.integrity_check(), "ok")
        app.memory.remember("architecture", "storage", "SQLite with WAL and FTS5", quality=0.9)
        hits = app.memory.search("SQLite")
        self.assertTrue(hits, "an inserted memory must be searchable")
        self.assertEqual(hits[0].title, "storage")

    def test_layout_storage_categories_and_usb_detection(self) -> None:
        app = self.make_app()
        layout = app.layout
        for category in ("datasets", "checkpoints", "models", "logs"):
            path = layout.resolve(category)
            self.assertTrue(str(path).startswith(str(app.data_root)) or layout.usb_active is not None)
        summary = layout.summary()
        self.assertIn("data_root", summary)
        self.assertIn("categories", summary)
        self.assertFalse(Path(summary["db_path"]).is_dir())

    def test_settings_persist_and_validate(self) -> None:
        app = self.make_app()
        changed = app.update_settings({"agent.verify_tests": False, "resources.sample_interval": 7})
        self.assertIn("agent.verify_tests", changed)
        self.assertFalse(app.config.agent.verify_tests)
        self.assertEqual(app.config.resources.sample_interval, 7)
        saved = json.loads(app.config_store.path.read_text())
        self.assertFalse(saved["agent"]["verify_tests"])
        # an out-of-range value must be rejected rather than stored silently
        with self.assertRaises(Exception):
            app.update_settings({"resources.sample_interval": -5})


class HardwareAndSandboxTests(TempDataCase):
    def test_hardware_probe_reports_real_values(self) -> None:
        app = self.make_app()
        self.assertTrue(app.wait_for_hardware(60), "hardware detection timed out")
        profile = app.hardware
        self.assertGreater(profile.logical_cores, 0)
        self.assertGreater(profile.ram_total_mb, 0)
        self.assertIn(profile.tier(), ("minimal", "modest", "capable", "workstation"))
        plan = profile.training_plan()
        self.assertIn("method", plan.as_dict() if hasattr(plan, "as_dict") else plan)

    def test_governor_reacts_to_pressure(self) -> None:
        from codemind.system.resources import ResourceSnapshot

        app = self.make_app()
        app.wait_for_hardware(60)
        calm = app.governor.update(app.hardware, app.config, ResourceSnapshot(ram_used_mb=100, ram_total_mb=16000))
        stressed = app.governor.update(
            app.hardware, app.config, ResourceSnapshot(ram_used_mb=15800, ram_total_mb=16000)
        )
        self.assertLessEqual(stressed.agent_parallel, calm.agent_parallel)
        self.assertTrue(stressed.pause_training or stressed.training_threads <= calm.training_threads)

    def test_sandbox_runs_commands_and_records_history(self) -> None:
        app = self.make_app()
        workspace = app.sandbox.create_workspace(self.project_root, task_id="test")
        result = app.sandbox.execute("python3 -c \"print(6 * 7)\"", workspace=workspace, purpose="unit-test")
        self.assertTrue(result.ok, result.summary(300))
        self.assertIn("42", result.stdout)
        history = app.sandbox.history(limit=5)
        self.assertTrue(any("42" in (entry.get("stdout_tail") or "") for entry in history))
        denial = app.sandbox.check_command("sudo rm -rf /", app.sandbox.policy())
        self.assertIsNotNone(denial, "blocked patterns must refuse sudo/rm -rf")

    def test_background_jobs_stream_and_stop(self) -> None:
        app = self.make_app()
        job = app.jobs.start("ticker", ["bash", "-c", "for i in 1 2 3; do echo tick $i; sleep 0.2; done"], cwd=self.project_root)
        deadline = time.time() + 20
        while time.time() < deadline and app.jobs.get(job.id).running:
            time.sleep(0.2)
        detail = app.jobs.get(job.id)
        self.assertEqual(detail.exit_code, 0)
        self.assertIn("tick 3", app.jobs.log_tail(job.id, 10))


class EventBusTests(TempDataCase):
    def test_events_reach_subscribers(self) -> None:
        from codemind.events import BUS, Topics

        seen: list[tuple[str, dict]] = []
        unsubscribe = BUS.subscribe(Topics.MEMORY_WRITE, lambda event: seen.append((event.topic, event.payload)))
        try:
            app = self.make_app()
            app.memory.remember("preference", "formatting", "use ruff", quality=0.8)
            deadline = time.time() + 5
            while time.time() < deadline and not seen:
                time.sleep(0.05)
            self.assertTrue(seen, "a memory write must publish an event")
        finally:
            unsubscribe()


if __name__ == "__main__":
    unittest.main()
