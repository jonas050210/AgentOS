"""Structured logging: JSONL on disk, bounded ring buffer in memory, live event stream.

Every log record is redacted before it leaves the process, carries a monotonically
increasing sequence number, and is mirrored onto the event bus so the Activity feed in the
GUI is the same data that is written to disk — there is no separate "UI log".
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codemind.events import BUS, Topics
from codemind.utils.files import atomic_write_text, ensure_dir
from codemind.utils.redaction import scrub_mapping

_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}

_RING: deque[dict[str, Any]] = deque(maxlen=2000)
_SEQ = 0
_LOCK = threading.RLock()
_HANDLER: "JsonlHandler | None" = None
_CONSOLE_ENABLED = False


class JsonlHandler(logging.Handler):
    """Writes one JSON object per line, rotating at ``max_bytes``."""

    def __init__(self, path: Path, max_bytes: int = 8 * 1024 * 1024, backups: int = 3) -> None:
        super().__init__()
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backups = backups
        ensure_dir(self.path.parent)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = record_to_dict(record)
            line = json.dumps(payload, ensure_ascii=False, default=str)
            with _LOCK:
                if self.path.exists() and self.path.stat().st_size + len(line) + 1 > self.max_bytes:
                    self._rotate()
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except Exception:  # pragma: no cover - logging must never raise
            pass

    def _rotate(self) -> None:
        for index in range(self.backups - 1, 0, -1):
            older = self.path.with_suffix(self.path.suffix + f".{index}")
            newer = self.path.with_suffix(self.path.suffix + f".{index + 1}") if index + 1 <= self.backups else None
            if older.exists():
                if newer is None:
                    older.unlink(missing_ok=True)
                else:
                    newer.unlink(missing_ok=True)
                    older.rename(newer)
        if self.path.exists():
            rotated = self.path.with_suffix(self.path.suffix + ".1")
            rotated.unlink(missing_ok=True)
            self.path.rename(rotated)


def record_to_dict(record: logging.LogRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
        "epoch": record.created,
        "level": record.levelname.lower(),
        "logger": record.name,
        "message": record.getMessage(),
        "module": f"{record.module}:{record.lineno}",
    }
    if record.exc_info:
        payload["exception"] = logging.Formatter().formatException(record.exc_info)
    extra = getattr(record, "cm_extra", None)
    if isinstance(extra, dict):
        payload.update({k: v for k, v in extra.items() if not k.startswith("_")})
    return scrub_mapping(payload)  # type: ignore[return-value]


class RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _SEQ
        try:
            payload = record_to_dict(record)
            with _LOCK:
                _SEQ += 1
                payload["seq"] = _SEQ
                _RING.append(payload)
            BUS.publish(Topics.LOG, **{k: v for k, v in payload.items()})
            if _CONSOLE_ENABLED and record.levelno >= logging.INFO:
                sys.stderr.write(f"[{payload['level']:>7}] {payload['logger']}: {payload['message']}\n")
        except Exception:  # pragma: no cover
            pass


def configure_logging(
    log_dir: Path,
    level: str = "info",
    console: bool = False,
    keep_days: int = 14,
) -> Path:
    """Install handlers and return the active log file path."""
    global _HANDLER, _CONSOLE_ENABLED
    ensure_dir(log_dir)
    path = Path(log_dir) / "codemind.jsonl"
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = JsonlHandler(path)
    handler.setLevel(_LEVELS.get(level, logging.INFO))
    root.addHandler(handler)
    root.addHandler(RingHandler())
    root.setLevel(logging.DEBUG)
    _HANDLER = handler
    _CONSOLE_ENABLED = console
    prune_logs(log_dir, keep_days)
    return path


def set_level(level: str) -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, JsonlHandler):
            handler.setLevel(_LEVELS.get(level, logging.INFO))
    root.setLevel(logging.DEBUG)


def set_console(enabled: bool) -> None:
    global _CONSOLE_ENABLED
    _CONSOLE_ENABLED = enabled


def prune_logs(log_dir: Path, keep_days: int) -> list[str]:
    """Delete rotated log files older than ``keep_days``; returns removed names."""
    removed: list[str] = []
    cutoff = time.time() - keep_days * 86400
    try:
        for entry in Path(log_dir).glob("*.jsonl.*"):
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    removed.append(entry.name)
            except OSError:
                continue
    except OSError:
        pass
    return removed


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name if name.startswith("codemind") else f"codemind.{name}")

    if not hasattr(logger, "_cm_extras_installed"):
        original = logger._log  # type: ignore[attr-defined]

        def _log(level, msg, args, exc_info=None, extra=None, stack_info=False, stacklevel=1, **kwargs):  # type: ignore[no-untyped-def]
            """Accept ``log.info("message", cm={...})`` and forward the fields as structured data."""
            combined = dict(extra or {})
            field_map = kwargs.pop("cm", None)
            if field_map is None:
                field_map = combined.pop("cm", None)
            if isinstance(field_map, dict):
                combined["cm_extra"] = field_map
            if kwargs:
                combined.setdefault("cm_extra", {})
                if isinstance(combined["cm_extra"], dict):
                    combined["cm_extra"].update(kwargs)
            original(level, msg, args, exc_info=exc_info, extra=combined or None, stack_info=stack_info, stacklevel=stacklevel + 1)

        logger._log = _log  # type: ignore[attr-defined]
        logger._cm_extras_installed = True  # type: ignore[attr-defined]
    return logger


def log_event(level: str, logger_name: str, message: str, **fields: Any) -> None:
    get_logger(logger_name).log(_LEVELS.get(level, logging.INFO), message, extra={"cm": fields})


def recent(limit: int = 200, level: str | None = None, contains: str | None = None) -> list[dict[str, Any]]:
    """Read the in-memory ring buffer, newest first."""
    with _LOCK:
        items = list(_RING)
    if level:
        allowed = {level} if level != "warning" else {"warning", "warn"}
        items = [item for item in items if item["level"] in allowed]
    if contains:
        needle = contains.lower()
        items = [item for item in items if needle in item["message"].lower() or needle in item.get("logger", "").lower()]
    return list(reversed(items))[:limit]


def counters() -> dict[str, int]:
    with _LOCK:
        items = list(_RING)
    out = {"debug": 0, "info": 0, "warning": 0, "error": 0, "critical": 0}
    for item in items:
        level = item["level"]
        out[level] = out.get(level, 0) + 1
    return out


def read_log_file(path: Path, tail: int = 500) -> list[dict[str, Any]]:
    """Read the tail of a JSONL log file (used when the GUI restarts)."""
    if not Path(path).exists():
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-tail:]
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def export_log(lines: list[dict[str, Any]], path: Path) -> Path:
    return atomic_write_text(path, "\n".join(json.dumps(line, default=str) for line in lines) + "\n", mode=0o600)


def environment_summary() -> dict[str, Any]:
    return {
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": sys.platform,
        "threads": threading.active_count(),
    }
