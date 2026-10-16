"""Model management: backend resolution, local model discovery, compatibility, downloads.

The manager answers three practical questions for the UI and the agent:

1. *What can answer right now?* — ``resolve_backend()`` prefers a configured model, then any
   reachable local runtime, and finally the built-in deterministic planner. It never silently
   pretends a model exists.
2. *What could run on this machine?* — ``catalog()`` merges discovered local files, Ollama
   models and a curated set of free open-weight coding models, each annotated with whether it
   fits the detected VRAM/RAM and why.
3. *What is downloading?* — ``download()`` runs a real pull in a worker thread and streams
   byte-level progress onto the event bus.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from codemind.config import Config
from codemind.events import BUS, Topics
from codemind.llm.base import BackendStatus, LLMBackend, LLMError
from codemind.llm.heuristic import HeuristicBackend
from codemind.llm.llamacpp import LlamaCppBackend
from codemind.llm.ollama import OllamaBackend
from codemind.logging_setup import get_logger
from codemind.system.hardware import HardwareProfile
from codemind.utils.files import human_bytes
from codemind.utils.redaction import GLOBAL_REDACTOR

log = get_logger("llm.manager")

# Bytes of weights per parameter for common GGUF quantizations (approximate but honest).
QUANT_BYTES_PER_PARAM = {
    "q2_k": 0.35, "q3_k_s": 0.45, "q3_k_m": 0.48, "q4_0": 0.55, "q4_k_s": 0.56, "q4_k_m": 0.60,
    "q5_k_s": 0.68, "q5_k_m": 0.72, "q6_k": 0.82, "q8_0": 1.06, "f16": 2.0, "bf16": 2.0, "fp32": 4.0,
    "4bit": 0.6, "8bit": 1.06,
}

SUGGESTED_MODELS: list[dict[str, Any]] = [
    {
        "name": "qwen2.5-coder:1.5b-instruct-q4_K_M",
        "family": "Qwen2.5-Coder",
        "params_b": 1.5,
        "quant": "q4_k_m",
        "context": 32768,
        "license": "Apache-2.0",
        "notes": "Fast, small, strong at single-file edits. Ideal when VRAM is tight and training data comes from your own traces.",
        "source": "ollama:qwen2.5-coder:1.5b-instruct-q4_K_M",
    },
    {
        "name": "qwen2.5-coder:3b-instruct-q4_K_M",
        "family": "Qwen2.5-Coder",
        "params_b": 3.0,
        "quant": "q4_k_m",
        "context": 32768,
        "license": "Qwen Research",
        "notes": "Best quality per gigabyte for a 4–8 GB GPU; good at multi-file reasoning.",
        "source": "ollama:qwen2.5-coder:3b-instruct-q4_K_M",
    },
    {
        "name": "qwen2.5-coder:7b-instruct-q4_K_M",
        "family": "Qwen2.5-Coder",
        "params_b": 7.0,
        "quant": "q4_k_m",
        "context": 32768,
        "license": "Apache-2.0",
        "notes": "The strongest 7B coding model that fits an 8 GB card at 4-bit; leaves little room for the KV cache.",
        "source": "ollama:qwen2.5-coder:7b-instruct-q4_K_M",
    },
    {
        "name": "deepseek-coder-v2:16b-lite-instruct-q4_K_M",
        "family": "DeepSeek-Coder-V2-Lite",
        "params_b": 16.0,
        "quant": "q4_k_m",
        "context": 32768,
        "license": "DeepSeek License",
        "notes": "MoE model: only ~2.4B parameters are active per token, so it is faster than its size suggests - but requires ~10 GB VRAM.",
        "source": "ollama:deepseek-coder-v2:16b-lite-instruct-q4_K_M",
    },
    {
        "name": "qwen2.5-coder:0.5b-instruct",
        "family": "Qwen2.5-Coder",
        "params_b": 0.5,
        "quant": "q4_k_m",
        "context": 32768,
        "license": "Apache-2.0",
        "notes": "Small enough to fine-tune with LoRA/QLoRA on a single 8 GB GPU - the practical base model for your own adapter.",
        "source": "ollama:qwen2.5-coder:0.5b-instruct",
    },
    {
        "name": "llama3.2:3b-instruct-q4_K_M",
        "family": "Llama 3.2",
        "params_b": 3.0,
        "quant": "q4_k_m",
        "context": 131072,
        "license": "Llama 3.2 Community",
        "notes": "General instruction following with a long context; useful for repository Q&A.",
        "source": "ollama:llama3.2:3b-instruct-q4_K_M",
    },
]


def estimate_weights_bytes(params_b: float, quant: str) -> int:
    per_param = QUANT_BYTES_PER_PARAM.get((quant or "").lower(), 0.6)
    return int(params_b * 1e9 * per_param)


def estimate_kv_bytes(params_b: float, context: int, layers: int | None = None) -> int:
    """Rough KV-cache estimate (GQA-aware enough for a compatibility hint)."""
    layer_count = layers or max(12, int(params_b * 8))
    head_dim = 128
    kv_heads = max(2, int((params_b * 8) / 6))
    per_token = layer_count * kv_heads * head_dim * 2 * 2  # k and v, fp16
    return int(per_token * max(512, context) * 0.5)  # assume half the context is used on average


@dataclass
class ModelEntry:
    name: str
    provider: str
    kind: str = "chat"
    size_bytes: int | None = None
    params_b: float | None = None
    quantization: str = ""
    context: int | None = None
    path: str = ""
    installed: bool = False
    fits_vram: bool | None = None
    fits_ram: bool | None = None
    fit_note: str = ""
    license: str = ""
    notes: str = ""
    source: str = ""
    capabilities: list[str] = field(default_factory=list)
    training_ready: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "size_human": human_bytes(self.size_bytes) if self.size_bytes else "—",
            "params_b": self.params_b,
            "quantization": self.quantization,
            "context": self.context,
            "path": self.path,
            "installed": self.installed,
            "fits_vram": self.fits_vram,
            "fits_ram": self.fits_ram,
            "fit_note": self.fit_note,
            "license": self.license,
            "notes": self.notes,
            "source": self.source,
            "capabilities": list(self.capabilities),
            "training_ready": self.training_ready,
        }


def _parse_params_from_name(name: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*[bB]\b", name)
    if match:
        return float(match.group(1))
    match = re.search(r":(\d+(?:\.\d+)?)b", name, flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


def _parse_quant_from_name(name: str) -> str:
    for quant in QUANT_BYTES_PER_PARAM:
        if quant in name.lower():
            return quant
    return ""


class ModelManager:
    """Owns backend instances, the model catalogue and downloads."""

    def __init__(self, config: Config, hardware: HardwareProfile, *, on_change: Callable[[], None] | None = None) -> None:
        self.config = config
        self.hardware = hardware
        self.on_change = on_change
        self._lock = threading.RLock()
        self._ollama = OllamaBackend(
            host=config.models.ollama_host,
            model=config.models.active_model,
            context_tokens=config.models.context_tokens,
            temperature=config.models.temperature,
            keep_alive=config.models.keep_alive,
        )
        self._llama = LlamaCppBackend(
            host=config.models.llama_server_host,
            model=config.models.active_model,
            context_tokens=config.models.context_tokens,
            temperature=config.models.temperature,
        )
        self._heuristic = HeuristicBackend()
        self._active: LLMBackend | None = None
        self._active_reason = ""
        self._downloads: dict[str, dict[str, Any]] = {}
        self._download_threads: list[threading.Thread] = []
        self._server_process: Any = None

    # -- configuration ----------------------------------------------------
    def update_config(self, config: Config) -> None:
        with self._lock:
            self.config = config
            self._ollama.host = config.models.ollama_host.rstrip("/")
            self._ollama.model = config.models.active_model
            self._ollama.context_tokens = config.models.context_tokens
            self._ollama.temperature = config.models.temperature
            self._ollama.keep_alive = config.models.keep_alive
            self._llama.host = config.models.llama_server_host.rstrip("/")
            self._llama.model = config.models.active_model
            self._llama.context_tokens = config.models.context_tokens
            self._active = None

    # -- backends ---------------------------------------------------------
    @property
    def ollama(self) -> OllamaBackend:
        return self._ollama

    @property
    def llama_cpp(self) -> LlamaCppBackend:
        return self._llama

    @property
    def heuristic(self) -> HeuristicBackend:
        return self._heuristic

    def statuses(self, force: bool = False) -> list[BackendStatus]:
        statuses = [self._ollama.status(force=force), self._llama.status(force=force), self._heuristic.status()]
        return statuses

    def resolve_backend(self, *, prefer_model: bool = True, force_probe: bool = False) -> tuple[LLMBackend, str]:
        """Pick the best available backend. Returns (backend, human-readable reason)."""
        with self._lock:
            if self._active is not None and not force_probe:
                return self._active, self._active_reason
            choice = self.config.models.backend
            candidates: list[tuple[LLMBackend, str]] = []
            if choice in ("auto", "ollama"):
                status = self._ollama.status(force=force_probe)
                if status.available:
                    model = self.config.models.active_model or (status.models[0] if status.models else "")
                    if model:
                        self._ollama.model = model
                        candidates.append((self._ollama, f"Ollama at {self.config.models.ollama_host} using {model}"))
            if choice in ("auto", "llama_cpp"):
                status = self._llama.status(force=force_probe)
                if status.available:
                    candidates.append((self._llama, f"llama.cpp server at {self.config.models.llama_server_host}"))
            if choice in ("auto", "heuristic") and not candidates:
                reason = "built-in deterministic planner (no local model reachable)"
                if choice == "auto":
                    ollama_status = self._ollama.status(force=force_probe)
                    if not ollama_status.available:
                        reason += f"; Ollama unreachable ({ollama_status.error or 'not running'})"
                candidates.append((self._heuristic, reason))
            backend, reason = candidates[0] if candidates else (self._heuristic, "built-in deterministic planner")
            if not prefer_model and backend is self._heuristic:
                reason += " — deterministic mode"
            self._active = backend
            self._active_reason = reason
            if self.on_change:
                try:
                    self.on_change()
                except Exception:  # pragma: no cover
                    pass
            return backend, reason

    def active_backend_info(self) -> dict[str, Any]:
        backend, reason = self.resolve_backend()
        return {
            "name": backend.name,
            "kind": backend.kind,
            "model": getattr(backend, "model", ""),
            "reason": reason,
            "is_model": backend.kind != "heuristic",
        }

    # -- catalogue --------------------------------------------------------
    def _local_models(self) -> list[ModelEntry]:
        entries: list[ModelEntry] = []
        for raw in self.config.models.model_dirs:
            base = Path(raw).expanduser()
            if not base.exists():
                continue
            for pattern in ("**/*.gguf", "**/*.safetensors", "**/model.safetensors"):
                for path in base.glob(pattern):
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    name = path.stem if path.name != "model.safetensors" else path.parent.name
                    params = _parse_params_from_name(name)
                    quant = _parse_quant_from_name(name)
                    entries.append(
                        ModelEntry(
                            name=name,
                            provider="local file",
                            size_bytes=size,
                            params_b=params,
                            quantization=quant or ("f16" if path.suffix == ".safetensors" else "unknown"),
                            path=str(path),
                            installed=True,
                            source="disk",
                            notes="Found in a configured model directory.",
                            training_ready=path.suffix == ".safetensors",
                        )
                    )
        return entries

    def _ollama_models(self) -> list[ModelEntry]:
        status = self._ollama.status()
        entries: list[ModelEntry] = []
        for name in status.models:
            details: dict[str, Any] = {}
            try:
                details = self._ollama.model_details(name)
            except LLMError:
                details = {}
            params = _parse_params_from_name(details.get("parameter_size") or name)
            entries.append(
                ModelEntry(
                    name=name,
                    provider="ollama",
                    size_bytes=details.get("size_bytes"),
                    params_b=params,
                    quantization=details.get("quantization") or _parse_quant_from_name(name),
                    context=details.get("context_length") or self.config.models.context_tokens,
                    installed=True,
                    capabilities=list(details.get("capabilities") or []),
                    notes="Installed in the local Ollama store.",
                )
            )
        return entries

    def catalog(self, *, probe: bool = True) -> list[ModelEntry]:
        """Everything installed plus suggestions, each annotated with real compatibility."""
        entries: list[ModelEntry] = []
        seen: set[str] = set()
        for entry in self._local_models() + (self._ollama_models() if probe else []):
            if entry.name in seen:
                continue
            seen.add(entry.name)
            entries.append(entry)
        installed_names = {e.name.lower() for e in entries}
        for suggestion in SUGGESTED_MODELS:
            if suggestion["name"].lower() in installed_names:
                continue
            entries.append(
                ModelEntry(
                    name=suggestion["name"],
                    provider="suggested",
                    size_bytes=estimate_weights_bytes(suggestion["params_b"], suggestion["quant"]),
                    params_b=suggestion["params_b"],
                    quantization=suggestion["quant"],
                    context=suggestion["context"],
                    license=suggestion["license"],
                    notes=suggestion["notes"],
                    source=suggestion["source"],
                    installed=False,
                )
            )
        for entry in entries:
            self.annotate_fit(entry)
        entries.sort(key=lambda e: (not e.installed, e.params_b or 99))
        return entries

    def annotate_fit(self, entry: ModelEntry) -> ModelEntry:
        """Decide whether a model fits the detected hardware, with a reason."""
        vram = self.hardware.vram_total_mb * 1024 * 1024
        ram = self.hardware.ram_total_mb * 1024 * 1024
        weights = entry.size_bytes or (estimate_weights_bytes(entry.params_b, entry.quantization) if entry.params_b else None)
        if not weights:
            entry.fits_vram = None
            entry.fits_ram = None
            entry.fit_note = "Size unknown — cannot judge compatibility."
            return entry
        entry.size_bytes = weights
        params_b = entry.params_b or (weights / (QUANT_BYTES_PER_PARAM.get(entry.quantization.lower(), 0.6) * 1e9))
        kv = estimate_kv_bytes(params_b, entry.context or self.config.models.context_tokens)
        reserve = self.config.resources.vram_reserve_mb * 1024 * 1024
        if vram and self.hardware.has_cuda:
            entry.fits_vram = (weights + kv + reserve) <= vram
            if entry.fits_vram:
                headroom = vram - weights - kv - reserve
                entry.fit_note = f"Fits in VRAM with {human_bytes(headroom)} to spare (KV cache for ~{int((entry.context or 0) / 2)} tokens included)."
            else:
                entry.fit_note = (
                    f"Needs about {human_bytes(weights + kv)}, larger than the "
                    f"{human_bytes(vram - reserve)} usable VRAM budget; it would spill to the CPU."
                )
        else:
            entry.fits_vram = False
            entry.fit_note = "No CUDA GPU detected: this model would run on the CPU."
        entry.fits_ram = (weights + max(2 * 1024**3, kv)) <= (ram - self.config.resources.ram_reserve_mb * 1024 * 1024)
        if not entry.fits_ram:
            entry.fit_note += " It also exceeds the RAM budget."
        entry.training_ready = bool(entry.training_ready or entry.provider == "local file")
        return entry

    def compatibility_report(self) -> dict[str, Any]:
        plan = self.hardware.training_plan()
        return {
            "hardware": self.hardware.summary_line(),
            "tier": self.hardware.tier(),
            "gpus": [gpu.as_dict() for gpu in self.hardware.gpus],
            "vram_total_mb": self.hardware.vram_total_mb,
            "ram_total_mb": self.hardware.ram_total_mb,
            "ml_stack": self.hardware.ml,
            "recommended_model": self.hardware.recommended_model(),
            "training_plan": plan,
            "notes": list(self.hardware.notes),
        }

    # -- selection --------------------------------------------------------
    def select_model(self, name: str, backend: str | None = None) -> dict[str, Any]:
        """Point CodeMind at a specific model and remember the choice."""
        values: dict[str, Any] = {"models.active_model": name}
        if backend:
            values["models.backend"] = backend
        self.config.apply(values)
        self.update_config(self.config.copy())
        self.resolve_backend(force_probe=True)
        log.info("active model changed", cm={"model": name, "backend": backend or self.config.models.backend})
        BUS.publish(Topics.MODEL_STATE, action="selected", model=name, backend=self.config.models.backend)
        return self.active_backend_info()

    # -- downloads --------------------------------------------------------
    def downloads(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._downloads.values())

    def is_downloading(self, name: str) -> bool:
        with self._lock:
            entry = self._downloads.get(name)
            return bool(entry and entry.get("status") == "running")

    def download(self, name: str, *, source: str = "") -> dict[str, Any]:
        """Start a download in the background. Ollama tags pull from the local daemon."""
        with self._lock:
            existing = self._downloads.get(name)
            if existing and existing.get("status") == "running":
                return existing
            record = {
                "name": name,
                "source": source or f"ollama:{name}",
                "status": "running",
                "started_at": time.time(),
                "completed": 0,
                "total": 0,
                "percent": 0.0,
                "message": "starting",
                "error": "",
            }
            self._downloads[name] = record
        BUS.publish(Topics.MODEL_DOWNLOAD, action="started", name=name, source=record["source"])
        thread = threading.Thread(target=self._download_worker, args=(name, record), name=f"download-{name[:20]}", daemon=True)
        thread.start()
        self._download_threads.append(thread)
        return record

    def _download_worker(self, name: str, record: dict[str, Any]) -> None:
        try:
            if not self.config.models.auto_pull:
                raise LLMError("model downloads are disabled in Settings → Models", backend="manager", retryable=False)

            def on_progress(chunk: dict[str, Any]) -> None:
                total = int(chunk.get("total") or 0)
                completed = int(chunk.get("completed") or 0)
                with self._lock:
                    record["total"] = total
                    record["completed"] = completed
                    record["percent"] = round(100.0 * completed / total, 2) if total else record.get("percent", 0.0)
                    record["message"] = str(chunk.get("status") or record.get("message"))
                BUS.publish(
                    Topics.MODEL_DOWNLOAD,
                    action="progress",
                    name=name,
                    percent=record["percent"],
                    completed=completed,
                    total=total,
                    message=record["message"],
                )

            self._ollama.pull(name, on_progress=on_progress)
            with self._lock:
                record["status"] = "completed"
                record["percent"] = 100.0
                record["message"] = "ready"
                record["finished_at"] = time.time()
            BUS.publish(Topics.MODEL_DOWNLOAD, action="completed", name=name)
            log.info("model download finished", cm={"model": name})
        except Exception as exc:
            with self._lock:
                record["status"] = "failed"
                record["error"] = GLOBAL_REDACTOR.redact(str(exc))[:400]
                record["finished_at"] = time.time()
            BUS.publish(Topics.MODEL_DOWNLOAD, action="failed", name=name, error=record["error"])
            log.warning("model download failed", cm={"model": name, "error": record["error"]})

    def download_hf_file(self, url: str, target: Path, *, on_progress: Callable[[int, int], None] | None = None) -> Path:
        """Resume-capable download of a single file (e.g. a GGUF from Hugging Face)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.stat().st_size if target.exists() else 0
        request = urllib.request.Request(url)
        if existing:
            request.add_header("Range", f"bytes={existing}-")
        try:
            response = urllib.request.urlopen(request, timeout=60)
        except urllib.error.URLError as exc:
            raise LLMError(f"download failed: {exc.reason}", backend="manager") from exc
        total = int(response.headers.get("Content-Length") or 0) + existing
        mode = "ab" if existing and response.status == 206 else "wb"
        written = existing
        with open(target, mode) as handle:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                if on_progress:
                    try:
                        on_progress(written, total)
                    except Exception:  # pragma: no cover
                        pass
        return target

    # -- local server -----------------------------------------------------
    def start_llama_server(self, model_path: str, *, port: int = 8080, context: int = 8192, gpu_layers: int = -1) -> dict[str, Any]:
        """Launch llama-server for a local GGUF file (real process, real port)."""
        import shutil

        from codemind.utils.process import StreamingProcess, build_env

        binary = shutil.which("llama-server") or shutil.which("llama-cpp-server")
        if not binary:
            raise LLMError("llama-server is not installed; install llama.cpp or use Ollama", backend="manager", retryable=False)
        path = Path(model_path).expanduser()
        if not path.exists():
            raise LLMError(f"model file not found: {path}", backend="manager", retryable=False)
        if self._server_process is not None and self._server_process.running:
            self._server_process.terminate()
        argv = [binary, "-m", str(path), "--port", str(port), "-c", str(context)]
        if gpu_layers >= 0:
            argv += ["-ngl", str(gpu_layers)]
        process = StreamingProcess(argv=argv, cwd=str(path.parent), env=build_env())
        process.start()
        self._server_process = process
        self.config.apply({"models.llama_server_host": f"http://127.0.0.1:{port}"})
        self.update_config(self.config.copy())
        log.info("llama-server started", cm={"pid": process.pid, "model": str(path)})
        return process.info()

    def stop_llama_server(self) -> None:
        if self._server_process is not None:
            self._server_process.terminate()
            self._server_process = None

    def server_info(self) -> dict[str, Any] | None:
        if self._server_process is None:
            return None
        return self._server_process.info()

    # -- housekeeping -----------------------------------------------------
    def shutdown(self) -> None:
        for thread in list(self._download_threads):
            thread.join(timeout=0.2)
        self.stop_llama_server()
