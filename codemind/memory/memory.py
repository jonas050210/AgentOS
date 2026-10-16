"""Durable memory store.

What is stored, and why each kind exists:

===================  ==========================================================================
kind                 meaning
===================  ==========================================================================
``architecture``     how a project is put together - inspected facts, not guesses
``preference``       how you like to work (style, tooling, review depth)
``solution``         a verified fix: the problem, the change, the evidence that it worked
``mistake``          something that failed and the lesson learned, so it is not repeated
``task_summary``     what a finished task did, for later reference
``knowledge``        general technical facts worth recalling (APIs, commands, gotchas)
``project_fact``     a concrete, checkable fact about a repository
``note``             free-form note
===================  ==========================================================================

Retrieval ranks by SQLite FTS5 bm25 relevance, then adjusts for quality, recency and how
often an entry has already been useful. Entries below ``memory.min_quality`` stay visible in
the UI but are never injected into a prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.storage.db import Database, _loads

log = get_logger("memory.store")

MEMORY_KINDS = (
    "architecture",
    "preference",
    "solution",
    "mistake",
    "task_summary",
    "knowledge",
    "project_fact",
    "note",
)

KIND_LABELS = {
    "architecture": "Architecture",
    "preference": "Preference",
    "solution": "Verified solution",
    "mistake": "Mistake & lesson",
    "task_summary": "Task summary",
    "knowledge": "Knowledge",
    "project_fact": "Project fact",
    "note": "Note",
}

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


@dataclass
class MemoryEntry:
    id: int
    kind: str
    title: str
    body: str
    tags: list[str] = field(default_factory=list)
    quality: float = 0.5
    project_id: int | None = None
    project_name: str | None = None
    key: str | None = None
    source: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    use_count: int = 0
    last_used: float | None = None
    pinned: bool = False
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "kind_label": KIND_LABELS.get(self.kind, self.kind),
            "title": self.title,
            "body": self.body,
            "tags": list(self.tags),
            "quality": round(self.quality, 3),
            "project_id": self.project_id,
            "project_name": self.project_name,
            "key": self.key,
            "source": self.source,
            "evidence": self.evidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "use_count": self.use_count,
            "last_used": self.last_used,
            "pinned": self.pinned,
            "score": round(self.score, 4),
        }

    def prompt_line(self) -> str:
        head = KIND_LABELS.get(self.kind, self.kind)
        tags = f" [{', '.join(self.tags)}]" if self.tags else ""
        return f"- ({head}){tags} {self.title}: {self.body}"

    def to_prompt(self, body_limit: int = 700) -> str:
        body = self.body if len(self.body) <= body_limit else self.body[: body_limit - 1] + "…"
        return self.prompt_line().replace(self.body, body)


def _tags_to_text(tags: Iterable[str]) -> str:
    return " ".join(f"#{tag.strip().lstrip('#')}" for tag in tags if str(tag).strip())


def _text_to_tags(value: Any) -> list[str]:
    if not value:
        return []
    return [tag.lstrip("#") for tag in str(value).split() if tag]


def _fts_query(text: str) -> str:
    words = [match.group(0) for match in _WORD_RE.finditer(text or "")][:24]
    if not words:
        return ""
    return " OR ".join(sorted(set(words)))


class MemoryStore:
    """SQLite-backed memory with full-text retrieval."""

    def __init__(self, db: Database, min_quality: float = 0.35, learn_from_failures: bool = True) -> None:
        self.db = db
        self.min_quality = min_quality
        self.learn_from_failures = learn_from_failures

    # -- writing ----------------------------------------------------------
    def remember(
        self,
        kind: str,
        title: str,
        body: str,
        *,
        tags: Sequence[str] = (),
        quality: float = 0.5,
        project_id: int | None = None,
        key: str | None = None,
        source: str = "",
        evidence: dict[str, Any] | None = None,
        pinned: bool = False,
        dedupe: bool = True,
    ) -> int | None:
        """Store (or update) one memory. Returns the row id, or None when rejected."""
        kind = (kind or "note").strip()
        if kind not in MEMORY_KINDS:
            kind = "note"
        title = " ".join((title or "").split())[:300]
        body = (body or "").strip()[:20000]
        if not title and not body:
            return None
        if not title:
            title = " ".join(body.split()[:12])
        quality = max(0.0, min(1.0, float(quality)))
        tags = [str(tag).strip().lstrip("#") for tag in tags if str(tag).strip()][:16]
        if key is None and dedupe:
            key = self._content_key(kind, title, project_id)
        now = time.time()
        payload = {
            "kind": kind,
            "project_id": project_id,
            "key": key,
            "title": title,
            "body": body,
            "tags": _tags_to_text(tags),
            "quality": quality,
            "source": source[:200],
            "evidence": json.dumps(evidence or {}, default=str),
            "created_at": now,
            "updated_at": now,
            "pinned": 1 if pinned else 0,
        }
        if dedupe and key:
            existing = self.db.query_one("SELECT id FROM memories WHERE kind = ? AND IFNULL(project_id,-1) = IFNULL(?,-1) AND key = ?", (kind, project_id, key))
            if existing:
                self.db.update(
                    "memories",
                    {"title": title, "body": body, "tags": payload["tags"], "quality": quality, "evidence": payload["evidence"], "updated_at": now},
                    "id = ?",
                    (existing["id"],),
                )
                BUS.publish(Topics.MEMORY_WRITE, action="updated", id=existing["id"], kind=kind, title=title)
                return int(existing["id"])
        try:
            row_id = self.db.insert("memories", payload)
        except Exception as exc:  # pragma: no cover - unique race
            log.debug("memory insert failed", cm={"error": str(exc)})
            return None
        BUS.publish(Topics.MEMORY_WRITE, action="created", id=row_id, kind=kind, title=title)
        return row_id

    @staticmethod
    def _content_key(kind: str, title: str, project_id: int | None) -> str:
        digest = hashlib.sha1(f"{kind}|{project_id}|{title.lower()}".encode("utf-8")).hexdigest()[:16]
        return f"auto-{digest}"

    def forget(self, memory_id: int) -> bool:
        row = self.db.query_one("SELECT pinned FROM memories WHERE id = ?", (memory_id,))
        if row is None:
            return False
        self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        BUS.publish(Topics.MEMORY_WRITE, action="deleted", id=memory_id)
        return True

    def pin(self, memory_id: int, pinned: bool = True) -> None:
        self.db.update("memories", {"pinned": 1 if pinned else 0, "updated_at": time.time()}, "id = ?", (memory_id,))
        BUS.publish(Topics.MEMORY_WRITE, action="pinned" if pinned else "unpinned", id=memory_id)

    def update(self, memory_id: int, **values: Any) -> bool:
        allowed = {"title", "body", "tags", "quality", "kind", "pinned"}
        payload = {k: v for k, v in values.items() if k in allowed}
        if not payload:
            return False
        if "tags" in payload and not isinstance(payload["tags"], str):
            payload["tags"] = _tags_to_text(payload["tags"])
        payload["updated_at"] = time.time()
        self.db.update("memories", payload, "id = ?", (memory_id,))
        BUS.publish(Topics.MEMORY_WRITE, action="updated", id=memory_id)
        return True

    def mark_used(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        now = time.time()
        placeholders = ",".join("?" for _ in ids)
        self.db.execute(f"UPDATE memories SET use_count = use_count + 1, last_used = ? WHERE id IN ({placeholders})", [now, *ids])

    def prune(self, max_entries: int) -> int:
        """Drop the least valuable entries beyond ``max_entries`` (pinned are kept)."""
        total = int(self.db.scalar("SELECT COUNT(*) FROM memories WHERE pinned = 0", default=0) or 0)
        if total <= max_entries:
            return 0
        excess = total - max_entries
        rows = self.db.query(
            """
            SELECT id FROM memories
            WHERE pinned = 0
            ORDER BY quality ASC, use_count ASC, updated_at ASC
            LIMIT ?
            """,
            (excess,),
        )
        ids = [row["id"] for row in rows]
        for memory_id in ids:
            self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        if ids:
            log.info("pruned memories", cm={"removed": len(ids)})
        return len(ids)

    # -- reading ----------------------------------------------------------
    def _row_to_entry(self, row: Any, score: float = 0.0) -> MemoryEntry:
        return MemoryEntry(
            id=int(row["id"]),
            kind=row["kind"],
            title=row["title"],
            body=row["body"],
            tags=_text_to_tags(row["tags"]),
            quality=float(row["quality"] or 0.0),
            project_id=row["project_id"],
            project_name=row["project_name"] if "project_name" in row.keys() else None,
            key=row["key"],
            source=row["source"] or "",
            evidence=_loads(row["evidence"], {}) or {},
            created_at=float(row["created_at"] or 0.0),
            updated_at=float(row["updated_at"] or 0.0),
            use_count=int(row["use_count"] or 0),
            last_used=row["last_used"],
            pinned=bool(row["pinned"]),
            score=score,
        )

    def get(self, memory_id: int) -> MemoryEntry | None:
        row = self.db.query_one(
            "SELECT m.*, p.name AS project_name FROM memories m LEFT JOIN projects p ON p.id = m.project_id WHERE m.id = ?",
            (memory_id,),
        )
        return self._row_to_entry(row) if row else None

    def search(
        self,
        query: str = "",
        *,
        kinds: Sequence[str] | None = None,
        project_id: int | None = None,
        limit: int = 50,
        min_quality: float = 0.0,
        include_global: bool = True,
    ) -> list[MemoryEntry]:
        """Full-text search with graceful degradation to a LIKE scan for tiny queries."""
        limit = max(1, min(500, limit))
        clauses: list[str] = []
        params: list[Any] = []
        if kinds:
            clauses.append(f"m.kind IN ({','.join('?' for _ in kinds)})")
            params.extend(kinds)
        if project_id is not None:
            clauses.append("(m.project_id = ? OR m.project_id IS NULL)" if include_global else "m.project_id = ?")
            params.append(project_id)
        if min_quality > 0:
            clauses.append("m.quality >= ?")
            params.append(min_quality)
        where = (" AND ".join(clauses)) if clauses else "1=1"
        fts = _fts_query(query)
        rows: list[Any] = []
        if fts:
            sql = f"""
                SELECT m.*, p.name AS project_name, bm25(memories_fts, 3.0, 1.0, 2.0) AS rank
                FROM memories_fts f
                JOIN memories m ON m.id = f.rowid
                LEFT JOIN projects p ON p.id = m.project_id
                WHERE memories_fts MATCH ? AND {where}
                ORDER BY m.pinned DESC, rank ASC, m.quality DESC
                LIMIT ?
            """
            try:
                rows = self.db.query(sql, [fts, *params, limit])
            except Exception as exc:  # pragma: no cover - malformed FTS expression
                log.debug("fts query failed, falling back", cm={"error": str(exc)})
                rows = []
        if not rows:
            like = f"%{query.strip()}%" if query.strip() else "%"
            sql = f"""
                SELECT m.*, p.name AS project_name, 0.0 AS rank
                FROM memories m LEFT JOIN projects p ON p.id = m.project_id
                WHERE ({where}) AND (m.title LIKE ? OR m.body LIKE ? OR IFNULL(m.tags,'') LIKE ?)
                ORDER BY m.pinned DESC, m.quality DESC, m.updated_at DESC
                LIMIT ?
            """
            rows = self.db.query(sql, [*params, like, like, like, limit])
        now = time.time()
        entries: list[MemoryEntry] = []
        for row in rows:
            entry = self._row_to_entry(row, score=float(row["rank"] or 0.0))
            entries.append(entry)
        if fts:
            entries.sort(key=lambda e: self._rank(e, now))
        return entries

    def _rank(self, entry: MemoryEntry, now: float) -> float:
        """Lower is better: bm25 (negative-ish scaled) adjusted by quality/recency/pinning."""
        base = entry.score if entry.score else 0.0
        age_days = max(0.0, (now - entry.updated_at) / 86400.0)
        recency = 1.0 / (1.0 + age_days / 30.0)
        usefulness = min(1.0, entry.use_count / 5.0)
        boost = 0.6 * entry.quality + 0.25 * recency + 0.15 * usefulness
        if entry.pinned:
            boost += 0.5
        return base - boost

    def recall(
        self,
        query: str,
        *,
        project_id: int | None = None,
        top_k: int = 6,
        kinds: Sequence[str] | None = None,
        min_quality: float | None = None,
        mark_used: bool = True,
    ) -> list[MemoryEntry]:
        """Return the memories worth injecting into a prompt for ``query``."""
        threshold = self.min_quality if min_quality is None else min_quality
        kinds = kinds or ("architecture", "preference", "solution", "mistake", "project_fact", "knowledge")
        entries = self.search(query, kinds=kinds, project_id=project_id, limit=max(top_k * 4, 12), min_quality=threshold)
        selected: list[MemoryEntry] = []
        seen: set[int] = set()
        for entry in entries:
            if entry.id in seen:
                continue
            # mistakes are only useful when they look related to the request
            if entry.kind == "mistake" and not _overlaps(query, f"{entry.title} {entry.body}"):
                continue
            seen.add(entry.id)
            selected.append(entry)
            if len(selected) >= top_k:
                break
        if mark_used and selected:
            self.mark_used([entry.id for entry in selected])
        return selected

    def recent(self, limit: int = 30, kinds: Sequence[str] | None = None) -> list[MemoryEntry]:
        clauses = ""
        params: list[Any] = []
        if kinds:
            clauses = f"WHERE m.kind IN ({','.join('?' for _ in kinds)})"
            params.extend(kinds)
        rows = self.db.query(
            f"SELECT m.*, p.name AS project_name FROM memories m LEFT JOIN projects p ON p.id = m.project_id {clauses} "
            "ORDER BY m.updated_at DESC LIMIT ?",
            [*params, limit],
        )
        return [self._row_to_entry(row) for row in rows]

    def context_block(
        self,
        query: str,
        *,
        project_id: int | None = None,
        top_k: int = 6,
        min_quality: float | None = None,
    ) -> tuple[str, list[MemoryEntry]]:
        """Render memories as a prompt block, plus the entries used."""
        entries = self.recall(query, project_id=project_id, top_k=top_k, min_quality=min_quality)
        if not entries:
            return "", []
        lines = [entry.to_prompt() for entry in entries]
        block = "Relevant memory from previous sessions:\n" + "\n".join(lines)
        return block, entries

    # -- statistics -------------------------------------------------------
    def stats(self, project_id: int | None = None) -> dict[str, Any]:
        where = "WHERE project_id = ?" if project_id is not None else ""
        params = (project_id,) if project_id is not None else ()
        counts = {kind: 0 for kind in MEMORY_KINDS}
        for row in self.db.query(f"SELECT kind, COUNT(*) AS n FROM memories {where} GROUP BY kind", params):
            counts[row["kind"]] = int(row["n"])
        total = int(self.db.scalar(f"SELECT COUNT(*) FROM memories {where}", params, default=0) or 0)
        avg_quality = float(self.db.scalar(f"SELECT AVG(quality) FROM memories {where}", params, default=0.0) or 0.0)
        last = self.db.query_one(f"SELECT MAX(updated_at) AS ts FROM memories {where}", params)
        usable = int(self.db.scalar(f"SELECT COUNT(*) FROM memories {where + (' AND ' if where else 'WHERE ')}quality >= ?", (*params, self.min_quality), default=0) or 0)
        return {
            "total": total,
            "by_kind": counts,
            "avg_quality": round(avg_quality, 3),
            "usable": usable,
            "min_quality": self.min_quality,
            "last_write": float(last["ts"]) if last and last["ts"] else None,
        }

    def export_jsonl(self, path: Any, kinds: Sequence[str] | None = None) -> int:
        entries = self.search("", kinds=kinds, limit=100000, min_quality=0.0)
        from codemind.utils.files import atomic_write_text

        lines = [json.dumps(entry.as_dict(), default=str) for entry in entries]
        atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""), mode=0o600)
        return len(lines)

    def learn_lesson(self, title: str, body: str, *, project_id: int | None, evidence: dict[str, Any], tags: Sequence[str] = ()) -> int | None:
        """Record a mistake so the same failed action is not attempted blindly again."""
        if not self.learn_from_failures:
            return None
        return self.remember(
            "mistake",
            title,
            body,
            tags=tags,
            quality=0.7,
            project_id=project_id,
            source="agent",
            evidence=evidence,
        )


def _overlaps(query: str, text: str, minimum: int = 1) -> bool:
    query_words = {w.lower() for w in _WORD_RE.findall(query or "") if len(w) > 3}
    if not query_words:
        return True
    text_words = {w.lower() for w in _WORD_RE.findall(text or "")}
    return len(query_words & text_words) >= minimum
