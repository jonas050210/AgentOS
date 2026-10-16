"""SQLite persistence layer.

One database file holds everything that must survive a restart: tasks and their steps,
execution history, memories, knowledge, training runs, checkpoints, evaluations, model
versions, approvals, GitHub actions and resource samples.

Concurrency: WAL journalling, one connection per thread, ``busy_timeout`` set, and a
process-wide lock around write transactions. That is more than enough for a desktop agent
and keeps the storage layer free of an ORM dependency.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from codemind.logging_setup import get_logger
from codemind.utils.files import ensure_dir

log = get_logger("storage.db")

SCHEMA: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'local',
        remote TEXT,
        language TEXT,
        created_at REAL NOT NULL,
        last_seen REAL NOT NULL,
        summary TEXT,
        stats TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        project_id INTEGER,
        title TEXT NOT NULL,
        prompt TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'assisted',
        origin TEXT NOT NULL DEFAULT 'user',
        status TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 5,
        created_at REAL NOT NULL,
        started_at REAL,
        finished_at REAL,
        workdir TEXT,
        plan TEXT,
        result TEXT,
        summary TEXT,
        error TEXT,
        approved INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        tool_calls INTEGER NOT NULL DEFAULT 0,
        tokens INTEGER NOT NULL DEFAULT 0,
        verify TEXT,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, priority, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id, created_at);",
    """
    CREATE TABLE IF NOT EXISTS task_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        idx INTEGER NOT NULL,
        title TEXT NOT NULL,
        detail TEXT,
        status TEXT NOT NULL,
        tool TEXT,
        started_at REAL,
        finished_at REAL,
        attempts INTEGER NOT NULL DEFAULT 0,
        result TEXT,
        error TEXT,
        evidence TEXT,
        UNIQUE(task_id, idx)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_steps_task ON task_steps(task_id, idx);",
    """
    CREATE TABLE IF NOT EXISTS task_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        ts REAL NOT NULL,
        kind TEXT NOT NULL,
        message TEXT,
        payload TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_events ON task_events(task_id, ts);",
    """
    CREATE TABLE IF NOT EXISTS task_diffs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        ts REAL NOT NULL,
        path TEXT NOT NULL,
        kind TEXT NOT NULL,
        diff TEXT,
        added INTEGER NOT NULL DEFAULT 0,
        removed INTEGER NOT NULL DEFAULT 0
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_diffs_task ON task_diffs(task_id, ts);",
    """
    CREATE TABLE IF NOT EXISTS approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT,
        ts REAL NOT NULL,
        action TEXT NOT NULL,
        risk TEXT NOT NULL,
        detail TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        resolved_at REAL,
        reason TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        project_id INTEGER,
        key TEXT,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        tags TEXT,
        quality REAL NOT NULL DEFAULT 0.5,
        source TEXT,
        evidence TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        use_count INTEGER NOT NULL DEFAULT 0,
        last_used REAL,
        pinned INTEGER NOT NULL DEFAULT 0
    );
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_key ON memories(kind, IFNULL(project_id, -1), IFNULL(key, ''));",
    "CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind, updated_at);",
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        title, body, tags, content='memories', content_rowid='id', tokenize='porter unicode61'
    );
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, title, body, tags) VALUES (new.id, new.title, new.body, IFNULL(new.tags, ''));
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, title, body, tags) VALUES ('delete', old.id, old.title, old.body, IFNULL(old.tags, ''));
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, title, body, tags) VALUES ('delete', old.id, old.title, old.body, IFNULL(old.tags, ''));
        INSERT INTO memories_fts(rowid, title, body, tags) VALUES (new.id, new.title, new.body, IFNULL(new.tags, ''));
    END;
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        path TEXT NOT NULL,
        kind TEXT NOT NULL,
        symbol TEXT NOT NULL DEFAULT '',
        line INTEGER,
        summary TEXT,
        hash TEXT,
        indexed_at REAL NOT NULL,
        UNIQUE(project_id, path, kind, symbol)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_knowledge_project ON knowledge(project_id, kind);",
    """
    CREATE TABLE IF NOT EXISTS executions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT,
        ts REAL NOT NULL,
        argv TEXT NOT NULL,
        cwd TEXT,
        exit_code INTEGER,
        duration REAL,
        stdout_tail TEXT,
        stderr_tail TEXT,
        policy TEXT,
        sandbox TEXT,
        timed_out INTEGER NOT NULL DEFAULT 0
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_executions_ts ON executions(ts DESC);",
    """
    CREATE TABLE IF NOT EXISTS training_runs (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        method TEXT NOT NULL,
        base_model TEXT,
        dataset TEXT,
        status TEXT NOT NULL,
        created_at REAL NOT NULL,
        started_at REAL,
        finished_at REAL,
        config TEXT,
        progress TEXT,
        metrics TEXT,
        artifacts TEXT,
        checkpoint TEXT,
        log_path TEXT,
        pid INTEGER,
        priority TEXT NOT NULL DEFAULT 'low',
        error TEXT,
        resumed_from TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_status ON training_runs(status, created_at DESC);",
    """
    CREATE TABLE IF NOT EXISTS training_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        ts REAL NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_training_events ON training_events(run_id, ts);",
    """
    CREATE TABLE IF NOT EXISTS checkpoints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        step INTEGER NOT NULL,
        path TEXT NOT NULL,
        loss REAL,
        created_at REAL NOT NULL,
        metrics TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_checkpoints ON checkpoints(run_id, step);",
    """
    CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        model_version TEXT,
        suite TEXT NOT NULL,
        created_at REAL NOT NULL,
        score REAL,
        baseline_score REAL,
        decision TEXT,
        reason TEXT,
        promoted INTEGER NOT NULL DEFAULT 0,
        results TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_evaluations ON evaluations(suite, created_at DESC);",
    """
    CREATE TABLE IF NOT EXISTS model_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id TEXT NOT NULL,
        version TEXT NOT NULL,
        base_model TEXT,
        path TEXT,
        kind TEXT NOT NULL DEFAULT 'adapter',
        status TEXT NOT NULL DEFAULT 'candidate',
        created_at REAL NOT NULL,
        metrics TEXT,
        notes TEXT,
        UNIQUE(model_id, version)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS datasets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        path TEXT NOT NULL,
        kind TEXT NOT NULL,
        examples INTEGER NOT NULL DEFAULT 0,
        validated INTEGER NOT NULL DEFAULT 0,
        rejected INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        notes TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT,
        ts REAL NOT NULL,
        tool TEXT NOT NULL,
        args TEXT,
        ok INTEGER NOT NULL,
        duration REAL,
        summary TEXT,
        risk TEXT,
        approved INTEGER NOT NULL DEFAULT 1
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tool_calls ON tool_calls(ts DESC);",
    """
    CREATE TABLE IF NOT EXISTS github_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        repo TEXT,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        payload TEXT,
        result TEXT,
        task_id TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        category TEXT NOT NULL,
        project_id INTEGER,
        title TEXT NOT NULL,
        body TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        task_id TEXT,
        resolved_at REAL,
        impact TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS resource_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        cpu_percent REAL,
        ram_used_mb REAL,
        ram_total_mb REAL,
        gpu_util REAL,
        vram_used_mb REAL,
        vram_total_mb REAL,
        disk_free_gb REAL,
        source TEXT,
        note TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_samples_ts ON resource_samples(ts DESC);",
    """
    CREATE TABLE IF NOT EXISTS model_catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        provider TEXT NOT NULL,
        kind TEXT,
        size_bytes INTEGER,
        parameters TEXT,
        quantization TEXT,
        context INTEGER,
        path TEXT,
        status TEXT,
        compatibility TEXT,
        updated_at REAL NOT NULL
    );
    """,
]


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _loads(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class Database:
    """Thread-safe SQLite wrapper with schema migrations."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        ensure_dir(self.path.parent)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._connections = 0
        self._init_lock = threading.Lock()
        self._initialize()

    # -- connection management -------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30.0, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @property
    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
            with self._init_lock:
                self._connections += 1
        return conn

    def _initialize(self) -> None:
        with self._write_lock:
            conn = self.connection
            for statement in SCHEMA:
                conn.execute(statement)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self._migrate(conn, version)

    def _migrate(self, conn: sqlite3.Connection, version: int) -> None:
        if version < 1:
            conn.execute("PRAGMA user_version=1")
            version = 1
        if version < 2:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(training_runs)")}
            if "resumed_from" not in columns:
                conn.execute("ALTER TABLE training_runs ADD COLUMN resumed_from TEXT")
            conn.execute("PRAGMA user_version=2")
            version = 2
        if version < 3:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON resource_samples(ts DESC)")
            conn.execute("PRAGMA user_version=3")

    # -- primitives -------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> sqlite3.Cursor:
        with self._write_lock:
            return self.connection.execute(sql, params)

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        with self._write_lock:
            return self.connection.executemany(sql, seq)

    def query(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return default
        value = row[0]
        return default if value is None else value

    def insert(self, table: str, values: Mapping[str, Any]) -> int:
        keys = list(values.keys())
        placeholders = ", ".join("?" for _ in keys)
        sql = f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})"
        with self._write_lock:
            cursor = self.connection.execute(sql, [self._adapt(values[k]) for k in keys])
            return int(cursor.lastrowid or 0)

    def update(self, table: str, values: Mapping[str, Any], where: str, params: Sequence[Any] = ()) -> int:
        keys = list(values.keys())
        assignments = ", ".join(f"{k}=?" for k in keys)
        sql = f"UPDATE {table} SET {assignments} WHERE {where}"
        with self._write_lock:
            cursor = self.connection.execute(sql, [self._adapt(values[k]) for k in keys] + list(params))
            return cursor.rowcount

    def upsert(self, table: str, values: Mapping[str, Any], conflict: Sequence[str]) -> int:
        keys = list(values.keys())
        placeholders = ", ".join("?" for _ in keys)
        updates = ", ".join(f"{k}=excluded.{k}" for k in keys if k not in conflict)
        sql = (
            f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders}) "
            f"ON CONFLICT({', '.join(conflict)}) DO UPDATE SET {updates}"
        )
        with self._write_lock:
            cursor = self.connection.execute(sql, [self._adapt(values[k]) for k in keys])
            return int(cursor.lastrowid or 0)

    @staticmethod
    def _adapt(value: Any) -> Any:
        if isinstance(value, (dict, list, tuple)):
            return _dumps(value)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, Path):
            return str(value)
        return value

    def transaction(self) -> "_Transaction":
        return _Transaction(self)

    # -- maintenance ------------------------------------------------------
    def prune(self, *, executions_days: int = 30, samples_minutes: int = 240, events_days: int = 14) -> dict[str, int]:
        now = time.time()
        removed: dict[str, int] = {}
        with self._write_lock:
            removed["executions"] = self.connection.execute(
                "DELETE FROM executions WHERE ts < ?", (now - executions_days * 86400,)
            ).rowcount
            removed["resource_samples"] = self.connection.execute(
                "DELETE FROM resource_samples WHERE ts < ?", (now - samples_minutes * 60,)
            ).rowcount
            removed["task_events"] = self.connection.execute(
                "DELETE FROM task_events WHERE ts < ?", (now - events_days * 86400,)
            ).rowcount
            removed["training_events"] = self.connection.execute(
                "DELETE FROM training_events WHERE ts < ?", (now - events_days * 86400,)
            ).rowcount
        return removed

    def vacuum(self) -> None:
        with self._write_lock:
            self.connection.execute("VACUUM")

    def integrity_check(self) -> str:
        row = self.query_one("PRAGMA integrity_check")
        return str(row[0]) if row else "unknown"

    def stats(self) -> dict[str, Any]:
        tables = [
            "projects", "tasks", "task_steps", "task_events", "task_diffs", "approvals", "memories",
            "knowledge", "executions", "training_runs", "checkpoints", "evaluations", "model_versions",
            "datasets", "tool_calls", "github_actions", "proposals", "resource_samples", "model_catalog",
        ]
        counts: dict[str, int] = {}
        for table in tables:
            try:
                counts[table] = int(self.scalar(f"SELECT COUNT(*) FROM {table}", default=0) or 0)
            except sqlite3.Error:
                counts[table] = -1
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        return {
            "path": str(self.path),
            "size_bytes": size,
            "page_count": int(self.scalar("PRAGMA page_count", default=0) or 0),
            "journal_mode": str(self.scalar("PRAGMA journal_mode", default="?")),
            "tables": counts,
            "integrity": self.integrity_check(),
        }

    def set_meta(self, key: str, value: Any) -> None:
        self.upsert("meta", {"key": key, "value": _dumps(value)}, ["key"])

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM meta WHERE key = ?", (key,))
        return _loads(row["value"], default) if row else default

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None


class _Transaction:
    def __init__(self, db: Database) -> None:
        self.db = db

    def __enter__(self) -> sqlite3.Connection:
        self.db._write_lock.acquire()
        conn = self.db.connection
        conn.execute("BEGIN IMMEDIATE")
        return conn

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        conn = self.db.connection
        try:
            if exc_type is None:
                conn.execute("COMMIT")
            else:
                conn.execute("ROLLBACK")
        finally:
            self.db._write_lock.release()
        return None


def json_field(row: sqlite3.Row | Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        value = row[key]  # type: ignore[index]
    except (KeyError, IndexError):
        return default
    return _loads(value, default)


__all__ = ["Database", "json_field", "_loads", "_dumps"]
