"""BM25 retrieval index training.

Not a gradient method, and it does not pretend to be: it builds a real lexical index over your
memories and indexed code, tunes BM25's parameters against held-out queries (leave-one-out on
the corpus itself, which is an honest if modest signal), and reports recall, MRR and NDCG so
the improvement is measurable rather than asserted.
"""

from __future__ import annotations

import math
import random
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from codemind.logging_setup import get_logger

log = get_logger("training.retrieval")

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-/]{1,}")


def tokenize(text: str) -> list[str]:
    return [word.lower() for word in _WORD.findall(text or "")]


@dataclass
class BM25Index:
    k1: float = 1.4
    b: float = 0.72
    documents: list[dict[str, Any]] = field(default_factory=list)
    doc_tokens: list[list[str]] = field(default_factory=list)
    doc_freq: Counter = field(default_factory=Counter)
    doc_lengths: list[int] = field(default_factory=list)
    avg_length: float = 0.0

    def build(self, documents: Sequence[dict[str, Any]]) -> "BM25Index":
        self.documents = [dict(document) for document in documents]
        self.doc_tokens = [tokenize(str(document.get("text", ""))) for document in self.documents]
        self.doc_freq = Counter()
        self.doc_lengths = []
        for tokens in self.doc_tokens:
            self.doc_lengths.append(len(tokens))
            for token in set(tokens):
                self.doc_freq[token] += 1
        self.avg_length = (sum(self.doc_lengths) / len(self.doc_lengths)) if self.doc_lengths else 0.0
        return self

    def _idf(self, term: str) -> float:
        count = self.doc_freq.get(term, 0)
        total = len(self.documents)
        if not count:
            return 0.0
        return math.log(1.0 + (total - count + 0.5) / (count + 0.5))

    def score(self, query: str, index: int) -> float:
        tokens = tokenize(query)
        if not tokens or not self.documents:
            return 0.0
        frequencies = Counter(self.doc_tokens[index])
        length = self.doc_lengths[index] or 1
        score = 0.0
        for token in tokens:
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            denominator = frequency + self.k1 * (1 - self.b + self.b * length / (self.avg_length or 1))
            score += self._idf(token) * (frequency * (self.k1 + 1)) / (denominator or 1)
        return score

    def search(self, query: str, limit: int = 5) -> list[tuple[int, float]]:
        scored = [(index, self.score(query, index)) for index in range(len(self.documents))]
        scored.sort(key=lambda item: -item[1])
        return [(index, score) for index, score in scored[:limit] if score > 0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "bm25",
            "k1": self.k1,
            "b": self.b,
            "documents": self.documents,
            "doc_freq": dict(self.doc_freq),
            "doc_lengths": self.doc_lengths,
            "avg_length": self.avg_length,
        }


def _relevance(query: str, document: dict[str, Any]) -> bool:
    """Ground truth for tuning: a document is relevant when it shares rare query terms."""
    query_terms = {term for term in tokenize(query) if len(term) > 3}
    if not query_terms:
        return False
    text_terms = set(tokenize(str(document.get("text", ""))))
    return len(query_terms & text_terms) >= max(1, len(query_terms) // 3)


def _metrics(index: BM25Index, queries: Sequence[tuple[str, int]], k: int = 5) -> dict[str, float]:
    reciprocal = 0.0
    hits = 0
    ndcg = 0.0
    for query, relevant_index in queries:
        results = [doc_index for doc_index, _score in index.search(query, limit=k)]
        if relevant_index in results:
            hits += 1
            reciprocal += 1.0 / (results.index(relevant_index) + 1)
            ndcg += 1.0 / math.log2(results.index(relevant_index) + 2)
    total = max(1, len(queries))
    return {
        f"recall@{k}": round(hits / total, 4),
        f"mrr@{k}": round(reciprocal / total, 4),
        f"ndcg@{k}": round(ndcg / total, 4),
    }


def build_retrieval_index(
    documents: Sequence[dict[str, Any]],
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    search_k: int = 5,
) -> dict[str, Any]:
    """Build and tune a BM25 index; returns {'index': ..., 'metrics': ...}."""
    if not documents:
        raise RuntimeError("no documents to index")
    rng = random.Random(11)
    corpus = list(documents)
    queries: list[tuple[str, int]] = []
    for index, document in enumerate(corpus):
        tokens = [token for token in tokenize(str(document.get("text", ""))) if len(token) > 3]
        if not tokens:
            continue
        sample = rng.sample(tokens, min(3, len(tokens)))
        queries.append((" ".join(sample), index))
    rng.shuffle(queries)
    holdout = queries[: max(5, len(queries) // 5)]
    tuning = queries[len(holdout) :] or queries

    best: dict[str, Any] | None = None
    trials: list[dict[str, Any]] = []
    grid = [(1.2, 0.6), (1.4, 0.72), (1.6, 0.8), (2.0, 0.75)]
    for step, (k1, b) in enumerate(grid, start=1):
        if should_stop is not None and should_stop():
            break
        index = BM25Index(k1=k1, b=b).build(corpus)
        metrics = _metrics(index, tuning[:400], k=search_k)
        trial = {"k1": k1, "b": b, **metrics, "documents": len(corpus), "tuning_queries": len(tuning)}
        trials.append(trial)
        if on_step is not None:
            on_step({"step": step, "total_steps": len(grid), "loss": round(1 - metrics[f"recall@{search_k}"], 4),
                     "accuracy": metrics[f"recall@{search_k}"], "epoch": step / len(grid), "trial": trial})
        if best is None or metrics[f"ndcg@{search_k}"] > best["metrics"][f"ndcg@{search_k}"]:
            best = {"k1": k1, "b": b, "metrics": metrics}
    if best is None:
        best = {"k1": 1.4, "b": 0.72, "metrics": {}}
    final_index = BM25Index(k1=best["k1"], b=best["b"]).build(corpus)
    holdout_metrics = _metrics(final_index, holdout, k=search_k)
    metrics = {
        "documents": len(corpus),
        "tuning_queries": len(tuning),
        "holdout_queries": len(holdout),
        "best_k1": best["k1"],
        "best_b": best["b"],
        "tuning": best["metrics"],
        "holdout": holdout_metrics,
        "best_avg_ndcg": holdout_metrics.get(f"ndcg@{search_k}", 0.0),
        "trials": trials,
        "built_at": time.time(),
    }
    log.info("retrieval index built", cm={k: v for k, v in holdout_metrics.items()})
    return {"index": final_index.to_dict(), "metrics": metrics}


def load_index(path: str) -> BM25Index:
    import json
    from pathlib import Path

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    index = BM25Index(k1=payload.get("k1", 1.4), b=payload.get("b", 0.72))
    index.documents = payload.get("documents", [])
    index.doc_tokens = [tokenize(str(document.get("text", ""))) for document in index.documents]
    index.doc_freq = Counter(payload.get("doc_freq", {}))
    index.doc_lengths = payload.get("doc_lengths", [len(tokens) for tokens in index.doc_tokens])
    index.avg_length = payload.get("avg_length", 0.0) or 1.0
    return index
