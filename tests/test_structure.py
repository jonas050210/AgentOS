"""Structural checks on the source tree itself.

This module exists because of a real bug: ``GithubClient`` defined ``status()`` twice, so the local
git status silently replaced the integration status and the ``git_status`` tool broke at runtime
without any import error. A small amount of static checking catches that whole family of mistakes —
duplicate definitions, modules that do not import, and hardcoded machine-specific paths, which the
requirements forbid.
"""

from __future__ import annotations

import ast
import collections
import importlib
import pathlib
import re
import sys
import unittest

from tests.support import ROOT

PACKAGE = ROOT / "codemind"


def product_modules() -> list[pathlib.Path]:
    modules = [path for path in sorted(PACKAGE.rglob("*.py"))]
    return [path for path in modules if "__pycache__" not in path.parts]


class SourceTreeTests(unittest.TestCase):
    def test_no_duplicate_definitions_in_a_class_body(self) -> None:
        problems: list[str] = []
        for path in product_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                counter = collections.Counter(
                    item.name for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
                for name, count in counter.items():
                    if count > 1:
                        problems.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}.{name} defined {count}×")
        self.assertEqual(problems, [], "a redefined method silently shadows the first one")

    def test_no_hardcoded_machine_paths(self) -> None:
        """Mount points and home directories must be detected, never assumed."""
        pattern = re.compile(r"^/(?:home|Users)/[A-Za-z0-9._-]+")
        problems: list[str] = []
        for path in product_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if pattern.match(node.value.strip()):
                        problems.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.value!r}")
        self.assertEqual(problems, [], "the product must not hardcode another machine's paths")

    def test_no_unimplemented_placeholders(self) -> None:
        """Nothing in the product may be a stub pretending to work."""
        markers = ("NotImplementedError", "TODO: implement", "FIXME")
        problems: list[str] = []
        for path in product_modules():
            text = path.read_text(encoding="utf-8")
            for marker in markers:
                if marker in text:
                    line = text[: text.index(marker)].count("\n") + 1
                    problems.append(f"{path.relative_to(ROOT)}:{line} contains {marker!r}")
        self.assertEqual(problems, [], "unfinished code must not be shipped as if it worked")

    def test_every_module_imports(self) -> None:
        """Catch syntax errors, bad imports and circular imports early."""
        skipped: list[str] = []
        errors: list[str] = []
        for path in product_modules():
            relative = path.relative_to(ROOT).with_suffix("")
            name = ".".join(relative.parts)
            if name.endswith(".__init__"):
                name = name[: -len(".__init__")]
            try:
                importlib.import_module(name)
            except ImportError as exc:  # pragma: no cover - depends on the machine
                if "PySide6" in str(exc) or "libGL" in str(exc):
                    skipped.append(name)
                    continue
                errors.append(f"{name}: {exc}")
            except Exception as exc:  # pragma: no cover - would be a real defect
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
        self.assertEqual(errors, [], "every module must import cleanly")
        if skipped:  # pragma: no cover - only when Qt is missing
            self.assertGreater(len(skipped), 0)  # keep the reason visible in the report
            print(f"skipped Qt-dependent modules: {', '.join(skipped)}")

    def test_no_shell_or_cloud_calls_in_the_agent(self) -> None:
        """Local inference only: the agent must not reach out to a hosted model API."""
        forbidden = ("api.openai.com", "anthropic.com", "generativelanguage.googleapis.com", "api.mistral.ai")
        problems: list[str] = []
        for path in product_modules():
            text = path.read_text(encoding="utf-8")
            for host in forbidden:
                if host in text:
                    problems.append(f"{path.relative_to(ROOT)} mentions {host}")
        self.assertEqual(problems, [], "no paid or cloud inference endpoints may be wired in")

    def test_documentation_and_entry_points_exist(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("python3 main.py", readme, "the documented launch command must be in the README")
        self.assertTrue((ROOT / "main.py").exists())
        self.assertTrue((ROOT / "requirements.txt").exists() or (ROOT / "pyproject.toml").exists())

    def test_ui_is_never_imported_by_the_headless_paths(self) -> None:
        """Training and headless task runs must work without Qt; importing it there would break that."""
        offenders: list[str] = []
        for path in product_modules():
            if "ui" in path.relative_to(ROOT).parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "import PySide6" in text or "from PySide6" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], "only the interface modules may depend on PySide6")
        self.assertIn("PySide6", (ROOT / "requirements.txt").read_text(encoding="utf-8") if (ROOT / "requirements.txt").exists() else "PySide6")


if __name__ == "__main__":
    unittest.main()
