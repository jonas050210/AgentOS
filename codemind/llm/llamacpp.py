"""llama.cpp backend (``llama-server``, OpenAI-compatible HTTP API).

Useful when you prefer GGUF files over Ollama: run
``llama-server -m model.gguf -c 8192 --port 8080`` and CodeMind will use it. Model files can
also be launched by CodeMind itself from the Models view.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Sequence

from codemind.llm.base import (
    BackendStatus,
    ChatMessage,
    LLMError,
    LLMResponse,
    RetryPolicy,
    ToolCall,
    ToolSpec,
    enforce_budget,
)
from codemind.logging_setup import get_logger

log = get_logger("llm.llamacpp")


class LlamaCppBackend:
    """Client for a local ``llama-server`` instance."""

    kind = "llama_cpp"

    def __init__(
        self,
        host: str = "http://127.0.0.1:8080",
        model: str = "",
        *,
        context_tokens: int = 8192,
        temperature: float = 0.2,
        timeout: float = 240.0,
        retries: int = 2,
    ) -> None:
        self.host = host.rstrip("/")
        self.name = "llama_cpp"
        self.model = model
        self.context_tokens = context_tokens
        self.temperature = temperature
        self.timeout = timeout
        self._retry = RetryPolicy(attempts=retries)
        self._cached_status: BackendStatus | None = None
        self._status_ts = 0.0

    def _request(self, path: str, *, payload: dict[str, Any] | None = None, method: str = "GET", timeout: float | None = None, stream: bool = False) -> Any:
        url = f"{self.host}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if stream:
            request.add_header("Accept", "text/event-stream")
        try:
            response = urllib.request.urlopen(request, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            raise LLMError(f"llama-server returned HTTP {exc.code}: {body}", backend=self.name, retryable=exc.code >= 500) from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"cannot reach llama-server at {self.host}: {exc.reason}", backend=self.name, retryable=True) from exc
        except TimeoutError as exc:
            raise LLMError(f"llama-server timed out after {timeout or self.timeout:.0f}s", backend=self.name, retryable=True) from exc
        if stream:
            return response
        with response:
            body = response.read().decode("utf-8", errors="replace")
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise LLMError("llama-server returned unparseable JSON", backend=self.name, retryable=False) from exc

    def status(self, force: bool = False) -> BackendStatus:
        if not force and self._cached_status and time.time() - self._status_ts < 15:
            return self._cached_status
        started = time.monotonic()
        try:
            payload = self._request("/v1/models", timeout=6.0)
            models = [entry.get("id", "") for entry in payload.get("data", []) if entry.get("id")]
            latency = (time.monotonic() - started) * 1000
            status = BackendStatus(
                name=self.name,
                kind=self.kind,
                available=True,
                detail=f"reachable · {len(models)} model(s) loaded" if models else "reachable (no model reported)",
                models=models,
                endpoint=self.host,
                latency_ms=round(latency, 1),
            )
        except LLMError as exc:
            status = BackendStatus(
                name=self.name,
                kind=self.kind,
                available=False,
                detail="not reachable — start llama-server",
                endpoint=self.host,
                error=str(exc),
            )
        self._cached_status = status
        self._status_ts = time.time()
        return status

    def list_models(self) -> list[str]:
        payload = self._request("/v1/models", timeout=10.0)
        return [entry.get("id", "") for entry in payload.get("data", []) if entry.get("id")]

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
        model: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        target = model or self.model or (self.list_models() or [""])[0]
        if not target:
            raise LLMError("llama-server reports no loaded model", backend=self.name, retryable=False)
        budget = enforce_budget(messages, self.context_tokens)
        payload: dict[str, Any] = {
            "model": target,
            "messages": [message.as_dict() for message in budget],
            "stream": True,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or 1024,
        }
        if stop:
            payload["stop"] = list(stop)
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = [tool.as_dict() for tool in tools]
            payload["tool_choice"] = "auto"

        started = time.monotonic()
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage = {"prompt": 0, "completion": 0}
        finish_reason = ""

        def _attempt() -> None:
            response = self._request("/v1/chat/completions", payload=payload, method="POST", timeout=timeout or self.timeout, stream=True)
            with response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    chunk_text = line[5:].strip()
                    if chunk_text in ("[DONE]", ""):
                        continue
                    try:
                        chunk = json.loads(chunk_text)
                    except json.JSONDecodeError:
                        continue
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta") or {}
                        piece = delta.get("content") or ""
                        if piece:
                            text_parts.append(piece)
                            if on_token:
                                try:
                                    on_token(piece)
                                except Exception:  # pragma: no cover
                                    pass
                        for raw_call in delta.get("tool_calls") or []:
                            call = ToolCall.from_dict(raw_call)
                            if call.name:
                                tool_calls.append(call)
                        if choice.get("finish_reason"):
                            finish_reason = str(choice["finish_reason"])
                    if chunk.get("usage"):
                        usage["prompt"] = int(chunk["usage"].get("prompt_tokens") or 0)
                        usage["completion"] = int(chunk["usage"].get("completion_tokens") or 0)

        self._retry.run(_attempt)
        return LLMResponse(
            text="".join(text_parts),
            backend=self.name,
            model=target,
            tool_calls=tool_calls,
            prompt_tokens=usage["prompt"],
            completion_tokens=usage["completion"],
            duration_s=time.monotonic() - started,
            finish_reason=finish_reason,
        )
