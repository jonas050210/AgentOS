"""Local inference backends.

Only free, locally runnable runtimes are supported: Ollama, llama.cpp's ``llama-server``, and
CodeMind's own deterministic planner. There is no paid API client anywhere in this package,
by design - the brief asks for local inference and that is what the code does.
"""

from codemind.llm.base import (
    BackendStatus,
    ChatMessage,
    LLMBackend,
    LLMError,
    LLMResponse,
    ToolCall,
    ToolSpec,
    extract_json,
)
from codemind.llm.heuristic import HeuristicBackend
from codemind.llm.llamacpp import LlamaCppBackend
from codemind.llm.manager import ModelManager
from codemind.llm.ollama import OllamaBackend

__all__ = [
    "BackendStatus",
    "ChatMessage",
    "LLMBackend",
    "LLMError",
    "LLMResponse",
    "ToolCall",
    "ToolSpec",
    "extract_json",
    "HeuristicBackend",
    "LlamaCppBackend",
    "OllamaBackend",
    "ModelManager",
]
