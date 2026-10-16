"""Thread-safe publish/subscribe bus used to move state from workers to the GUI.

The bus is intentionally tiny and dependency-free. Every publisher runs in some worker
thread (agent executor, training subprocess reader, resource sampler); every subscriber
that touches Qt widgets must re-emit through a Qt signal, which is what
``codemind.ui.bridge`` does. Subscribers that raise are isolated: a broken listener never
breaks the producer.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

Listener = Callable[["Event"], None]


@dataclass(frozen=True)
class Event:
    """A single fact that happened somewhere in the system."""

    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    seq: int = 0

    def matches(self, pattern: str) -> bool:
        if pattern in ("*", "#"):
            return True
        if pattern.endswith(".*"):
            return self.topic.startswith(pattern[:-1])
        return self.topic == pattern


class EventBus:
    """Bounded, thread-safe event fan-out with a replayable ring buffer."""

    def __init__(self, history: int = 400) -> None:
        self._lock = threading.RLock()
        self._listeners: list[tuple[str, Listener]] = []
        self._history: deque[Event] = deque(maxlen=history)
        self._counter = itertools.count(1)
        self._delivered = 0
        self._errors = 0

    # -- subscription -----------------------------------------------------
    def subscribe(self, pattern: str, listener: Listener) -> Callable[[], None]:
        """Register ``listener`` for ``pattern``; returns an unsubscribe callable."""
        with self._lock:
            entry = (pattern, listener)
            self._listeners.append(entry)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._listeners.remove(entry)
                except ValueError:
                    pass

        return _unsubscribe

    def on(self, pattern: str, listener: Listener) -> Callable[[], None]:
        return self.subscribe(pattern, listener)

    # -- publication ------------------------------------------------------
    def publish(self, topic: str, **payload: Any) -> Event:
        event = Event(topic=topic, payload=dict(payload), seq=next(self._counter))
        with self._lock:
            self._history.append(event)
            listeners = list(self._listeners)
        for pattern, listener in listeners:
            if not event.matches(pattern):
                continue
            try:
                listener(event)
                with self._lock:
                    self._delivered += 1
            except Exception:  # pragma: no cover - defensive isolation
                with self._lock:
                    self._errors += 1
        return event

    def emit(self, event: Event) -> Event:
        with self._lock:
            self._history.append(event)
            listeners = list(self._listeners)
        for pattern, listener in listeners:
            if event.matches(pattern):
                try:
                    listener(event)
                except Exception:  # pragma: no cover
                    pass
        return event

    # -- inspection -------------------------------------------------------
    def history(self, pattern: str = "*", limit: int = 100) -> list[Event]:
        with self._lock:
            items: Iterable[Event] = list(self._history)
        matched = [e for e in items if e.matches(pattern)]
        return matched[-limit:]

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "listeners": len(self._listeners),
                "buffered": len(self._history),
                "delivered": self._delivered,
                "listener_errors": self._errors,
            }

    def wait_for(self, pattern: str, timeout: float = 5.0, predicate: Callable[[Event], bool] | None = None) -> Event | None:
        """Block until a matching event arrives (used by tests and CLI waiters)."""
        box: list[Event] = []
        done = threading.Event()

        def _listener(event: Event) -> None:
            if predicate is None or predicate(event):
                box.append(event)
                done.set()

        unsubscribe = self.subscribe(pattern, _listener)
        try:
            if done.wait(timeout) and box:
                return box[0]
            return None
        finally:
            unsubscribe()


BUS = EventBus()


# Canonical topic names (keeps producers and consumers in sync).
class Topics:
    STARTUP = "app.startup"
    SHUTDOWN = "app.shutdown"
    LOG = "log.entry"
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_STEP = "task.step"
    TASK_PLAN = "task.plan"
    TASK_LOG = "task.log"
    TASK_DIFF = "task.diff"
    TASK_FINISHED = "task.finished"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    SANDBOX_EXEC = "sandbox.exec"
    SANDBOX_STATUS = "sandbox.status"
    TRAINING_RUN = "training.run"
    TRAINING_PROGRESS = "training.progress"
    TRAINING_EVAL = "training.eval"
    TRAINING_FINISHED = "training.finished"
    MODEL_STATE = "model.state"
    MODEL_DOWNLOAD = "model.download"
    MEMORY_WRITE = "memory.write"
    MEMORY_INDEX = "memory.index"
    RESOURCE_SAMPLE = "system.resources"
    HARDWARE = "system.hardware"
    GOVERNOR = "system.governor"
    GITHUB_ACTION = "github.action"
    AUTONOMY_PROPOSAL = "autonomy.proposal"
    SETTINGS_CHANGED = "settings.changed"
    STORAGE_CHANGED = "storage.changed"
