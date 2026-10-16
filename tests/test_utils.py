"""Utilities, credential hygiene and configuration validation.

These are the small pieces every other subsystem leans on, so a regression here would be felt
everywhere: a leaky log, a command that outlives its timeout, a config value that is accepted but
never validated. Each of them is checked directly.
"""

from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path

from tests.support import TempDataCase


class TextAndFileTests(TempDataCase):
    def test_shorten_is_total(self) -> None:
        from codemind.utils.textutil import shorten

        # every column in the database can be NULL, and the UI feeds those straight into shorten()
        self.assertEqual(shorten(None), "")
        self.assertEqual(shorten("", 10), "")
        self.assertEqual(shorten("short", 40), "short")
        long_text = "x" * 200
        self.assertLessEqual(len(shorten(long_text, 20)), 20)
        self.assertIn("…", shorten(long_text, 20))

    def test_truncate_and_diff_helpers(self) -> None:
        from codemind.utils.textutil import diff_stats, slugify, strip_ansi, tail_lines, truncate_middle, unified_diff

        self.assertEqual(strip_ansi("\x1b[31mred\x1b[0m"), "red")
        middle = truncate_middle("a" * 5000, 200)
        self.assertLess(len(middle), 5000)
        self.assertEqual(len(tail_lines("1\n2\n3\n4\n", 2).splitlines()), 2)
        self.assertEqual(slugify("Fix the Parser Bug!"), "fix-the-parser-bug")
        diff = unified_diff("one\n", "two\n", "file.txt")
        self.assertIn("-one", diff)
        self.assertIn("+two", diff)
        self.assertEqual(diff_stats(diff)["removed"], 1)
        self.assertEqual(diff_stats(diff)["added"], 1)

    def test_human_readable_formatters(self) -> None:
        from codemind.utils.files import human_bytes, human_duration, human_rate

        self.assertEqual(human_bytes(0), "0 B")
        self.assertTrue(human_bytes(1536).startswith("1.5"))
        self.assertTrue(human_bytes(5 * 1024**3).endswith("GB"))
        self.assertEqual(human_duration(9), "9.0 s")
        self.assertIn("m", human_duration(90))
        self.assertTrue(human_rate(2048).endswith("/s"))
        self.assertIn("GB", human_bytes(5 * 1024**3))

    def test_atomic_writes_and_safe_paths(self) -> None:
        from codemind.utils.files import atomic_write_json, atomic_write_text, read_json, resolve_within, safe_relpath

        root = self.data_root / "writes"
        target = atomic_write_text(root / "note.txt", "hello")
        self.assertEqual(target.read_text(encoding="utf-8"), "hello")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600, "private files must not be world readable")
        atomic_write_json(root / "data.json", {"a": 1})
        self.assertEqual(read_json(root / "data.json"), {"a": 1})
        self.assertEqual(read_json(root / "missing.json", default={}), {})
        self.assertEqual(safe_relpath(root / "sub" / "x.txt", root), os.path.join("sub", "x.txt"))
        with self.assertRaises(ValueError):
            resolve_within(root, "../../etc/passwd")


