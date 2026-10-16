"""Dataset construction and validation.

A dataset here is a JSONL file of supervised examples plus a validation report. Every example
is checked before it can be used:

* shape — it must contain an instruction and either an output, a chosen action, or messages;
* content — no credentials, no empty outputs, no duplicate instructions;
* quality — a 0..1 score combining length, specificity and (when available) whether the
  originating task was verified.

Nothing is trained on examples that fail validation, and the rejected count is reported
rather than silently dropped.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.storage.db import Database
from codemind.utils.files import atomic_write_text, ensure_dir, read_text
from codemind.utils.redaction import redact_text

log = get_logger("training.datasets")

REQUIRED_HINT = "each example needs 'instruction' plus one of 'output', 'action' or 'messages'"
_SECRET_HINT = re.compile(r"(?i)\b(?:sk-[A-Za-z0-9]{10,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})")


def shorthand_action(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Accept the friendly ``{"instruction": ..., "tool": ...}`` shape as an action.

    Hand-written JSONL from a bug report or a spreadsheet usually names the tool directly rather
    than nesting it under ``action``. Refusing that data would be pedantic; validating it is not:
    the example still has to pass every check below.
    """
    tool = payload.get("tool") or payload.get("tool_name")
    if not isinstance(tool, str) or not tool.strip():
        return None
    action: dict[str, Any] = {"tool": tool.strip()}
    arguments = payload.get("arguments")
    if isinstance(arguments, dict):
        action["arguments"] = arguments
    return action


@dataclass
class Example:
    instruction: str
    output: str = ""
    action: dict[str, Any] | None = None
    context: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    quality: float = 0.5
    source: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"instruction": self.instruction, "quality": round(self.quality, 3), "source": self.source}
        if self.context:
            payload["context"] = self.context
        if self.output:
            payload["output"] = self.output
        if self.action:
            payload["action"] = self.action
        if self.messages:
            payload["messages"] = self.messages
        if self.meta:
            payload["meta"] = self.meta
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, source: str = "") -> "Example":
        return cls(
            instruction=str(payload.get("instruction") or payload.get("prompt") or payload.get("input") or "").strip(),
            output=str(payload.get("output") or payload.get("response") or payload.get("completion") or "").strip(),
            action=payload.get("action") if isinstance(payload.get("action"), dict) else shorthand_action(payload),
            context=str(payload.get("context") or "").strip(),
            messages=[{"role": str(m.get("role")), "content": str(m.get("content"))} for m in payload.get("messages", []) if isinstance(m, dict)],
            quality=float(payload.get("quality", 0.5) or 0.5),
            source=source or str(payload.get("source", "")),
            meta=payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
        )


@dataclass
class ValidationReport:
    total: int = 0
    valid: int = 0
    rejected: int = 0
    duplicates: int = 0
    issues: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "valid": self.valid,
            "rejected": self.rejected,
            "duplicates": self.duplicates,
            "issues": self.issues[:40],
        }


def validate_example(example: Example, seen: set[str]) -> str | None:
    """Return a rejection reason, or None when the example is usable."""
    if not example.instruction or len(example.instruction) < 8:
        return "instruction is empty or too short"
    if not (example.output or example.action or example.messages):
        return REQUIRED_HINT
    if example.output and len(example.output) < 4 and not example.action:
        return "output is too short to be useful"
    fingerprint = example.instruction.lower().strip()[:200]
    if fingerprint in seen:
        return "duplicate instruction"
    if _SECRET_HINT.search(example.instruction) or _SECRET_HINT.search(example.output or "") or _SECRET_HINT.search(json.dumps(example.action or {})):
        return "contains something that looks like a credential"
    for message in example.messages:
        if not message.get("content"):
            return "message with empty content"
    return None


