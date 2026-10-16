"""Thread-safe bridge from the event bus to Qt.

Worker threads publish events (task progress, training steps, sandbox output). Qt widgets may
only be touched from the GUI thread, so events are queued here and re-emitted as Qt signals from
a timer that runs on the GUI thread.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

from codemind.events import BUS, Event


class EventBridge(QObject):
    """Receive bus events on any thread, deliver them as Qt signals on the GUI thread."""

    event = Signal(str, dict)

    def __init__(self, *, interval_ms: int = 120, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: deque[tuple[str, dict[str, Any]]] = deque(maxlen=4000)
        self._lock = threading.Lock()
        self._unsubscribe = BUS.subscribe("*", self._on_event)
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._drain)
        self._timer.start()

    def _on_event(self, event: Event) -> None:
        with self._lock:
            self._queue.append((event.topic, dict(event.payload or {})))

    def _drain(self) -> None:
        batch: list[tuple[str, dict[str, Any]]] = []
        with self._lock:
            while self._queue:
                batch.append(self._queue.popleft())
        for topic, payload in batch:
            self.event.emit(topic, payload)

    def stop(self) -> None:
        self._timer.stop()
        try:
            self._unsubscribe()
        except Exception:  # pragma: no cover
            pass
