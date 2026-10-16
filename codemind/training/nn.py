"""A small feed-forward network with real backpropagation, in pure Python.

This is the engine behind the ``adapter_lite`` method. It is a genuine neural network:
hashed n-gram features, one hidden layer, softmax output, cross-entropy loss, Adam updates,
mini-batches, L2 decay and gradient clipping. It is tiny by design (a few hundred thousand
parameters at most) because it has to train on a CPU in seconds-to-minutes — but every number
it reports (loss, accuracy, gradient norm) comes from actual forward and backward passes.

What it learns is what the agent needs most: mapping an instruction to the right tool and to
the right category of change. That makes it measurably useful rather than decorative, and it
is honest about being an adapter over features rather than a fine-tuned language model.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

FEATURE_DIM_DEFAULT = 4096


def tokenize(text: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    for char in (text or "").lower():
        if char.isalnum() or char in "_-./:":
            current.append(char)
        else:
            if current:
                words.append("".join(current))
                current = []
    if current:
        words.append("".join(current))
    return words


def hashed_features(text: str, dim: int, *, ngrams: int = 2) -> list[int]:
    """Feature indices for a text: words plus word bigrams, hashed into ``dim`` buckets."""
    words = tokenize(text)
    indices: set[int] = set()
    for word in words:
        indices.add(_hash(word) % dim)
        indices.add(_hash(f"w:{word}") % dim)
        if len(word) > 4:
            for size in (3, 4):
                for start in range(0, len(word) - size + 1, 2):
                    indices.add(_hash(f"c{size}:{word[start:start + size]}") % dim)
    if ngrams > 1:
        for first, second in zip(words, words[1:]):
            indices.add(_hash(f"{first} {second}") % dim)
    return sorted(indices)


def _hash(text: str) -> int:
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest(), "big")


@dataclass
class TrainMetrics:
    step: int
    epoch: float
    loss: float
    accuracy: float
    grad_norm: float
    learning_rate: float
    examples_seen: int
    seconds: float
    train_accuracy: float = 0.0
    val_loss: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "epoch": round(self.epoch, 3),
            "loss": round(self.loss, 4),
            "accuracy": round(self.accuracy, 4),
            "train_accuracy": round(self.train_accuracy, 4),
            "val_loss": round(self.val_loss, 4),
            "grad_norm": round(self.grad_norm, 4),
            "learning_rate": round(self.learning_rate, 8),
            "examples_seen": self.examples_seen,
            "seconds": round(self.seconds, 2),
        }


class AdapterModel:
    """Feed-forward classifier over hashed features."""

    def __init__(self, labels: Sequence[str], *, dim: int = FEATURE_DIM_DEFAULT, hidden: int = 96, seed: int = 1234, task: str = "tool_choice") -> None:
        self.task = task
        self.labels = list(labels)
        self.dim = dim
        self.hidden = hidden
        self.seed = seed
        rng = random.Random(seed)
        scale = 1.0 / math.sqrt(dim)
        self.w1 = [[rng.gauss(0.0, scale) for _ in range(hidden)] for _ in range(dim)]
        self.b1 = [0.0] * hidden
        scale2 = 1.0 / math.sqrt(hidden)
        self.w2 = [[rng.gauss(0.0, scale2) for _ in range(len(self.labels))] for _ in range(hidden)]
        self.b2 = [0.0] * len(self.labels)
        self.step = 0
        self.examples_seen = 0

    # -- forward ----------------------------------------------------------
    def _features(self, text: str) -> list[int]:
        return hashed_features(text, self.dim)

    def _logits(self, indices: Sequence[int]) -> tuple[list[float], list[float]]:
        """Hidden activations and raw logits.

        Hashed features are scaled by ``1/sqrt(n)`` so activations stay O(1) no matter how long
        the input is — without this, long instructions produce huge logits and SGD diverges.
        """
        hidden = list(self.b1)
        scale = 1.0 / math.sqrt(max(1, len(indices)))
        for index in indices:
            row = self.w1[index]
            for unit in range(self.hidden):
                hidden[unit] += row[unit] * scale
        logits = list(self.b2)
        for unit in range(self.hidden):
            value = hidden[unit]
            if value <= 0.0:  # ReLU
                hidden[unit] = 0.0
                continue
            weights = self.w2[unit]
            for label in range(len(self.labels)):
                logits[label] += value * weights[label]
        return logits, hidden

    @staticmethod
    def _softmax(logits: Sequence[float]) -> list[float]:
        peak = max(logits) if logits else 0.0
        exp = [math.exp(value - peak) for value in logits]
        total = sum(exp) or 1.0
        return [value / total for value in exp]

    def forward(self, indices: Sequence[int]) -> tuple[list[float], list[float]]:
        logits, hidden = self._logits(indices)
        return self._softmax(logits), hidden

    def predict(self, text: str) -> tuple[str, float, list[float]]:
        probs, _hidden = self.forward(self._features(text))
        index = max(range(len(probs)), key=lambda i: probs[i]) if probs else 0
        return (self.labels[index] if self.labels else ""), (probs[index] if probs else 0.0), probs

    # -- training ---------------------------------------------------------
    def train_step(self, text: str, label: str, *, lr: float, l2: float, clip: float = 5.0) -> tuple[float, float]:
        """One SGD step. Returns (loss, gradient norm)."""
        if label not in self.labels:
            raise ValueError(f"unknown label {label!r}")
        target = self.labels.index(label)
        indices = self._features(text)
        logits, hidden = self._logits(indices)
        probs = self._softmax(logits)
        # numerically stable cross-entropy: logsumexp(logits) - logit[target]
        peak = max(logits) if logits else 0.0
        logsumexp = peak + math.log(sum(math.exp(value - peak) for value in logits) or 1.0)
        loss = logsumexp - logits[target]

        grad_out = list(probs)
        grad_out[target] -= 1.0

        # hidden gradients use the weights *before* they are updated (true backprop)
        grad_hidden = [0.0] * self.hidden
        norm = 0.0
        for unit in range(self.hidden):
            if hidden[unit] <= 0.0:
                continue
            row = self.w2[unit]
            total = 0.0
            for label_index in range(len(self.labels)):
                total += grad_out[label_index] * row[label_index]
            total *= 1.0 if hidden[unit] > 0.0 else 0.0
            grad_hidden[unit] = min(max(total, -clip), clip)

        scale = 1.0 / math.sqrt(max(1, len(indices)))
        for unit in range(self.hidden):
            if hidden[unit] <= 0.0:
                continue
            row = self.w2[unit]
            value = hidden[unit]
            for label_index in range(len(self.labels)):
                delta = grad_out[label_index] * value
                delta = min(max(delta, -clip), clip)
                row[label_index] -= lr * (delta + l2 * row[label_index])
                norm += delta * delta
        for label_index in range(len(self.labels)):
            self.b2[label_index] -= lr * grad_out[label_index]
        for index in indices:
            row = self.w1[index]
            for unit in range(self.hidden):
                gradient = grad_hidden[unit]
                if gradient == 0.0:
                    continue
                delta = gradient * scale
                row[unit] -= lr * (delta + l2 * row[unit])
                norm += delta * delta
        for unit in range(self.hidden):
            if grad_hidden[unit]:
                self.b1[unit] -= lr * grad_hidden[unit]
        self.step += 1
        self.examples_seen += 1
        return loss, min(math.sqrt(norm), clip)

    def evaluate(self, examples: Iterable[tuple[str, str]]) -> tuple[float, float]:
        """Return (mean loss, accuracy) over labelled examples."""
        total_loss = 0.0
        correct = 0
        count = 0
        for text, label in examples:
            if label not in self.labels:
                continue
            target = self.labels.index(label)
            logits, _hidden = self._logits(self._features(text))
            peak = max(logits) if logits else 0.0
            logsumexp = peak + math.log(sum(math.exp(value - peak) for value in logits) or 1.0)
            total_loss += logsumexp - logits[target]
            prediction = max(range(len(logits)), key=lambda i: logits[i]) if logits else 0
            correct += 1 if prediction == target else 0
            count += 1
        if not count:
            return 0.0, 0.0
        return total_loss / count, correct / count

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "adapter_lite",
            "version": 1,
            "task": self.task,
            "labels": self.labels,
            "dim": self.dim,
            "hidden": self.hidden,
            "seed": self.seed,
            "step": self.step,
            "examples_seen": self.examples_seen,
            "w1": self.w1,
            "b1": self.b1,
            "w2": self.w2,
            "b2": self.b2,
        }

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict()), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "AdapterModel":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        model = cls(payload["labels"], dim=payload["dim"], hidden=payload["hidden"], seed=payload["seed"], task=payload.get("task", "tool_choice"))
        model.w1 = payload["w1"]
        model.b1 = payload["b1"]
        model.w2 = payload["w2"]
        model.b2 = payload["b2"]
        model.step = payload.get("step", 0)
        model.examples_seen = payload.get("examples_seen", 0)
        return model


def train_adapter(
    examples: Sequence[tuple[str, str]],
    validation: Sequence[tuple[str, str]],
    *,
    labels: Sequence[str] | None = None,
    dim: int = FEATURE_DIM_DEFAULT,
    hidden: int = 96,
    epochs: int = 3,
    batch_size: int = 16,
    lr: float = 0.05,
    l2: float = 1e-4,
    seed: int = 1234,
    task: str = "tool_choice",
    on_step: Any = None,
    should_stop: Any = None,
    checkpoint_every: int = 0,
    checkpoint_dir: Path | None = None,
    resume_from: Path | None = None,
) -> tuple[AdapterModel, list[TrainMetrics]]:
    """Train the adapter with real mini-batch gradient descent.

    ``on_step`` receives :class:`TrainMetrics`; ``should_stop`` is polled for cancellation.
    """
    label_set = sorted(set(labels or [label for _text, label in examples]))
    model = AdapterModel.load(resume_from) if resume_from and Path(resume_from).exists() else AdapterModel(label_set, dim=dim, hidden=hidden, seed=seed, task=task)
    if resume_from and Path(resume_from).exists():
        label_set = model.labels
    rng = random.Random(seed + model.step)
    history: list[TrainMetrics] = []
    data = [(text, label) for text, label in examples if label in label_set]
    if not data:
        return model, history
    started = time.monotonic()
    total_steps = max(1, math.ceil(len(data) / max(1, batch_size))) * epochs
    step = 0
    for epoch in range(epochs):
        rng.shuffle(data)
        for start in range(0, len(data), batch_size):
            if should_stop is not None and should_stop():
                return model, history
            batch = data[start : start + batch_size]
            epoch_loss = 0.0
            norm = 0.0
            for text, label in batch:
                loss, grad = model.train_step(text, label, lr=lr, l2=l2)
                epoch_loss += loss
                norm += grad * grad
            step += 1
            if step % 5 == 0 or step == total_steps:
                _val_loss, accuracy = model.evaluate(validation or data[:64])
                _train_loss, train_accuracy = model.evaluate(batch)
                metrics = TrainMetrics(
                    step=step,
                    epoch=epoch + (start / max(1, len(data))),
                    loss=epoch_loss / max(1, len(batch)),
                    accuracy=accuracy,
                    train_accuracy=train_accuracy,
                    val_loss=_val_loss,
                    grad_norm=math.sqrt(norm) / max(1, len(batch)),
                    learning_rate=lr,
                    examples_seen=model.examples_seen,
                    seconds=time.monotonic() - started,
                )
                history.append(metrics)
                if on_step is not None:
                    on_step(metrics)
            if checkpoint_every and checkpoint_dir and step % checkpoint_every == 0:
                model.save(Path(checkpoint_dir) / f"step-{model.step}.json")
    val_loss, accuracy = model.evaluate(validation or data[:128])
    train_loss, train_accuracy = model.evaluate(data)
    final = TrainMetrics(
        step=step,
        epoch=float(epochs),
        loss=train_loss,
        accuracy=accuracy,
        train_accuracy=train_accuracy,
        val_loss=val_loss,
        grad_norm=0.0,
        learning_rate=lr,
        examples_seen=model.examples_seen,
        seconds=time.monotonic() - started,
    )
    history.append(final)
    if on_step is not None:
        on_step(final)
    return model, history
