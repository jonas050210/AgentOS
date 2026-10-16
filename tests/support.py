"""Shared helpers for the test suite: temporary storage, a sample project and a context."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SAMPLE_FILES = {
    "src/__init__.py": "",
    "src/calculator.py": '''"""A tiny calculator used by the tests."""


def add(a, b):
    """Return the sum of two numbers."""
    return a + b


def divide(a, b):
    """Return a / b."""
    if b == 0:
        raise ValueError("cannot divide by zero")
    return a / b


class Calculator:
    """Keeps a history of operations."""

    def __init__(self):
        self.history = []

    def apply(self, operation, a, b):
        result = operation(a, b)
        self.history.append((operation.__name__, a, b, result))
        return result
''',
    "tests/__init__.py": "",
    "tests/test_calculator.py": '''import unittest

from src.calculator import Calculator, add, divide


class CalculatorTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_divide(self):
        self.assertEqual(divide(9, 3), 3)

    def test_divide_by_zero(self):
        with self.assertRaises(ValueError):
            divide(1, 0)

    def test_history(self):
        calculator = Calculator()
        calculator.apply(add, 1, 2)
        self.assertEqual(len(calculator.history), 1)


if __name__ == "__main__":
    unittest.main()
''',
    "README.md": "# Sample project\n\nUsed by the CodeMind test suite.\n",
}


def make_sample_project(root: Path) -> Path:
    """Write a small but real project on disk and return its path."""
    for relative, text in SAMPLE_FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


class TempDataCase(unittest.TestCase):
    """Base class that gives each test its own storage root and application context."""

    data_root: Path
    project_root: Path
    app = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = Path(tempfile.mkdtemp(prefix="codemind-test-"))
        cls.data_root = cls._tmp / "data"
        cls.data_root.mkdir(parents=True, exist_ok=True)
        cls.project_root = make_sample_project(cls._tmp / "project")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.app is not None:
            try:
                cls.app.shutdown()
            except Exception:
                pass
            cls.app = None
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def reset_storage(self) -> None:
        """Give the current test its own storage root.

        Classes that write a lot of state (training runs, promoted models) would otherwise see the
        leftovers of the test that ran before them, and two apps would share one SQLite file.
        """
        if type(self).app is not None:
            try:
                type(self).app.shutdown()
            except Exception:
                pass
            type(self).app = None
        type(self).data_root = Path(tempfile.mkdtemp(prefix="codemind-test-data-", dir=str(self._tmp)))
        self.data_root = type(self).data_root

    def make_app(self, *, background: bool = False):
        from codemind.app import CodeMindApp

        app = CodeMindApp(data_root=self.data_root, start_background=background)
        self.__class__.app = app
        return app

    def wait_for_task(self, task_id: str, timeout: float = 180.0) -> object:
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.app.tasks.get(task_id)
            if task is not None and task.is_terminal:
                return task
            time.sleep(0.25)
        return self.app.tasks.get(task_id)
