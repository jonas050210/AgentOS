"""Motion helpers for the desktop interface.

Every animation lives here so the whole application moves with the same timing and easing, and so
that the ``Appearance → Motion`` setting is honoured in exactly one place: ``full`` animates,
``reduced`` keeps short cross-fades and drops movement, ``off`` renders every state instantly.

Animations are always optional. Nothing in this module may be required for a view to be *correct* —
a hidden window, a test run without an event loop, or ``motion: off`` all still produce the right
final state.
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QTimer, QVariantAnimation, Qt
from PySide6.QtWidgets import QGraphicsOpacityEffect, QStackedWidget, QWidget

from codemind.ui import theme as T


def level() -> str:
    """``full``, ``reduced`` or ``off`` — read from the live appearance settings."""
    return T.appearance().motion


def enabled() -> bool:
    return level() != "off"


def full_motion() -> bool:
    return level() == "full"


def duration(ms: int) -> int:
    """Scale a nominal duration for the current motion level (0 = do not animate)."""
    current = level()
    if current == "off":
        return 0
    if current == "reduced":
        return max(60, int(ms * 0.6))
    return ms


class Tween(QObject):
    """Animate a float from ``start`` to ``end`` and report every frame.

    Kept deliberately small: no property system, no widget specifics, one parent for lifetime.
    """

    def __init__(
        self,
        start: float,
        end: float,
        on_frame: Callable[[float], None],
        *,
        ms: int = 260,
        on_done: Callable[[], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_frame = on_frame
        self._on_done = on_done
        scaled = duration(ms)
        if scaled <= 0 or abs(end - start) < 1e-9:
            on_frame(end)
            if on_done is not None:
                on_done()
            self._animation = None
            return
        animation = QVariantAnimation(self)
        animation.setStartValue(float(start))
        animation.setEndValue(float(end))
        animation.setDuration(scaled)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.valueChanged.connect(lambda value: self._on_frame(float(value)))
        animation.finished.connect(self._finish)
        self._animation = animation
        animation.start()

    def _finish(self) -> None:
        if self._on_done is not None:
            self._on_done()


def animate_number(
    apply_text: Callable[[str], None],
    start: float,
    end: float,
    formatter: Callable[[float], str],
    *,
    ms: int = 320,
    parent: QObject | None = None,
    on_done: Callable[[], None] | None = None,
) -> Tween | None:
    """Count from ``start`` to ``end`` while a label is updated with ``formatter``."""
    return Tween(start, end, lambda value: apply_text(formatter(value)), ms=ms, parent=parent, on_done=on_done)


def pulse(widget: QWidget, *, low: float = 0.45, high: float = 1.0, period: int = 1500) -> None:
    """Let a widget breathe — used for 'live' badges and running indicators."""
    if not enabled():
        return
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    effect.setOpacity(high)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration(period))
    animation.setStartValue(high)
    animation.setKeyValueAt(0.5, low)
    animation.setEndValue(high)
    animation.setLoopCount(-1)
    animation.setEasingCurve(QEasingCurve.InOutSine)
    animation.start()
    widget._pulse_animation = animation  # type: ignore[attr-defined]


def stop_pulse(widget: QWidget) -> None:
    animation = getattr(widget, "_pulse_animation", None)
    if animation is not None:
        animation.stop()
    effect = widget.graphicsEffect()
    if isinstance(effect, QGraphicsOpacityEffect):
        effect.setOpacity(1.0)
        widget.setGraphicsEffect(None)


def fade_in(widget: QWidget, *, ms: int = 200, delay: int = 0) -> None:
    """Fade a widget in, optionally after a short delay (used for lists that grow)."""
    if not enabled():
        return
    instance = Tween(0.0, 1.0, lambda value: _set_opacity(widget, value), ms=ms, parent=widget)
    if delay:
        animation = getattr(instance, "_animation", None)
        if animation is not None:
            animation.stop()
            QTimer.singleShot(duration(delay), lambda: fade_in(widget, ms=ms))
    else:
        _set_opacity(widget, 0.0)


def _set_opacity(widget: QWidget, value: float) -> None:
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    effect.setOpacity(max(0.0, min(1.0, value)))
    if value >= 1.0:
        # keeping an opacity effect on a complex widget costs a repaint every frame: drop it again
        widget.setGraphicsEffect(None)


def stagger(widgets: Sequence[QWidget], *, step: int = 45, ms: int = 220) -> None:
    """Reveal a group of widgets one after another — used for the first paint of a page."""
    if not enabled():
        return
    for index, widget in enumerate(widgets):
        if index == 0:
            fade_in(widget, ms=ms)
        else:
            QTimer.singleShot(duration(index * step), lambda w=widget: fade_in(w, ms=ms))


class FadeStack(QStackedWidget):
    """A stacked widget that cross-fades between pages instead of snapping."""

    def __init__(self, parent: QWidget | None = None, *, ms: int = 180) -> None:
        super().__init__(parent)
        self._ms = ms
        self._first = True

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 - Qt naming
        previous = self.currentIndex()
        super().setCurrentIndex(index)
        if index == previous:
            return
        page = self.widget(index)
        if page is None:
            return
        if self._first or not enabled():
            page.setGraphicsEffect(None)
            self._first = False
            return
        self._first = False
        _fade_page(page, duration(self._ms))


def _fade_page(page: QWidget, ms: int) -> None:
    effect = QGraphicsOpacityEffect(page)
    page.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", page)
    animation.setDuration(ms)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.OutCubic)

    def _cleanup() -> None:
        # an opacity effect forces software compositing of the whole page, so remove it afterwards
        if page.graphicsEffect() is effect:
            page.setGraphicsEffect(None)

    animation.finished.connect(_cleanup)
    animation.start()
    page._fade_animation = animation  # type: ignore[attr-defined]


def flash(widget: QWidget, *, tone: str | None = None, ms: int = 700) -> None:
    """Briefly highlight a widget whose value just changed."""
    hook = getattr(widget, "flash", None)
    if callable(hook):
        hook(tone, ms)
        return
    if not enabled():
        return
    fade_in(widget, ms=ms)


def describe_motion() -> str:
    """One line for the Settings view so the setting is visibly connected to behaviour."""
    return {
        "full": "full motion: transitions, counting numbers, breathing live indicators",
        "reduced": "reduced motion: short cross-fades only",
        "off": "motion off: every state appears instantly",
    }.get(level(), level())


def animated(widget: QWidget, *, ms: int = 220) -> QWidget:
    """Convenience wrapper returning the widget with a fade applied (keeps call sites short)."""
    fade_in(widget, ms=ms)
    return widget


__all__ = [
    "FadeStack",
    "Tween",
    "animate_number",
    "animated",
    "describe_motion",
    "duration",
    "enabled",
    "fade_in",
    "flash",
    "full_motion",
    "level",
    "pulse",
    "stagger",
    "stop_pulse",
]
