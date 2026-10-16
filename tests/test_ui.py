"""The desktop interface and the launcher.

A GUI test cannot judge whether the design is beautiful, but it can prove the things that were
actually broken during development: that every view refreshes against a real application without
raising, that a failure in one card does not blank the window, and that the documented entry point
(``python3 main.py``) really starts and reports its own health.

Qt needs a display (or the offscreen platform plugin). When neither is available the UI tests skip
instead of failing, so the suite stays runnable on a headless machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support import ROOT, TempDataCase

VIEW_COUNT = 7
VIEW_NAMES = ("Overview", "Coding Agent", "Training", "Sandbox", "Memory & Models", "GitHub", "Settings")


def _qt_available() -> tuple[bool, str]:
    try:
        from PySide6 import QtWidgets  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on the machine
        return False, f"PySide6 is not importable with {sys.executable}: {exc}"
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        if app is None:  # pragma: no cover - defensive
            return False, "no QApplication could be created"
    except Exception as exc:  # pragma: no cover - depends on the machine
        return False, f"Qt cannot open a display: {exc}"
    return True, ""


class LauncherTests(TempDataCase):
    """The documented entry points must work exactly as the README says."""

    def run_cli(self, *args: str, timeout: float = 300.0) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["CODEMIND_DATA_DIR"] = str(self.data_root)
        env.setdefault("PYTHONUNBUFFERED", "1")
        return subprocess.run(
            [sys.executable, str(ROOT / "main.py"), *args],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )

    def test_version_and_check(self) -> None:
        version = self.run_cli("--version", timeout=120)
        self.assertEqual(version.returncode, 0, version.stdout)
        self.assertIn("CodeMind", version.stdout)

        check = self.run_cli("--check", timeout=300)
        self.assertEqual(check.returncode, 0, check.stdout)
        self.assertIn("check finished", check.stdout)
        self.assertIn("agent tools", check.stdout)
        self.assertIn("sandbox", check.stdout)
        # the desktop readiness must be reported honestly: either it works, or the check says why
        self.assertIn("PySide6", check.stdout)
        if "no problems" not in check.stdout:
            self.assertIn("warning", check.stdout, "a non-zero problem count must be explained")
            self.assertIn("not usable here", check.stdout)

    def test_launcher_never_reexecutes_twice(self) -> None:
        """A plain `python3 main.py` may switch to the checkout's venv — once, and only as a script."""
        import main as launcher

        real_argv = list(sys.argv)
        calls: list[tuple] = []
        real_execve = os.execve

        def recording_execve(*args, **kwargs):  # pragma: no cover - only on a regression
            calls.append(args)
            raise AssertionError("the launcher replaced the test process")

        os.execve = recording_execve  # type: ignore[assignment]
        try:
            os.environ["CODEMIND_REEXEC"] = "1"
            try:
                self.assertFalse(launcher._reexec_with_project_venv(), "the re-exec must never loop")
            finally:
                os.environ.pop("CODEMIND_REEXEC", None)

            # imported (argv[0] is the test runner, not main.py): the process must be left alone
            sys.argv = ["/usr/lib/python3/unittest/__main__.py", "discover", "-s", "tests"]
            self.assertFalse(launcher._reexec_with_project_venv(), "an import must never re-exec")
            self.assertEqual(calls, [])
        finally:
            sys.argv = real_argv
            os.execve = real_execve  # type: ignore[assignment]

    def test_doctor_reports_real_machine_state(self) -> None:
        import json

        result = self.run_cli("--doctor", timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout)
        payload = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertIn("hardware", payload)
        self.assertIn("tools", payload)
        self.assertTrue(payload["hardware"]["logical_cores"] >= 1)
        self.assertIsInstance(payload["tools"], list)
        self.assertGreaterEqual(len(payload["tools"]), 20, "the doctor must list the real tool inventory")
        self.assertIn("analyze_repository", payload["tools"])
        self.assertIn("sandbox", payload)
        self.assertIn("training", payload)
        self.assertIn("policy", payload)

    def test_headless_task_runner_verifies_its_own_work(self) -> None:
        """`main.py --task ...` must exit 0 only when the task really completed."""
        project = self.project_root
        result = self.run_cli(
            "--task",
            "Analyse this repository and report its languages, size and test setup",
            "--project",
            str(project),
            "--timeout",
            "420",
            timeout=600,
        )
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
        self.assertIn("completed", result.stdout.lower())


