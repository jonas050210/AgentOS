"""Ollama backend.

Talks to a local Ollama server over its HTTP API using ``urllib`` only, so CodeMind needs no
third-party HTTP client. Supports streaming, tool calling, model listing, model details and
downloads with progress reporting.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator, Sequence

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
from codemind.utils.redaction import GLOBAL_REDACTOR

log = get_logger("llm.ollama")


class OllamaBackend:
    """Local Ollama server (https://ollama.com)."""

    kind = "ollama"

    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        model: str = "",
        *,
        context_tokens: int = 8192,
        temperature: float = 0.2,
        keep_alive: str = "10m",
        timeout: float = 180.0,
        retries: int = 2,
    ) -> None:
        self.host = host.rstrip("/")
        self.name = "ollama"
        self.model = model
        self.context_tokens = context_tokens
        self.temperature = temperature
        self.keep_alive = keep_alive
        self.timeout = timeout
        self._retry = RetryPolicy(attempts=retries)
        self._cached_status: BackendStatus | None = None
        self._status_ts = 0.0

    # -- http helpers -----------------------------------------------------
    def _request(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        method: str = "GET",
        timeout: float | None = None,
        stream: bool = False,
    ) -> Any:
        url = f"{self.host}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        try:
            response = urllib.request.urlopen(request, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:400]
            raise LLMError(f"ollama returned HTTP {exc.code}: {body}", backend=self.name, retryable=exc.code >= 500) from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"cannot reach ollama at {self.host}: {exc.reason}", backend=self.name, retryable=True) from exc
        except TimeoutError as exc:
            raise LLMError(f"ollama request timed out after {timeout or self.timeout:.0f}s", backend=self.name, retryable=True) from exc
        if stream:
            return _JsonLineStream(response)
        with response:
            body = response.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body) if body.strip() else {}
        except json.JSONDecodeError as exc:
            raise LLMError(f"ollama sent an unparseable response: {body[:200]}", backend=self.name, retryable=False) from exc

    # -- status -----------------------------------------------------------
    def status(self, force: bool = False) -> BackendStatus:
        if not force and self._cached_status and time.time() - self._status_ts < 15:
            return self._cached_status
        started = time.monotonic()
        try:
            version = self._request("/api/version", timeout=6.0)
            models = self.list_models()
            latency = (time.monotonic() - started) * 1000
            detail = f"reachable · {len(models)} model(s) installed"
            if self.model and models and not any(m.split(":")[0] == self.model.split(":")[0] for m in models):
                detail += f" · configured model '{self.model}' is not installed"
            status = BackendStatus(
                name=self.name,
                kind=self.kind,
                available=True,
                detail=detail,
                models=models,
                endpoint=self.host,
                latency_ms=round(latency, 1),
                version=str(version.get("version", "")),
            )
        except LLMError as exc:
            status = BackendStatus(
                name=self.name,
                kind=self.kind,
                available=False,
                detail="not reachable — start the Ollama service (`ollama serve`)",
                endpoint=self.host,
                error=str(exc),
            )
        self._cached_status = status
        self._status_ts = time.time()
        return status

    def list_models(self) -> list[str]:
        payload = self._request("/api/tags", timeout=10.0)
        return sorted(entry.get("name", "") for entry in payload.get("models", []) if entry.get("name"))

    def model_details(self, model: str) -> dict[str, Any]:
        info = self._request("/api/show", payload={"name": model}, method="POST", timeout=20.0)
        details = info.get("details") or {}
        return {
            "name": model,
            "family": details.get("family", ""),
            "parameter_size": details.get("parameter_size", ""),
            "quantization": details.get("quantization_level", ""),
            "format": details.get("format", ""),
            "context_length": (info.get("model_info") or {}).get("llama.context_length") or (info.get("model_info") or {}).get("context_length"),
            "size_bytes": info.get("size"),
            "capabilities": info.get("capabilities", []),
            "modified_at": info.get("modified_at"),
            "system": (info.get("system") or "")[:500],
        }

    # -- inference --------------------------------------------------------
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
        target = model or self.model
        if not target:
            raise LLMError("no model configured — choose one in Models", backend=self.name, retryable=False)
        budget = enforce_budget(messages, self.context_tokens)
        payload: dict[str, Any] = {
            "model": target,
            "messages": [message.as_dict() for message in budget],
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature if temperature is None else temperature,
                "num_ctx": self.context_tokens,
                "num_predict": max_tokens or 1024,
            },
        }
        if json_mode:
            payload["format"] = "json"
        if tools:
            payload["tools"] = [tool.as_dict() for tool in tools]
        if stop:
            payload["options"]["stop"] = list(stop)

        started = time.monotonic()
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        counters = {"prompt": 0, "eval": 0}
        finish_reason = ""

        def _attempt() -> None:
            stream = self._request("/api/chat", payload=payload, method="POST", timeout=timeout or self.timeout, stream=True)
            for chunk in stream:
                if "error" in chunk:
                    raise LLMError(f"ollama error: {chunk['error']}", backend=self.name, retryable=False)
                message = chunk.get("message") or {}
                piece = message.get("content") or ""
                if piece:
                    text_parts.append(piece)
                    if on_token:
                        try:
                            on_token(piece)
                        except Exception:  # pragma: no cover
                            pass
                for raw_call in message.get("tool_calls") or []:
                    call = ToolCall.from_dict(raw_call)
                    if call.name:
                        tool_calls.append(call)
                if chunk.get("done"):
                    counters["prompt"] = int(chunk.get("prompt_eval_count") or 0)
                    counters["eval"] = int(chunk.get("eval_count") or 0)
                    finish_reason = str(chunk.get("done_reason") or "stop")

        try:
            self._retry.run(_attempt)
        except LLMError:
            raise
        duration = time.monotonic() - started
        return LLMResponse(
            text="".join(text_parts),
            backend=self.name,
            model=target,
            tool_calls=tool_calls,
            prompt_tokens=counters["prompt"],
            completion_tokens=counters["eval"],
            duration_s=duration,
            finish_reason=finish_reason,
        )

    # -- downloads --------------------------------------------------------
    def pull(self, model: str, on_progress: Callable[[dict[str, Any]], None] | None = None, timeout: float = 3600.0) -> dict[str, Any]:
        """Download a model from the public registry, reporting real progress."""
        try:
            stream = self._request("/api/pull", payload={"model": model, "stream": True}, method="POST", timeout=timeout, stream=True)
        except LLMError:
            raise
        last: dict[str, Any] = {}
        for chunk in stream:
            if "error" in chunk:
                raise LLMError(f"download failed: {chunk['error']}", backend=self.name, retryable=False)
            last = chunk
            if on_progress:
                try:
                    on_progress(chunk)
                except Exception:  # pragma: no cover
                    pass
        return last

    def unload(self, model: str) -> None:
        try:
            self._request("/api/generate", payload={"model": model, "keep_alive": 0}, method="POST", timeout=15.0)
        except LLMError as exc:
            log.debug("unload failed", cm={"error": str(exc)})


class _JsonLineStream:
    """Iterates newline-delimited JSON from an HTTP response."""

    def __init__(self, response: Any) -> None:
        self._response = response

    def __iter__(self) -> Iterator[dict[str, Any]]:
        try:
            for raw in self._response:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    log.debug("skipping unparseable stream line", cm={"line": GLOBAL_REDACTOR.redact(line[:120])})
        finally:
            try:
                self._response.close()
            except Exception:  # pragma: no cover
                pass
