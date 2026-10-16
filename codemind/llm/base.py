"""Contracts shared by every inference backend."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence, Protocol


class LLMError(RuntimeError):
    """Raised when a backend cannot answer (unreachable, timeout, malformed response)."""

    def __init__(self, message: str, *, backend: str = "", retryable: bool = True) -> None:
        super().__init__(message)
        self.backend = backend
        self.retryable = retryable


@dataclass
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str
    name: str = ""
    tool_calls: list["ToolCall"] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            payload["name"] = self.name
        if self.tool_calls:
            payload["tool_calls"] = [call.as_dict() for call in self.tool_calls]
        return payload

    @classmethod
    def system(cls, content: str) -> "ChatMessage":
        return cls("system", content)

    @classmethod
    def user(cls, content: str) -> "ChatMessage":
        return cls("user", content)

    @classmethod
    def assistant(cls, content: str) -> "ChatMessage":
        return cls("assistant", content)

    @classmethod
    def tool(cls, content: str, name: str = "") -> "ChatMessage":
        return cls("tool", content, name=name)


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = {"name": self.name, "arguments": self.arguments}
        if self.id:
            payload["id"] = self.id
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ToolCall":
        function = payload.get("function") if isinstance(payload.get("function"), Mapping) else None
        source = function or payload
        name = str(source.get("name") or payload.get("name") or "")
        args = source.get("arguments") or payload.get("arguments") or source.get("parameters") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
        if not isinstance(args, Mapping):
            args = {"value": args}
        return cls(name=name, arguments=dict(args), id=str(payload.get("id") or ""))


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


@dataclass
class BackendStatus:
    name: str
    kind: str
    available: bool
    detail: str
    models: list[str] = field(default_factory=list)
    endpoint: str = ""
    latency_ms: float | None = None
    version: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "available": self.available,
            "detail": self.detail,
            "models": list(self.models),
            "endpoint": self.endpoint,
            "latency_ms": self.latency_ms,
            "version": self.version,
            "error": self.error,
        }


@dataclass
class LLMResponse:
    text: str
    backend: str
    model: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_s: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "backend": self.backend,
            "model": self.model,
            "tool_calls": [call.as_dict() for call in self.tool_calls],
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "duration_s": round(self.duration_s, 3),
            "finish_reason": self.finish_reason,
        }


class LLMBackend(Protocol):
    """What the planner needs from an inference backend."""

    name: str
    kind: str
    model: str

    def status(self) -> BackendStatus: ...

    def list_models(self) -> list[str]: ...

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        stop: Sequence[str] | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> LLMResponse: ...


_FENCE_RE = re.compile(r"```(?:json|jsonc|json5)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str, *, expect: str | None = None) -> Any:
    """Pull the first JSON value out of a model answer.

    Models wrap JSON in prose and fences; this walks the string looking for the first
    balanced object or array and parses it. Returns ``None`` when nothing parses.
    """
    if not text:
        return None
    candidates: list[str] = []
    for match in _FENCE_RE.finditer(text):
        candidates.append(match.group(1).strip())
    candidates.append(text.strip())
    for candidate in candidates:
        parsed = _parse_balanced(candidate)
        if parsed is not None:
            return parsed
    if expect:
        # last resort: the requested key appears somewhere in the text
        match = re.search(rf'"{re.escape(expect)}"\s*:\s*(\[[\s\S]*?\]|\{{[\s\S]*?\}})', text)
        if match:
            return _parse_balanced(match.group(1))
    return None


def _parse_balanced(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth = 0
            in_string = False
            escape = False
            for index in range(start, len(text)):
                char = text[index]
                if in_string:
                    if escape:
                        escape = False
                    elif char == "\\":
                        escape = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == opener:
                    depth += 1
                elif char == closer:
                    depth -= 1
                    if depth == 0:
                        chunk = text[start : index + 1]
                        try:
                            return json.loads(chunk)
                        except json.JSONDecodeError:
                            repaired = _repair_json(chunk)
                            if repaired is not None:
                                return repaired
                            break
            start = text.find(opener, start + 1)
    return None


def _repair_json(chunk: str) -> Any:
    """Salvage the common model mistakes: trailing commas and single quotes."""
    repaired = re.sub(r",\s*([}\]])", r"\1", chunk)
    if '"' not in repaired and "'" in repaired:
        repaired = repaired.replace("'", '"')
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token) for budgeting prompts."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def enforce_budget(messages: Sequence[ChatMessage], max_tokens: int) -> list[ChatMessage]:
    """Trim the oldest non-system messages so the prompt fits the context window."""
    total = sum(estimate_tokens(m.content) for m in messages)
    if total <= max_tokens:
        return list(messages)
    system = [m for m in messages if m.role == "system"]
    rest = [m for m in messages if m.role != "system"]
    budget = max_tokens - sum(estimate_tokens(m.content) for m in system)
    kept: list[ChatMessage] = []
    running = 0
    for message in reversed(rest):
        cost = estimate_tokens(message.content)
        if running + cost > budget and kept:
            break
        kept.append(message)
        running += cost
    kept.reverse()
    return system + kept


def render_messages(messages: Sequence[ChatMessage]) -> str:
    """Flatten messages into the transcript form used by the built-in planner."""
    parts: list[str] = []
    for message in messages:
        prefix = {"system": "SYSTEM", "user": "USER", "assistant": "ASSISTANT", "tool": "TOOL"}.get(message.role, message.role.upper())
        parts.append(f"### {prefix}\n{message.content}")
    return "\n\n".join(parts)


def timed() -> float:
    return time.monotonic()


class RetryPolicy:
    """Small helper so every backend retries transient failures the same way."""

    def __init__(self, attempts: int = 2, base_delay: float = 0.6, max_delay: float = 5.0) -> None:
        self.attempts = max(1, attempts)
        self.base_delay = base_delay
        self.max_delay = max_delay

    def run(self, operation: Callable[[], Any], *, on_retry: Callable[[int, Exception], None] | None = None) -> Any:
        last: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                return operation()
            except LLMError as exc:
                last = exc
                if not exc.retryable or attempt == self.attempts:
                    raise
                if on_retry:
                    on_retry(attempt, exc)
                time.sleep(min(self.max_delay, self.base_delay * attempt))
            except OSError as exc:
                last = exc
                if attempt == self.attempts:
                    raise LLMError(str(exc), retryable=True) from exc
                if on_retry:
                    on_retry(attempt, exc)
                time.sleep(min(self.max_delay, self.base_delay * attempt))
        if last:
            raise last
        raise LLMError("retry policy exhausted without a result")


def iter_lines(text: str) -> Iterable[str]:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            yield stripped