def quality_score(example: Example, *, verified: bool = False, success: bool = True) -> float:
    score = 0.45
    if example.output:
        score += min(0.2, len(example.output) / 4000)
    if example.action:
        score += 0.15
    if example.context:
        score += 0.05
    if verified:
        score += 0.2
    if success:
        score += 0.05
    score += min(0.1, len(example.instruction) / 1200)
    if not verified and not example.action:
        score -= 0.15
    return max(0.0, min(1.0, score))


class DatasetBuilder:
    """Creates datasets on disk and records them in the database."""

    def __init__(self, db: Database, dataset_dir: Path) -> None:
        self.db = db
        self.dir = Path(dataset_dir)
        ensure_dir(self.dir)

    # -- sources ----------------------------------------------------------
    def from_examples(self, name: str, examples: Iterable[Example], *, notes: str = "", split: float = 0.1, seed: int = 7, merge: bool = False) -> dict[str, Any]:
        """Write a validated dataset.

        ``merge=True`` adds the new examples to what the dataset already contains instead of
        replacing it — the safe default whenever the source may include the dataset itself.
        """
        items = list(examples)
        report = ValidationReport()
        seen: set[str] = set()
        kept: list[Example] = []
        target = self.dir / f"{name}.jsonl"
        val_path = self.dir / f"{name}.validation.jsonl"
        if merge:
            for existing in self._load_file(target) + self._load_file(val_path):
                seen.add(existing.instruction.lower().strip()[:200])
                kept.append(existing)
        if not items and kept:
            report.valid = len(kept)
            return {"name": name, "path": str(target), "validation_path": str(val_path), "examples": len(self._load_file(target)), "validation_examples": len(self._load_file(val_path)), "report": report.as_dict(), "unchanged": True}
        for example in items:
            report.total += 1
            example.instruction = redact_text(example.instruction)
            example.output = redact_text(example.output)
            reason = validate_example(example, seen)
            if reason:
                if reason == "duplicate instruction":
                    report.duplicates += 1
                else:
                    report.rejected += 1
                    if len(report.issues) < 40:
                        report.issues.append(f"{reason}: {example.instruction[:60]}")
                continue
            seen.add(example.instruction.lower().strip()[:200])
            kept.append(example)
        report.valid = len(kept)
        if not kept and not merge and (target.exists() or val_path.exists()):
            # never let a bad import wipe a dataset that already has content
            existing = len(self._load_file(target))
            report.issues.append(
                f"no usable examples in this import; the existing dataset was left untouched ({existing} example(s) kept)"
            )
            return {
                "name": name,
                "path": str(target),
                "validation_path": str(val_path),
                "examples": existing,
                "validation_examples": len(self._load_file(val_path)),
                "report": report.as_dict(),
                "unchanged": True,
            }
        random.Random(seed).shuffle(kept)
        cut = max(1, int(len(kept) * split)) if len(kept) > 1 else 0
        validation, train = kept[:cut], kept[cut:]
        atomic_write_text(target, "\n".join(json.dumps(example.as_dict(), default=str) for example in train) + ("\n" if train else ""))
        atomic_write_text(val_path, "\n".join(json.dumps(example.as_dict(), default=str) for example in validation) + ("\n" if validation else ""))
        record = {
            "name": name,
            "path": str(target),
            "kind": "instruction",
            "examples": len(train),
            "validated": report.valid,
            "rejected": report.rejected + report.duplicates,
            "created_at": time.time(),
            "updated_at": time.time(),
            "notes": notes or f"validation: {report.valid} usable of {report.total}",
        }
        self.db.upsert("datasets", record, ["name"])
        BUS.publish(Topics.MEMORY_INDEX, dataset=name, examples=len(train), rejected=record["rejected"])
        log.info("dataset written", cm={"name": name, "train": len(train), "validation": len(validation), "rejected": record["rejected"]})
        return {
            "name": name,
            "path": str(target),
            "validation_path": str(val_path),
            "examples": len(train),
            "validation_examples": len(validation),
            "report": report.as_dict(),
        }

    def from_files(self, name: str, paths: Sequence[str | Path], *, notes: str = "", merge: bool = False) -> dict[str, Any]:
        """Import user JSONL/JSON/CSV files, validating each row."""
        examples: list[Example] = []
        target = (self.dir / f"{name}.jsonl").resolve()
        validation = (self.dir / f"{name}.validation.jsonl").resolve()
        skipped: list[str] = []
        used = 0
        for raw in paths:
            path = Path(raw).expanduser()
            if not path.exists():
                continue
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if resolved in (target, validation):
                # the file *is* this dataset; re-reading it as a new source would truncate it
                skipped.append(path.name)
                merge = True
                continue
            for payload in _read_records(path):
                examples.append(Example.from_dict(payload, source=str(path.name)))
            used += 1
        if skipped and not examples:
            existing = len(self._load_file(target))
            return {
                "name": name,
                "path": str(target),
                "validation_path": str(validation),
                "examples": existing,
                "validation_examples": len(self._load_file(validation)),
                "report": ValidationReport(total=existing, valid=existing).as_dict(),
                "unchanged": True,
                "note": f"{', '.join(skipped)} already belongs to this dataset; nothing to import",
            }
        return self.from_examples(name, examples, notes=notes or f"imported from {used} file(s)", merge=merge)

    def _load_file(self, path: Path) -> list[Example]:
        if not path.exists():
            return []
        try:
            return [Example.from_dict(payload) for payload in _read_records(path)]
        except Exception:
            return []

    def _dataset_validation_path(self, path: str | Path) -> str:
        """Companion validation file for a dataset (``<name>.validation.jsonl``)."""
        candidate = Path(path)
        return str(candidate.with_name(f"{candidate.stem}.validation{candidate.suffix}"))

    def load(self, path: str | Path) -> list[Example]:
        return [Example.from_dict(payload) for payload in _read_records(Path(path))]

    def list_datasets(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM datasets ORDER BY updated_at DESC")
        out = []
        for row in rows:
            item = dict(row)
            item["exists"] = Path(row["path"]).exists()
            item["bytes"] = Path(row["path"]).stat().st_size if item["exists"] else 0
            out.append(item)
        return out

    def delete(self, name: str) -> bool:
        row = self.db.query_one("SELECT path FROM datasets WHERE name = ?", (name,))
        if not row:
            return False
        for suffix in ("", ".validation"):
            candidate = Path(row["path"])
            target = candidate.with_name(candidate.stem + suffix + candidate.suffix)
            target.unlink(missing_ok=True)
        self.db.execute("DELETE FROM datasets WHERE name = ?", (name,))
        return True

    def stats(self) -> dict[str, Any]:
        rows = self.list_datasets()
        return {
            "datasets": len(rows),
            "examples": sum(int(row.get("examples") or 0) for row in rows),
            "rejected": sum(int(row.get("rejected") or 0) for row in rows),
            "bytes": sum(int(row.get("bytes") or 0) for row in rows),
            "items": rows[:40],
        }


def _read_records(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    text = read_text(path)
    if path.suffix == ".jsonl":
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload
    elif path.suffix == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    yield item
        elif isinstance(payload, dict):
            for item in payload.get("examples", [payload]):
                if isinstance(item, dict):
                    yield item
    elif path.suffix in (".txt", ".md"):
        blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
        for block in blocks:
            if "->" in block:
                instruction, _, output = block.partition("->")
                yield {"instruction": instruction.strip(), "output": output.strip()}
    else:
        # CSV with instruction,output columns
        lines = text.splitlines()
        if not lines:
            return
        header = [h.strip().lower() for h in lines[0].split(",")]
        if "instruction" in header:
            for line in lines[1:]:
                parts = line.split(",")
                if len(parts) >= 2:
                    yield {"instruction": parts[0].strip(), "output": ",".join(parts[1:]).strip()}