class CommandTests(TempDataCase):
    def test_command_success_failure_and_timeout(self) -> None:
        from codemind.utils.process import run_command

        ok = run_command(["python3", "-c", "print('hello')"], cwd=self.data_root, timeout=30)
        self.assertTrue(ok.ok, ok.error)
        self.assertEqual(ok.exit_code, 0)
        self.assertIn("hello", ok.stdout)

        bad = run_command(["python3", "-c", "import sys; sys.exit(3)"], cwd=self.data_root, timeout=30)
        self.assertFalse(bad.ok)
        self.assertEqual(bad.exit_code, 3)

        slow = run_command(["python3", "-c", "import time; time.sleep(30)"], cwd=self.data_root, timeout=1.5)
        self.assertTrue(slow.timed_out, "a command that overruns its timeout must be killed")
        self.assertLess(slow.duration, 15, "the timeout must be enforced, not merely requested")
        self.assertTrue(slow.killed_by)

    def test_missing_working_directory_is_reported(self) -> None:
        from codemind.utils.process import run_command

        result = run_command(["echo", "hi"], cwd=self.data_root / "does-not-exist", timeout=10)
        self.assertFalse(result.ok)
        self.assertIsNone(result.exit_code)
        self.assertIn("does not exist", result.error or "")

    def test_streaming_process_can_be_terminated(self) -> None:
        from codemind.utils.process import StreamingProcess

        lines: list[str] = []
        process = StreamingProcess(
            ["python3", "-c", "import time; print('started', flush=True); time.sleep(30)"],
            cwd=self.data_root,
            on_line=lambda _stream, line: lines.append(line),
        )
        process.start()
        self.assertIsNotNone(process.pid)
        time.sleep(1.2)
        self.assertTrue(process.running)
        process.terminate()
        exit_code = process.wait(timeout=15)
        self.assertIsNotNone(exit_code, "a terminated process must be reaped")
        self.assertFalse(process.running)
        self.assertTrue(any("started" in line for line in lines), "streamed output must be captured")

    def test_child_processes_do_not_inherit_secrets(self) -> None:
        from codemind.utils.process import build_env, run_command
        from codemind.utils.redaction import REDACTED, is_sensitive_env, redact_env

        token = "ghp_" + "A" * 24
        os.environ["CODEMIND_TEST_TOKEN"] = token
        os.environ["MY_API_KEY"] = "sk-" + "b" * 24
        try:
            self.assertTrue(is_sensitive_env("MY_API_KEY"))
            env = build_env()
            self.assertIn("PATH", env)
            self.assertNotIn("CODEMIND_TEST_TOKEN", env, "the parent environment's tokens must not be inherited")
            self.assertNotIn("MY_API_KEY", env)
            self.assertEqual(redact_env({"GITHUB_TOKEN": token})["GITHUB_TOKEN"], REDACTED)

            # and a command that tries to echo the parent's token sees nothing
            result = run_command(["python3", "-c", "import os; print(os.environ.get('MY_API_KEY', 'absent'))"], cwd=self.data_root, timeout=30)
            self.assertIn("absent", result.stdout)
            self.assertNotIn(token, result.stdout)
        finally:
            os.environ.pop("CODEMIND_TEST_TOKEN", None)
            os.environ.pop("MY_API_KEY", None)