class ViewTests(TempDataCase):
    def setUp(self) -> None:
        self.reset_storage()

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.qt_ok, cls.qt_reason = _qt_available()

    def test_every_view_refreshes_against_a_real_application(self) -> None:
        if not self.qt_ok:
            self.skipTest(f"{self.qt_reason} (run this suite with ./.venv/bin/python -m unittest)")

        from codemind.ui.main_window import MainWindow

        app = self.make_app(background=False)
        # most views only show their interesting branches when there is real state; a background job
        # and a memory entry have each broken a view before
        job = app.jobs.start("ui-smoke", ["/bin/bash", "-lc", "echo started; sleep 45"], cwd=self.project_root)
        app.memory.remember("architecture", "UI smoke test memory", "Written by the interface test suite.")
        window = MainWindow(app)
        try:
            self.assertEqual(len(window.views), VIEW_COUNT, "the window must show every documented view")
            self.assertEqual(list(window.views.keys()), list(VIEW_NAMES))
            for index, name in enumerate(VIEW_NAMES):
                self.assertEqual(name, window.buttons[index].label(), "every view needs a sidebar button")
                window.show_view(index)
                view = list(window.views.values())[index]
                self.assertIs(window.stack.currentWidget(), view)
                failures = view.safe_refresh()
                self.assertEqual(failures, [], f"the {name} view failed to refresh: {failures}")
            # a second pass must be just as clean: refresh is what the 1.5s timer calls forever
            for name, view in window.views.items():
                self.assertEqual(view.safe_refresh(), [], f"{name} is not idempotent")
        finally:
            window.close()
            app.jobs.stop(job.id)

    def test_one_broken_card_does_not_blank_the_window(self) -> None:
        if not self.qt_ok:
            self.skipTest(self.qt_reason)

        from codemind.ui.main_window import MainWindow

        app = self.make_app(background=False)
        window = MainWindow(app)
        try:
            view = next(iter(window.views.values()))
            # simulate a broken data source: the refresh must report the section, not raise
            original = app.overview_snapshot

            def broken(*args, **kwargs):
                raise RuntimeError("simulated failure")

            app.overview_snapshot = broken  # type: ignore[assignment]
            try:
                failures = view.safe_refresh()
                self.assertTrue(failures, "a failing section must be reported")
                self.assertTrue(any("simulated failure" in item for item in failures))
            finally:
                app.overview_snapshot = original  # type: ignore[assignment]
            self.assertEqual(view.safe_refresh(), [], "the view must recover once the cause is gone")
        finally:
            window.close()

class PaletteTests(TempDataCase):
    """The command palette must offer the real actions, and only real ones."""

    def setUp(self) -> None:
        self.reset_storage()

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.qt_ok, cls.qt_reason = _qt_available()

    def _window(self):
        from codemind.ui.main_window import MainWindow

        app = self.make_app(background=False)
        window = MainWindow(app)
        return app, window

    def test_every_listed_action_is_callable_and_complete(self) -> None:
        if not self.qt_ok:
            self.skipTest(f"{self.qt_reason} (run with ./.venv/bin/python -m unittest)")
        from codemind.ui.command_palette import build_commands

        _app, window = self._window()
        try:
            commands = build_commands(window)
            titles = [command.title for command in commands]
            self.assertGreaterEqual(len(commands), 12)
            for command in commands:
                self.assertTrue(command.title, "a command without a title cannot be found")
                self.assertTrue(callable(command.run), f"{command.title} has nothing to run")
            for expected in (
                "Refresh this view",
                "Keyboard shortcuts",
                "Open data folder",
                "Copy diagnostics",
                "Quit CodeMind",
                "Focus the task prompt",
                "Cancel the active task",
                "Export the change passport",
                "Roll back a task",
                "Run an autonomy session now",
                "Start a training run",
                "New isolated workspace",
            ):
                self.assertIn(expected, titles, f"the palette is missing {expected!r}")
            for name in VIEW_NAMES:
                self.assertIn(f"Open {name}", titles, "every view must be reachable from the palette")
        finally:
            window.close()

    def test_palette_filters_and_runs(self) -> None:
        if not self.qt_ok:
            self.skipTest(self.qt_reason)
        from codemind.ui.command_palette import CommandPalette, build_commands

        _app, window = self._window()
        try:
            palette = CommandPalette(build_commands(window), window)
            palette.query.setText("training")
            names = [command.title for command in palette._shown]
            self.assertIn("Start a training run", names)
            self.assertTrue(all("training" in command.haystack() for command in palette._shown))

            # typing the wrong thing must not silently offer everything
            palette.query.setText("zzzz-no-such-command")
            self.assertEqual(palette._shown, [])

            # selecting a row really switches the view
            palette.query.setText("open sandbox")
            palette.results.setCurrentRow(0)
            palette._run_selected()
            self.assertEqual(window.stack.currentWidget(), window.views["Sandbox"])
        finally:
            window.close()


