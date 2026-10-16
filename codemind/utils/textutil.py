"""Text helpers: ANSI stripping, truncation, diff-friendly quoting."""

from __future__ import annotations

import difflib
import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences and stray control characters from command output."""
    if not text:
        return text
    return _CONTROL_RE.sub("", _ANSI_RE.sub("", text))


def shorten(text: str | None, limit: int = 120) -> str:
    """Collapse whitespace and trim to ``limit``. ``None`` becomes an empty string."""
    text = "" if text is None else str(text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def truncate_middle(text: str, limit: int = 4000, marker: str = "\n… [{dropped} characters omitted] …\n") -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    dropped = len(text) - limit
    return text[:head] + marker.format(dropped=dropped) + text[-tail:]


def tail_lines(text: str, lines: int = 50) -> str:
    if not text:
        return ""
    parts = text.splitlines()
    return "\n".join(parts[-lines:])


def unified_diff(old: str, new: str, path: str = "file", context: int = 3) -> str:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=context,
    )
    out = "".join(diff)
    if old and not old.endswith("\n"):
        out += "\n\\ No newline at end of file\n"
    return out


def diff_stats(diff_text: str) -> dict[str, int]:
    added = removed = 0
    files = set()
    for line in diff_text.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            if line.startswith("+++ "):
                files.add(line[4:].strip())
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"added": added, "removed": removed, "files": len(files)}


def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or "item")[:limit].strip("-")


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    word = singular if count == 1 else (plural_form or singular + "s")
    return f"{count} {word}"


def indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line if line.strip() else line for line in text.splitlines())