class RedactionTests(TempDataCase):
    def test_known_credential_shapes_are_redacted(self) -> None:
        from codemind.utils.redaction import REDACTED, redact_text, scrub_mapping

        samples = [
            "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "github_pat_11ABCDEFG0abcdefghijklmnopqrstuv",
            "AWS key AKIAIOSFODNN7EXAMPLE",
            "api_key = sk-abcdef0123456789abcdef",
            "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6",
            "https://user:hunter2@github.com/private/repo.git",
            "OPENAI_TOKEN: supersecretvalue123",
        ]
        for sample in samples:
            cleaned = redact_text(sample)
            self.assertIn(REDACTED, cleaned, f"not redacted: {sample}")
            for secret in ("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "AKIAIOSFODNN7EXAMPLE", "hunter2", "supersecretvalue123"):
                self.assertNotIn(secret, cleaned)
        scrubbed = scrub_mapping({"GITHUB_TOKEN": "ghp_real", "note": "fine"})
        self.assertEqual(scrubbed["GITHUB_TOKEN"], REDACTED)
        self.assertEqual(scrubbed["note"], "fine")

    def test_secrets_never_reach_the_logs_or_the_database(self) -> None:
        app = self.make_app()
        token = "ghp_" + "Z" * 32
        log_path = Path(app.info.log_path)
        self.assertTrue(log_path.exists(), "the application must log to a file")

        workspace = app.sandbox.create_workspace(self.project_root, task_id="redaction", mode=app.config.sandbox.workspace_mode)
        result = app.sandbox.execute(
            f"echo 'export GITHUB_TOKEN={token}' && echo 'using {token}'",
            workspace=workspace,
            task_id="redaction",
            purpose="test",
        )
        self.assertIn(token[:4], result.stdout)
        from codemind.logging_setup import get_logger

        get_logger("test.redaction").info("payload contained %s", token)

        # give the logging handlers a moment, then look at every artefact that persists text
        time.sleep(0.4)
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        self.assertNotIn(token, log_text, "a credential reached the log file")
        executions = json.dumps(app.db.query("SELECT * FROM executions ORDER BY id DESC LIMIT 5"), default=str)
        self.assertIn("‹redacted›", log_text, "the log must show that something was redacted")
        self.assertNotIn(token, executions, "a credential reached the execution history")
        tool_calls = json.dumps(app.db.query("SELECT * FROM tool_calls ORDER BY id DESC LIMIT 5"), default=str)
        self.assertNotIn(token, tool_calls, "a credential reached the tool-call history")


class ConfigTests(TempDataCase):
    def test_settings_round_trip_and_validation(self) -> None:
        from codemind.config import ConfigError

        app = self.make_app()
        changed = app.update_settings({"agent.max_steps": 7, "sandbox.allow_network": True})
        self.assertIn("agent.max_steps", changed)
        self.assertEqual(app.config.agent.max_steps, 7)
        self.assertTrue(app.config.sandbox.allow_network)

        with self.assertRaises(ConfigError):
            app.update_settings({"agent.max_steps": 10_000})  # above the documented ceiling of 400
        with self.assertRaises(ConfigError):
            app.update_settings({"agent.max_steps": "not a number"})
        with self.assertRaises((KeyError, ConfigError)):
            app.update_settings({"nope.unknown_key": 1})
        # a rejected value must not have been applied
        self.assertEqual(app.config.agent.max_steps, 7)

    def test_settings_persist_across_restart(self) -> None:
        app = self.make_app()
        app.update_settings({"agent.autonomy": "trusted", "sandbox.allow_network": True, "training.epochs": 2})
        app.shutdown()
        self.__class__.app = None
        second = self.make_app()
        self.assertEqual(second.config.agent.autonomy, "trusted")
        self.assertTrue(second.config.sandbox.allow_network)
        self.assertEqual(second.config.training.epochs, 2)

    def test_storage_layout_is_created_and_overridable(self) -> None:
        app = self.make_app()
        summary = app.layout.summary()
        self.assertTrue(summary["data_root"])
        for section in ("databases", "logs", "datasets", "models"):
            path = Path(app.layout.resolve(section))
            self.assertTrue(path.exists(), f"{section} directory must exist")
        from codemind.storage.layout import detect_usb_volumes

        volumes = detect_usb_volumes(app.config.storage.usb_marker)
        self.assertIsInstance(volumes, list, "USB detection must always answer, even with nothing plugged in")
        categories = summary.get("categories") or app.layout.category_paths()
        self.assertTrue(categories, "the storage layout must describe its categories")

class JobLogPersistenceTests(TempDataCase):
    """Background processes have to survive a restart — which means their output must be on disk."""

    def test_job_output_is_written_to_the_log_file(self) -> None:
        from codemind.utils.jobs import JobManager

        manager = JobManager(self.make_app().db, self.data_root / "joblogs")
        job = manager.start("log-probe", ["python3", "-c", "print('first line'); print('second line')"], cwd=self.data_root)
        deadline = time.time() + 30
        while job.running and time.time() < deadline:
            time.sleep(0.05)

        path = Path(job.log_path)
        self.assertTrue(path.exists(), "every job needs a log file on disk, not only an in-memory tail")
        text = path.read_text(encoding="utf-8")
        self.assertIn("first line", text)
        self.assertIn("second line", text)
        self.assertEqual(job.exit_code, 0)
        self.assertEqual(job.status, "stopped")
        stats = manager.stats()
        self.assertGreaterEqual(stats["total"], 1)
        self.assertEqual(stats["running"], 0)

    def test_stopping_a_job_ends_the_process_and_the_log_stays_readable(self) -> None:
        from codemind.utils.jobs import JobManager

        manager = JobManager(self.make_app().db, self.data_root / "joblogs")
        job = manager.start("sleeper", ["python3", "-c", "import time; print('up', flush=True); time.sleep(60)"], cwd=self.data_root)
        deadline = time.time() + 15
        while job.runtime < 0.5 and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(job.running)
        self.assertTrue(manager.stop(job.id), "a running job must be stoppable")
        deadline = time.time() + 20
        while job.running and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(job.running)
        self.assertIn("up", manager.log_tail(job.id))
        self.assertEqual(manager.stop_all(), 0, "nothing may be left running")


class UsbRoutingTests(TempDataCase):
    """An attached 128 GB drive takes the heavy categories, everything else stays internal."""

    def test_attached_volume_receives_the_configured_categories(self) -> None:
        from codemind.storage.layout import StorageLayout

        volume = self.data_root.parent / "fake-usb"
        (volume / "codemind").mkdir(parents=True, exist_ok=True)
        (volume / ".codemind").write_text("codemind", encoding="utf-8")
        layout = StorageLayout.create(
            self.data_root / "layout",
            usb_override=str(volume),
            usb_categories=("datasets", "checkpoints", "models"),
        )
        self.assertEqual(layout.usb_active, volume)
        self.assertTrue(layout.volumes[0].marked)
        datasets = layout.resolve("datasets")
        self.assertEqual(datasets, volume / "codemind" / "datasets")
        self.assertTrue(datasets.exists(), "the directory on the drive must be created")
        self.assertEqual(layout.resolve("logs"), self.data_root / "layout" / "logs", "logs stay on the internal disk")
        self.assertEqual(layout.summary()["usb_active"], str(volume))

    def test_unavailable_volume_falls_back_without_losing_state(self) -> None:
        from codemind.storage.layout import StorageLayout

        layout = StorageLayout.create(
            self.data_root / "layout2",
            usb_override=str(self.data_root / "not-mounted"),
            usb_categories=("datasets", "checkpoints"),
        )
        self.assertIsNone(layout.usb_active)
        self.assertEqual(layout.volumes, [])
        datasets = layout.resolve("datasets")
        self.assertEqual(datasets, self.data_root / "layout2" / "datasets")
        self.assertTrue(datasets.exists(), "without the drive the data root has to carry everything")


class SettingsRepairTests(TempDataCase):
    """One broken value in a hand-edited settings file must not throw the rest away."""

    def test_a_single_invalid_value_is_repaired_and_reported(self) -> None:
        from codemind.config import Config, ConfigStore

        path = self.data_root / "settings-repair.json"
        path.write_text(
            json.dumps(
                {
                    "agent": {"max_steps": 100_000, "autonomy": "autonomous"},
                    "app": {"accent": "not-a-colour", "theme_mode": "midnight"},
                }
            ),
            encoding="utf-8",
        )
        store = ConfigStore(path)
        config = store.load()
        self.assertEqual(config.agent.max_steps, Config().agent.max_steps)
        self.assertEqual(config.agent.autonomy, "autonomous", "valid neighbours must survive")
        self.assertEqual(config.app.theme_mode, "midnight")
        self.assertEqual(config.app.accent, Config().app.accent)
        self.assertGreaterEqual(len(store.load_errors), 2, f"the repairs must be reported: {store.load_errors}")
        self.assertTrue(any("max_steps" in note for note in store.load_errors))
        self.assertTrue(any("accent" in note for note in store.load_errors))

    def test_unknown_keys_are_reported_as_configuration_errors(self) -> None:
        from codemind.config import ConfigError, ConfigStore

        store = ConfigStore(self.data_root / "settings-unknown.json")
        store.load()
        with self.assertRaises(ConfigError):
            store.update({"agent.no_such_setting": 1})
        with self.assertRaises(ConfigError):
            store.config.get("agent.no_such_setting")


if __name__ == "__main__":
    unittest.main()
