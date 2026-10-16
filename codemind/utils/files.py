"""Filesystem helpers: atomic writes, safe paths, size formatting and diffs."""

from __future__ import annotations

import errno
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

_TEXT_SUFFIXES = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".md", ".rst", ".txt", ".sh", ".bash", ".zsh", ".fish", ".ps1",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".swift",
    ".sql", ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte", ".xml", ".csv",
    ".tsv", ".env", ".gitignore", ".dockerfile", ".gradle", ".cmake", ".lua", ".pl", ".r", ".m", ".mm",
}

_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".tgz",
    ".bz2", ".xz", ".7z", ".rar", ".so", ".o", ".a", ".dylib", ".dll", ".exe", ".bin", ".pt", ".pth",
    ".safetensors", ".gguf", ".onnx", ".mp3", ".mp4", ".wav", ".ogg", ".woff", ".woff2", ".ttf", ".otf",
}


def human_bytes(num: float | int | None) -> str:
    if num is None:
        return "—"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024 or unit == "PB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = max(0.0, float(seconds))
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, sec = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours:02d}h"


def human_rate(per_second: float | None, unit: str = "/s") -> str:
    if per_second is None:
        return "—"
    if per_second >= 1000:
        return f"{per_second:,.0f}{unit}"
    if per_second >= 10:
        return f"{per_second:.1f}{unit}"
    return f"{per_second:.2f}{unit}"


def ensure_dir(path: str | os.PathLike[str]) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def atomic_write_text(path: str | os.PathLike[str], text: str, mode: int = 0o600) -> Path:
    target = Path(path)
    ensure_dir(target.parent)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def atomic_write_json(path: str | os.PathLike[str], payload: Any, mode: int = 0o600) -> Path:
    return atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=False, default=str) + "\n", mode=mode)


def read_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return default


def is_probably_binary(path: str | os.PathLike[str]) -> bool:
    p = Path(path)
    if p.suffix.lower() in _BINARY_SUFFIXES:
        return True
    if p.suffix.lower() in _TEXT_SUFFIXES:
        return False
    try:
        with open(p, "rb") as handle:
            chunk = handle.read(4096)
    except OSError:
        return True
    if b"\x00" in chunk:
        return True
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return False  # latin-1-ish text is still readable enough for our purposes
    return False


def safe_relpath(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> str:
    """Return ``path`` relative to ``root``; raise ValueError when it escapes the root."""
    root_path = Path(root).resolve()
    try:
        target = Path(path).resolve()
    except OSError as exc:  # pragma: no cover - defensive
        raise ValueError(f"cannot resolve path {path!r}: {exc}") from exc
    try:
        return str(target.relative_to(root_path))
    except ValueError as exc:
        raise ValueError(f"{target} is outside the allowed root {root_path}") from exc


def resolve_within(root: str | os.PathLike[str], candidate: str | os.PathLike[str]) -> Path:
    """Resolve ``candidate`` under ``root``, rejecting traversal and absolute escapes."""
    root_path = Path(root).resolve()
    raw = Path(candidate)
    if raw.is_absolute():
        target = raw.resolve()
    else:
        target = (root_path / raw).resolve()
    if target != root_path and root_path not in target.parents:
        raise ValueError(f"path {candidate!r} escapes workspace root {root_path}")
    return target


def iter_files(
    root: str | os.PathLike[str],
    *,
    ignore_dirs: Iterable[str] = (".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache", "target", ".next", ".tox"),
    max_files: int = 20000,
    max_file_bytes: int = 2_000_000,
) -> Iterable[Path]:
    """Yield readable source files under ``root`` (skipping VCS/deps/build directories)."""
    ignored = {d for d in ignore_dirs}
    root_path = Path(root)
    count = 0
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(d for d in dirnames if d not in ignored and not d.startswith(".git"))
        for name in sorted(filenames):
            if count >= max_files:
                return
            path = Path(dirpath) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > max_file_bytes or stat.st_size == 0:
                continue
            if is_probably_binary(path):
                continue
            count += 1
            yield path


def read_text(path: str | os.PathLike[str], limit: int | None = None) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        if limit is None:
            return handle.read()
        return handle.read(limit)


def disk_usage(path: str | os.PathLike[str]) -> tuple[int, int, int] | None:
    try:
        usage = shutil.disk_usage(str(path))
    except OSError:
        return None
    return usage.total, usage.used, usage.free


def free_space(path: str | os.PathLike[str]) -> int:
    usage = disk_usage(path)
    return usage[2] if usage else 0


def copy_tree(src: str | os.PathLike[str], dst: str | os.PathLike[str], ignore_names: Iterable[str] = (".git", "node_modules", "__pycache__")) -> int:
    """Copy a directory tree, returning the number of files copied."""
    ignored = set(ignore_names)
    count = 0
    src_path, dst_path = Path(src), Path(dst)
    for dirpath, dirnames, filenames in os.walk(src_path):
        dirnames[:] = [d for d in dirnames if d not in ignored]
        rel = Path(dirpath).relative_to(src_path)
        target_dir = dst_path / rel
        ensure_dir(target_dir)
        for name in filenames:
            source = Path(dirpath) / name
            if source.is_symlink():
                continue
            try:
                shutil.copy2(source, target_dir / name)
                count += 1
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EPERM, errno.ENOENT):
                    raise
    return count