class AppearanceTests(TempDataCase):
    """The appearance settings in the config file have to reach the pixels."""

    def setUp(self) -> None:
        self.reset_storage()

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.qt_ok, cls.qt_reason = _qt_available()

    def test_surface_accent_and_motion_come_from_the_settings(self) -> None:
        if not self.qt_ok:
            self.skipTest(self.qt_reason)
        from codemind.ui import anim, theme as T
        from codemind.ui.main_window import MainWindow

        app = self.make_app(background=False)
        app.update_settings(
            {
                "app.theme_mode": "midnight",
                "app.glass_intensity": 0.35,
                "app.accent": "#FF8A4C",
                "app.motion": "reduced",
                "app.start_view": "sandbox",
            }
        )
        window = MainWindow(app)
        try:
            self.assertEqual(T.appearance().mode, "midnight")
            self.assertAlmostEqual(T.appearance().glass, 0.35, places=3)
            self.assertEqual(T.ACCENT.upper(), "#FF8A4C")
            self.assertEqual(T.page_gradient(), ("#05070E", "#02030A"))
            self.assertLess(T.glass_alpha(), 1.0)
            self.assertFalse(anim.full_motion())
            self.assertEqual(anim.duration(300), 180, "reduced motion must shorten animations")
            self.assertIn("reduced", anim.describe_motion())
            # the start view setting decides which page is open
            self.assertEqual(window.stack.currentWidget(), window.views["Sandbox"])
            self.assertTrue(window.buttons[VIEW_NAMES.index("Sandbox")].isChecked())

            # changing the accent while the window is open must reach the shell, not only the config
            app.update_settings({"app.accent": "#34C759"})
            window.apply_appearance()
            self.assertEqual(T.ACCENT.upper(), "#34C759")

            # motion off means every state appears instantly
            app.update_settings({"app.motion": "off"})
            self.assertFalse(anim.enabled())
            self.assertEqual(anim.duration(400), 0)
            self.assertIn("off", anim.describe_motion())
        finally:
            window.close()

    def test_window_geometry_is_remembered(self) -> None:
        if not self.qt_ok:
            self.skipTest(self.qt_reason)
        from codemind.ui.main_window import MainWindow

        app = self.make_app(background=False)
        window = MainWindow(app)
        window.resize(1180, 800)
        window.close()
        stored = app.config.app.window or {}
        self.assertEqual((stored.get("w"), stored.get("h")), (1180, 800))

        second = MainWindow(app)
        try:
            self.assertEqual((second.width(), second.height()), (1180, 800), "the window must reopen where it was")
        finally:
            second.close()

        # with the setting switched off the geometry is deliberately not restored
        app.update_settings({"app.restore_geometry": False})
        third = MainWindow(app)
        try:
            self.assertNotEqual((third.width(), third.height()), (1180, 800))
        finally:
            third.close()


class RenderingTests(TempDataCase):
    """The glass surfaces must actually paint something (a blank pane is the classic Qt mistake)."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.qt_ok, cls.qt_reason = _qt_available()

    def test_cards_and_views_paint_a_background(self) -> None:
        if not self.qt_ok:
            self.skipTest(self.qt_reason)
        from PySide6.QtGui import QPixmap

        from codemind.ui import theme as T
        from codemind.ui.views.base import View
        from codemind.ui.widgets.glass import Card, Sparkline, StatTile

        app = self.make_app(background=False)
        card = Card("Title", "Subtitle")
        card.resize(320, 140)
        tile = StatTile("CPU", "0%")
        tile.set_number(37.5, lambda value: f"{value:.0f}%")
        self.assertEqual(tile.value.text(), "38%")
        spark = Sparkline()
        spark.set_values([1, 5, 3, 9])
        self.assertEqual(spark._values, [1.0, 5.0, 3.0, 9.0])
        for widget in (card, tile, spark):
            widget.show()
            pixmap = widget.grab()
            self.assertFalse(pixmap.isNull())
            image = pixmap.toImage()
            colors = {image.pixel(x, y) for x in range(0, image.width(), 17) for y in range(0, image.height(), 13)}
            self.assertGreater(len(colors), 1, f"{type(widget).__name__} painted a flat single colour")
        view = View(app)
        view.resize(600, 400)
        view.show()
        grabbed = view.grab().toImage()
        top = grabbed.pixelColor(5, 5)
        bottom = grabbed.pixelColor(5, view.height() - 5)
        self.assertNotEqual(top.name(), T.TEXT, "the view must paint its own background")
        self.assertNotEqual((top.red(), top.green(), top.blue()), (bottom.red(), bottom.green(), bottom.blue()),
                            "the page gradient must differ from top to bottom")



if __name__ == "__main__":
    unittest.main()
