"""Repository analysis and the project knowledge index.

:func:`analyze_repository` walks a checkout and produces facts: languages, file counts,
build/test tooling, entry points, dependencies, git state. :class:`KnowledgeIndex` persists
a per-file symbol outline in SQLite so later tasks can find the right file with a query
instead of reading the whole tree.

Everything here is derived from the working tree with regexes and standard tooling - no
model is involved, so it also works on the machine that hosts the tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from codemind.logging_setup import get_logger
from codemind.storage.db import Database, _loads
from codemind.utils.files import iter_files, read_text
from codemind.utils.process import run_command

log = get_logger("memory.knowledge")

LANGUAGE_BY_SUFFIX = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".jsx": "JavaScript", ".go": "Go", ".rs": "Rust",
    ".java": "Java", ".kt": "Kotlin", ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".c": "C", ".h": "C",
    ".cc": "C++", ".cpp": "C++", ".hpp": "C++", ".swift": "Swift", ".sh": "Shell", ".sql": "SQL",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".md": "Markdown", ".json": "JSON", ".yaml": "YAML",
    ".yml": "YAML", ".toml": "TOML", ".lua": "Lua", ".ex": "Elixir", ".exs": "Elixir", ".dart": "Dart",
}

TEST_PATTERNS = (
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"[^/]+_test\.go$"),
    re.compile(r"\.test\.[jt]sx?$"),
    re.compile(r"\.spec\.[jt]sx?$"),
    re.compile(r"(^|/)spec/"),
)

ENTRY_HINTS = (
    "main.py", "app.py", "cli.py", "__main__.py", "manage.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "main.js", "main.ts", "server.js", "server.ts", "app.js", "app.ts",
    "main.go", "main.rs", "Cargo.toml", "pyproject.toml", "setup.py", "package.json", "Makefile",
    "docker-compose.yml", "Dockerfile", "CMakeLists.txt", "go.mod",
)

SYMBOL_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "Python": [
        ("class", re.compile(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)")),
        ("function", re.compile(r"^(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)")),
        ("import", re.compile(r"^(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+))")),
    ],
    "JavaScript": [
        ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
        ("const", re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\(|function)")),
        ("import", re.compile(r"^\s*import\s+(?:.*from\s+)?['\"]([^'\"]+)['\"]")),
    ],
    "TypeScript": [],  # filled below
    "Go": [
        ("function", re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)")),
        ("type", re.compile(r"^type\s+([A-Za-z_][\w]*)")),
        ("import", re.compile(r'^\s*(?:import\s+)?"([\w./-]+)"')),
    ],
    "Rust": [
        ("function", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][\w]*)")),
        ("struct", re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_][\w]*)")),
        ("use", re.compile(r"^\s*use\s+([\w:]+)")),
    ],
    "Ruby": [
        ("class", re.compile(r"^\s*class\s+([A-Za-z_][\w:]*)")),
        ("function", re.compile(r"^\s*def\s+([A-Za-z_][\w?!]*)")),
    ],
    "Java": [
        ("class", re.compile(r"^\s*(?:public\s+|final\s+|abstract\s+)*class\s+([A-Za-z_][\w]*)")),
        ("function", re.compile(r"^\s*(?:public|private|protected)\s+[\w<>\[\],\s]+\s+([A-Za-z_][\w]*)\s*\(")),
        ("import", re.compile(r"^\s*import\s+([\w\.]+)")),
    ],
}
SYMBOL_PATTERNS["TypeScript"] = SYMBOL_PATTERNS["JavaScript"]
SYMBOL_PATTERNS["C#"] = SYMBOL_PATTERNS["Java"]

DEPENDENCY_FILES = (
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py", "Pipfile", "poetry.lock",
    "package.json", "go.mod", "Cargo.toml", "Gemfile", "pom.xml", "build.gradle", "composer.json",
)

TEST_COMMAND_HINTS: list[tuple[str, str, str]] = [
    ("Python", "pytest", "pytest -q"),
    ("Python", "unittest", "python3 -m unittest discover -v"),
    ("JavaScript", "npm", "npm test --silent"),
    ("TypeScript", "npm", "npm run test --silent"),
    ("Go", "go", "go test ./..."),
    ("Rust", "cargo", "cargo test"),
    ("Java", "maven", "mvn -q test"),
]


@dataclass
class FileFacts:
    path: str
    language: str
    size_bytes: int
    lines: int
    is_test: bool
    symbols: list[tuple[str, str, int]] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "size_bytes": self.size_bytes,
            "lines": self.lines,
            "is_test": self.is_test,
            "symbols": [{"kind": kind, "name": name, "line": line} for kind, name, line in self.symbols],
            "imports": self.imports[:40],
            "hash": self.hash,
        }


@dataclass
class ProjectAnalysis:
    root: str
    name: str
    languages: dict[str, int] = field(default_factory=dict)
    total_files: int = 0
    total_lines: int = 0
    test_files: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    dependency_files: list[str] = field(default_factory=list)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    test_commands: list[str] = field(default_factory=list)
    build_commands: list[str] = field(default_factory=list)
    run_commands: list[str] = field(default_factory=list)
    top_directories: dict[str, int] = field(default_factory=dict)
    git: dict[str, Any] = field(default_factory=dict)
    largest_files: list[dict[str, Any]] = field(default_factory=list)
    files: list[FileFacts] = field(default_factory=list)
    scanned_at: float = field(default_factory=time.time)
    truncated: bool = False
    readme_excerpt: str = ""

    @property
    def primary_language(self) -> str:
        return next(iter(self.languages), "unknown")

    def as_dict(self, include_files: bool = False) -> dict[str, Any]:
        payload = {
            "root": self.root,
            "name": self.name,
            "languages": self.languages,
            "primary_language": self.primary_language,
            "total_files": self.total_files,
            "total_lines": self.total_lines,
            "test_files": self.test_files[:200],
            "entry_points": self.entry_points,
            "dependency_files": self.dependency_files,
            "dependencies": self.dependencies,
            "test_commands": self.test_commands,
            "build_commands": self.build_commands,
            "run_commands": self.run_commands,
            "top_directories": self.top_directories,
            "git": self.git,
            "largest_files": self.largest_files,
            "scanned_at": self.scanned_at,
            "truncated": self.truncated,
            "readme_excerpt": self.readme_excerpt,
        }
        if include_files:
            payload["files"] = [f.as_dict() for f in self.files]
        return payload

    def outline_text(self, limit: int = 4000) -> str:
        """Compact, prompt-friendly description of the repository."""
        lines = [
            f"Project: {self.name} ({self.primary_language})",
            f"Files: {self.total_files} · lines: {self.total_lines}",
            f"Languages: " + ", ".join(f"{lang} {count}" for lang, count in list(self.languages.items())[:6]),
        ]
        if self.top_directories:
            lines.append("Top directories: " + ", ".join(f"{name}/ ({count})" for name, count in list(self.top_directories.items())[:8]))
        if self.entry_points:
            lines.append("Entry points: " + ", ".join(self.entry_points[:10]))
        if self.test_files:
            lines.append(f"Tests: {len(self.test_files)} files, e.g. " + ", ".join(self.test_files[:5]))
        if self.test_commands:
            lines.append("Test commands: " + "; ".join(self.test_commands))
        if self.build_commands:
            lines.append("Build commands: " + "; ".join(self.build_commands))
        if self.dependencies:
            for group, deps in list(self.dependencies.items())[:4]:
                lines.append(f"{group}: " + ", ".join(deps[:20]))
        if self.git:
            lines.append(
                "Git: branch %s, %s changed file(s), remote %s"
                % (self.git.get("branch", "?"), self.git.get("dirty_files", 0), self.git.get("remote", "none"))
            )
        text = "\n".join(lines)
        return text[:limit]

    def to_markdown(self) -> str:
        rows = ["| Fact | Value |", "| --- | --- |"]
        rows.append(f"| Root | `{self.root}` |")
        rows.append(f"| Primary language | {self.primary_language} |")
        rows.append(f"| Files / lines | {self.total_files} / {self.total_lines} |")
        rows.append(f"| Test files | {len(self.test_files)} |")
        rows.append(f"| Test command | {'; '.join(self.test_commands) or 'not detected'} |")
        rows.append(f"| Build command | {'; '.join(self.build_commands) or 'not detected'} |")
        rows.append(f"| Git branch | {self.git.get('branch', '—')} |")
        return "\n".join(rows)


def _count_lines(text: str) -> int:
    return text.count("\n") + (0 if text.endswith("\n") or not text else 1)


def _extract_symbols(text: str, language: str, limit: int = 400) -> tuple[list[tuple[str, str, int]], list[str]]:
    patterns = SYMBOL_PATTERNS.get(language, [])
    symbols: list[tuple[str, str, int]] = []
    imports: list[str] = []
    if not patterns:
        return symbols, imports
    for number, line in enumerate(text.splitlines(), start=1):
        if len(symbols) >= limit:
            break
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith(("#", "//", "*")):
            continue
        for kind, pattern in patterns:
            match = pattern.match(stripped if kind == "function" else stripped)
            if not match:
                match = pattern.search(stripped)
            if match:
                name = next((g for g in match.groups() if g), "")
                if not name:
                    continue
                if kind in ("import", "use"):
                    imports.append(name)
                else:
                    symbols.append((kind, name, number))
                break
    return symbols, imports


def _git_info(root: Path) -> dict[str, Any]:
    if not (root / ".git").exists():
        return {}
    info: dict[str, Any] = {}
    branch = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, timeout=10)
    if branch.ok:
        info["branch"] = branch.stdout.strip()
    remote = run_command(["git", "remote", "get-url", "origin"], cwd=root, timeout=10)
    if remote.ok:
        info["remote"] = remote.stdout.strip()
    status = run_command(["git", "status", "--porcelain"], cwd=root, timeout=15)
    if status.ok:
        changed = [line for line in status.stdout.splitlines() if line.strip()]
        info["dirty_files"] = len(changed)
        info["changes_sample"] = changed[:20]
    head = run_command(["git", "log", "-1", "--pretty=%H %s"], cwd=root, timeout=10)
    if head.ok:
        info["head"] = head.stdout.strip()
    return info


def _parse_dependencies(root: Path, files: Iterable[str]) -> dict[str, list[str]]:
    deps: dict[str, list[str]] = {}
    for name in files:
        path = root / name
        try:
            text = read_text(path, limit=200_000)
        except OSError:
            continue
        if name == "package.json":
            data = _loads(text, {}) or {}
            if isinstance(data, dict):
                deps["npm dependencies"] = list((data.get("dependencies") or {}).keys())[:40]
                deps["npm devDependencies"] = list((data.get("devDependencies") or {}).keys())[:40]
                scripts = data.get("scripts") or {}
                if isinstance(scripts, dict):
                    deps["npm scripts"] = [f"{k}: {v}" for k, v in list(scripts.items())[:15]]
        elif name in ("requirements.txt", "requirements-dev.txt"):
            deps[name] = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")][:60]
        elif name == "pyproject.toml":
            match = re.findall(r'^\s*"?([A-Za-z0-9_.\-]+)"?\s*[<>=!~]', text, flags=re.MULTILINE)
            deps["pyproject dependencies"] = sorted(set(match))[:60]
        elif name == "go.mod":
            deps["go modules"] = re.findall(r"^\s*([\w./-]+)\s+v[\d.]+", text, flags=re.MULTILINE)[:60]
        elif name == "Cargo.toml":
            deps["cargo dependencies"] = re.findall(r"^\s*([A-Za-z0-9_\-]+)\s*=", text, flags=re.MULTILINE)[:60]
        elif name == "Gemfile":
            deps["gems"] = re.findall(r"^\s*gem\s+['\"]([^'\"]+)", text, flags=re.MULTILINE)[:60]
        elif name == "pom.xml":
            deps["maven artifacts"] = re.findall(r"<artifactId>([^<]+)</artifactId>", text)[:40]
    return deps


def _unittest_command(root: Path, analysis: ProjectAnalysis) -> str:
    """Pick a discovery command that really finds the tests in this layout.

    ``python3 -m unittest discover`` only imports tests inside packages. A very common layout is a
    ``tests/`` directory *without* ``__init__.py``; bare discovery then prints "Ran 0 tests" and an
    agent would wrongly conclude that the project has no tests. Pointing discovery at the directory
    that actually holds them is what works there, so use it whenever that is the case.
    """
    directories = sorted({str(Path(name).parent) for name in analysis.test_files if str(Path(name).parent) not in ("", ".")})
    if len(directories) == 1:
        directory = directories[0]
        if not (root / directory / "__init__.py").exists():
            return f"python3 -m unittest discover -s {directory} -v"
    return "python3 -m unittest discover -v"


def detect_test_commands(root: Path, analysis: ProjectAnalysis) -> list[str]:
    """Work out how tests are run here, preferring what the project itself declares."""
    commands: list[str] = []
    package_json = root / "package.json"
    if package_json.exists():
        data = _loads(read_text(package_json, limit=200_000), {}) or {}
        scripts = (data.get("scripts") or {}) if isinstance(data, dict) else {}
        if isinstance(scripts, dict) and scripts.get("test"):
            runner = "npm" if (root / "package-lock.json").exists() else "yarn" if (root / "yarn.lock").exists() else "pnpm" if (root / "pnpm-lock.yaml").exists() else "npm"
            commands.append(f"{runner} test --silent")
    pytest_declared = False
    for marker in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"):
        candidate = root / marker
        if candidate.exists() and "pytest" in read_text(candidate, limit=100_000):
            pytest_declared = True
    uses_pytest = uses_unittest = False
    for name in analysis.test_files[:40]:
        try:
            text = read_text(root / name, limit=60_000)
        except OSError:
            continue
        if re.search(r"^\s*(import pytest|from pytest)", text, flags=re.MULTILINE):
            uses_pytest = True
        elif re.search(r"^\s*(import unittest|from unittest)", text, flags=re.MULTILINE):
            uses_unittest = True
    # Conventional test_*.py files are pytest-shaped even without an explicit import.
    conventional = any(Path(name).name.startswith("test_") for name in analysis.test_files)
    if pytest_declared or uses_pytest or (conventional and not uses_unittest):
        commands.append("python3 -m pytest -q")
    elif uses_unittest:
        commands.append(_unittest_command(root, analysis))
    if (root / "go.mod").exists():
        commands.append("go test ./...")
    if (root / "Cargo.toml").exists():
        commands.append("cargo test")
    if (root / "Makefile").exists():
        makefile = read_text(root / "Makefile", limit=100_000)
        for target in ("test", "check", "lint"):
            if re.search(rf"^{target}\s*:", makefile, flags=re.MULTILINE):
                commands.append(f"make {target}")
    # de-duplicate, keep order
    seen: set[str] = set()
    unique = [c for c in commands if not (c in seen or seen.add(c))]
    return unique


def detect_build_commands(root: Path, analysis: ProjectAnalysis) -> list[str]:
    commands: list[str] = []
    if (root / "package.json").exists():
        data = _loads(read_text(root / "package.json", limit=200_000), {}) or {}
        scripts = (data.get("scripts") or {}) if isinstance(data, dict) else {}
        if "build" in scripts:
            commands.append("npm run build")
        if "typecheck" in scripts:
            commands.append("npm run typecheck")
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        commands.append("python3 -m compileall -q .")
    if (root / "go.mod").exists():
        commands.append("go build ./...")
    if (root / "Cargo.toml").exists():
        commands.append("cargo build")
    if (root / "Makefile").exists():
        makefile = read_text(root / "Makefile", limit=100_000)
        if re.search(r"^build\s*:", makefile, flags=re.MULTILINE):
            commands.append("make build")
    return commands


def analyze_repository(
    root: str | os.PathLike[str],
    *,
    max_files: int = 4000,
    max_file_bytes: int = 1_500_000,
    collect_files: bool = True,
) -> ProjectAnalysis:
    """Inspect a working tree and return structured facts about it."""
    root_path = Path(root).expanduser().resolve()
    analysis = ProjectAnalysis(root=str(root_path), name=root_path.name or str(root_path))
    if not root_path.is_dir():
        return analysis
    language_lines: Counter[str] = Counter()
    language_files: Counter[str] = Counter()
    top_dirs: Counter[str] = Counter()
    sizes: list[tuple[int, str]] = []
    files_seen = 0
    for path in iter_files(root_path, max_files=max_files, max_file_bytes=max_file_bytes):
        files_seen += 1
        rel = str(path.relative_to(root_path))
        language = LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "Other")
        try:
            text = read_text(path)
        except OSError:
            continue
        lines = _count_lines(text)
        analysis.total_files += 1
        analysis.total_lines += lines
        language_files[language] += 1
        language_lines[language] += lines
        if "/" in rel:
            top_dirs[rel.split("/", 1)[0]] += 1
        is_test = any(pattern.search(rel) for pattern in TEST_PATTERNS)
        if is_test:
            analysis.test_files.append(rel)
        if path.name in ENTRY_HINTS or (path.suffix == ".py" and path.name in ("main.py", "cli.py")):
            analysis.entry_points.append(rel)
        if path.name in DEPENDENCY_FILES:
            analysis.dependency_files.append(rel)
        if path.name.lower().startswith("readme") and not analysis.readme_excerpt:
            analysis.readme_excerpt = text[:1500]
        sizes.append((len(text), rel))
        if collect_files:
            symbols, imports = _extract_symbols(text, language)
            digest = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()
            analysis.files.append(
                FileFacts(
                    path=rel,
                    language=language,
                    size_bytes=len(text.encode("utf-8", errors="replace")),
                    lines=lines,
                    is_test=is_test,
                    symbols=symbols,
                    imports=imports,
                    hash=digest,
                )
            )
    analysis.languages = {lang: count for lang, count in language_files.most_common()}
    analysis.top_directories = dict(top_dirs.most_common(12))
    sizes.sort(reverse=True)
    analysis.largest_files = [{"path": rel, "bytes": size} for size, rel in sizes[:8]]
    analysis.truncated = files_seen >= max_files
    analysis.dependencies = _parse_dependencies(root_path, analysis.dependency_files)
    analysis.test_commands = detect_test_commands(root_path, analysis)
    analysis.build_commands = detect_build_commands(root_path, analysis)
    analysis.git = _git_info(root_path)
    if not analysis.entry_points:
        candidates = [f.path for f in analysis.files if Path(f.path).name in ("__main__.py", "index.js", "app.js")][:5]
        analysis.entry_points = candidates
    log.info(
        "repository analysed",
        cm={
            "root": str(root_path),
            "files": analysis.total_files,
            "language": analysis.primary_language,
            "tests": len(analysis.test_files),
            "test_commands": analysis.test_commands,
        },
    )
    return analysis


class KnowledgeIndex:
    """Persistent per-project index of files, symbols and suites."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # -- projects ---------------------------------------------------------
    def register_project(
        self,
        path: str | os.PathLike[str],
        *,
        name: str | None = None,
        kind: str = "local",
        remote: str = "",
        analysis: ProjectAnalysis | None = None,
    ) -> int:
        resolved = str(Path(path).expanduser().resolve())
        now = time.time()
        existing = self.db.query_one("SELECT id, created_at FROM projects WHERE path = ?", (resolved,))
        payload = {
            "path": resolved,
            "name": name or Path(resolved).name,
            "kind": kind,
            "remote": remote or (analysis.git.get("remote") if analysis else "") or None,
            "language": analysis.primary_language if analysis else None,
            "last_seen": now,
            "summary": json.dumps(analysis.as_dict() if analysis else {}, default=str),
            "stats": json.dumps(
                {
                    "files": analysis.total_files if analysis else 0,
                    "lines": analysis.total_lines if analysis else 0,
                    "tests": len(analysis.test_files) if analysis else 0,
                }
            ),
        }
        if existing:
            self.db.update("projects", payload, "id = ?", (existing["id"],))
            return int(existing["id"])
        payload["created_at"] = now
        return self.db.insert("projects", payload)

    def project(self, project_id: int) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not row:
            return None
        return {
            "id": row["id"],
            "path": row["path"],
            "name": row["name"],
            "kind": row["kind"],
            "remote": row["remote"],
            "language": row["language"],
            "created_at": row["created_at"],
            "last_seen": row["last_seen"],
            "summary": _loads(row["summary"], {}) or {},
            "stats": _loads(row["stats"], {}) or {},
        }

    def project_by_path(self, path: str | os.PathLike[str]) -> dict[str, Any] | None:
        resolved = str(Path(path).expanduser().resolve())
        row = self.db.query_one("SELECT id FROM projects WHERE path = ?", (resolved,))
        return self.project(int(row["id"])) if row else None

    def projects(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT id FROM projects ORDER BY last_seen DESC LIMIT ?", (limit,))
        return [p for p in (self.project(int(row["id"])) for row in rows) if p]

    # -- indexing ---------------------------------------------------------
    def index_project(self, project_id: int, analysis: ProjectAnalysis, *, prune: bool = True) -> dict[str, int]:
        """Write the symbol outline for a project, incrementally (unchanged files are skipped)."""
        now = time.time()
        existing_rows = self.db.query(
            "SELECT path, kind, IFNULL(symbol,'') AS symbol, hash FROM knowledge WHERE project_id = ?",
            (project_id,),
        )
        existing: dict[tuple[str, str, str], str] = {
            (row["path"], row["kind"], row["symbol"]): (row["hash"] or "") for row in existing_rows
        }
        current_hashes: dict[str, str] = {}
        inserted = updated = skipped = 0
        with self.db.transaction() as conn:
            for facts in analysis.files:
                current_hashes[f"{facts.path}:file"] = facts.hash
                key = (facts.path, "file", "")
                previous = existing.get(key)
                if previous == facts.hash:
                    skipped += 1
                else:
                    conn.execute(
                        """
                        INSERT INTO knowledge (project_id, path, kind, symbol, summary, hash, indexed_at)
                        VALUES (?, ?, 'file', '', ?, ?, ?)
                        ON CONFLICT(project_id, path, kind, symbol) DO UPDATE SET
                            summary=excluded.summary, hash=excluded.hash, indexed_at=excluded.indexed_at
                        """,
                        (project_id, facts.path, facts.language, facts.hash, now),
                    )
                    if previous is None:
                        inserted += 1
                    else:
                        updated += 1
                for kind, name, line in facts.symbols:
                    symbol_key = (facts.path, kind, name)
                    if symbol_key in existing and existing[symbol_key] == facts.hash:
                        continue
                    conn.execute(
                        """
                        INSERT INTO knowledge (project_id, path, kind, symbol, line, summary, hash, indexed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(project_id, path, kind, symbol) DO UPDATE SET
                            line=excluded.line, summary=excluded.summary, hash=excluded.hash, indexed_at=excluded.indexed_at
                        """,
                        (project_id, facts.path, kind, name, line, facts.language, facts.hash, now),
                    )
        suites = 0
        for command in analysis.test_commands:
            self.db.upsert(
                "knowledge",
                {
                    "project_id": project_id, "path": "test-suite", "kind": "suite", "symbol": command,
                    "summary": command, "hash": "suite", "indexed_at": now,
                },
                ["project_id", "path", "kind", "symbol"],
            )
            suites += 1
        result = {"inserted": inserted, "updated": updated, "skipped": skipped, "symbols": sum(len(f.symbols) for f in analysis.files), "suites": suites}
        log.info("knowledge indexed", cm={"project_id": project_id, **result})
        return result

    def drop_project(self, project_id: int) -> None:
        self.db.execute("DELETE FROM knowledge WHERE project_id = ?", (project_id,))

    # -- querying ---------------------------------------------------------
    def search(self, query: str, *, project_id: int | None = None, limit: int = 40, kinds: Iterable[str] = ("file", "class", "function", "type", "struct", "const", "suite")) -> list[dict[str, Any]]:
        words = [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query or "")][:8]
        clauses = [f"kind IN ({','.join('?' for _ in kinds)})"]
        params: list[Any] = list(kinds)
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if words:
            like_clause = " OR ".join("(path LIKE ? OR IFNULL(symbol,'') LIKE ?)" for _ in words)
            clauses.append(f"({like_clause})")
            for word in words:
                params.extend([f"%{word}%", f"%{word}%"])
        sql = (
            "SELECT path, kind, symbol, line, summary, indexed_at FROM knowledge "
            f"WHERE {' AND '.join(clauses)} ORDER BY kind = 'file' DESC, path LIMIT ?"
        )
        params.append(limit)
        rows = self.db.query(sql, params)
        return [
            {
                "path": row["path"],
                "kind": row["kind"],
                "symbol": row["symbol"],
                "line": row["line"],
                "language": row["summary"],
                "indexed_at": row["indexed_at"],
            }
            for row in rows
        ]

    def outline(self, project_id: int, limit: int = 300) -> list[dict[str, Any]]:
        rows = self.db.query(
            """
            SELECT path, kind, symbol, line FROM knowledge
            WHERE project_id = ? AND kind IN ('class','function','type','struct','const')
            ORDER BY path, line LIMIT ?
            """,
            (project_id, limit),
        )
        return [dict(row) for row in rows]

    def test_suites(self, project_id: int) -> list[str]:
        rows = self.db.query("SELECT symbol FROM knowledge WHERE project_id = ? AND kind = 'suite'", (project_id,))
        return [row["symbol"] for row in rows if row["symbol"]]

    def stats(self, project_id: int | None = None) -> dict[str, Any]:
        where = "WHERE project_id = ?" if project_id is not None else ""
        params = (project_id,) if project_id is not None else ()
        counters: dict[str, int] = {}
        for row in self.db.query(f"SELECT kind, COUNT(*) AS n FROM knowledge {where} GROUP BY kind", params):
            counters[row["kind"]] = int(row["n"])
        last = self.db.query_one(f"SELECT MAX(indexed_at) AS ts FROM knowledge {where}", params)
        return {
            "files": counters.get("file", 0),
            "symbols": sum(v for k, v in counters.items() if k not in ("file", "suite")),
            "suites": counters.get("suite", 0),
            "by_kind": counters,
            "last_indexed": float(last["ts"]) if last and last["ts"] else None,
        }


# Backwards-friendly alias
ProjectIndex = KnowledgeIndex
