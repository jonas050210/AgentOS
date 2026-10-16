"""Training methods: what can really be trained here, and what it costs.

Each method declares its requirements and reports availability honestly:

* ``adapter_lite`` — always available; pure-Python neural adapter, trains in-process, useful
  for tool selection and routing decisions, verified by accuracy on held-out traces.
* ``qlora`` / ``lora`` — LoRA adapters for a local causal LM; requires torch + peft
  (+ bitsandbytes for 4-bit) and, for QLoRA, a CUDA GPU with enough VRAM.
* ``retrieval_index`` — builds a BM25 index over the project knowledge and memory; not a
  gradient method but a real, measurable improvement for retrieval.

The engine refuses to start a run whose requirements are missing, and says exactly what to
install.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from codemind.config import Config
from codemind.system.hardware import HardwareProfile


@dataclass
class MethodInfo:
    name: str
    title: str
    kind: str
    summary: str
    available: bool
    requirements: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    estimated: str = ""
    requires_gpu: bool = False
    trains_weights: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "kind": self.kind,
            "summary": self.summary,
            "available": self.available,
            "requirements": self.requirements,
            "limitations": self.limitations,
            "estimated": self.estimated,
            "requires_gpu": self.requires_gpu,
            "trains_weights": self.trains_weights,
        }


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _torch_cuda_available() -> bool:
    """Ask a subprocess, so a broken torch install cannot take the GUI with it."""
    from codemind.utils.process import run_command

    result = run_command(
        [sys.executable, "-c", "import torch,sys;sys.stdout.write('cuda' if torch.cuda.is_available() else 'cpu')"],
        timeout=40,
    )
    return result.ok and result.stdout.strip().endswith("cuda")


def evaluate_methods(config: Config, hardware: HardwareProfile) -> list[MethodInfo]:
    """Report every method with its true availability on this machine."""
    torch_present = module_available("torch")
    transformers_present = module_available("transformers")
    peft_present = module_available("peft")
    bnb_present = module_available("bitsandbytes")
    cuda_present = bool(hardware.has_cuda)
    vram = hardware.vram_total_mb
    plan = hardware.training_plan()

    methods: list[MethodInfo] = []

    adapter_requirements = ["Python 3.11+ (standard library only)"]
    methods.append(
        MethodInfo(
            name="adapter_lite",
            title="CodeMind adapter (built-in, no dependencies)",
            kind="neural adapter",
            summary=(
                "Trains a small feed-forward network with real backpropagation over hashed n-gram features. "
                "It learns the decisions the agent makes all day: which tool an instruction needs, and which "
                "category of change it is. Runs on the CPU of any machine that can run CodeMind itself."
            ),
            available=True,
            requirements=adapter_requirements,
            limitations=[
                "It is not a language model: it cannot write prose or code, it answers routing/decision questions.",
                "Small capacity by design (hashed features + one hidden layer), so it plateaus earlier than LoRA.",
            ],
            estimated="seconds to a few minutes, a few hundred MB of RAM",
            requires_gpu=False,
        )
    )

    retrieval_requirements = ["Python 3.11+ (standard library only)"]
    methods.append(
        MethodInfo(
            name="retrieval_index",
            title="Retrieval index (BM25 over your knowledge base)",
            kind="index",
            summary=(
                "Builds a real BM25 index over the project knowledge and memories, tunes its parameters on held-out "
                "queries and reports recall. It improves what the agent sees when it starts a task."
            ),
            available=True,
            requirements=retrieval_requirements,
            limitations=["Not a gradient method: it changes what the agent retrieves, not the weights of a model."],
            estimated="seconds",
            requires_gpu=False,
            trains_weights=False,
        )
    )

    missing: list[str] = []
    if not torch_present:
        missing.append("PyTorch (pip install torch --index-url https://download.pytorch.org/whl/cu121)")
    if not transformers_present:
        missing.append("transformers (pip install transformers)")
    if not peft_present:
        missing.append("peft (pip install peft)")
    if not bnb_present:
        missing.append("bitsandbytes for 4-bit QLoRA (pip install bitsandbytes)")

    lora_available = torch_present and transformers_present and peft_present
    methods.append(
        MethodInfo(
            name="qlora",
            title="QLoRA fine-tune (4-bit LoRA on a local model)",
            kind="gradient / PEFT",
            summary=(
                "Genuine 4-bit quantised LoRA fine-tuning of a small local causal LM (for example "
                "Qwen2.5-Coder-1.5B-Instruct) on your own verified task traces, with checkpoints and resume."
            ),
            available=lora_available and bnb_present and cuda_present,
            requirements=(["NVIDIA GPU with CUDA"] if not cuda_present else []) + missing,
            limitations=[
                "8 GB of VRAM fits a 1.5B base at 4-bit with short sequences; a 7B base needs CPU offload and is much slower.",
                "Quality depends on how many verified traces you accumulate — a few dozen examples will move routing, not coding ability.",
            ],
            estimated=f"~{plan.get('model', '1.5B')} · {'feasible on this GPU' if cuda_present and vram >= 6000 else 'CPU or larger GPU required'}"
            + (f" · {vram} MB VRAM" if vram else ""),
            requires_gpu=True,
        )
    )

    methods.append(
        MethodInfo(
            name="lora",
            title="LoRA fine-tune (fp16/bf16, GPU or CPU)",
            kind="gradient / PEFT",
            summary="LoRA fine-tuning without 4-bit quantisation: higher fidelity, larger memory footprint.",
            available=lora_available,
            requirements=["CUDA GPU recommended; CPU works but is slow"] + missing,
            limitations=["A full-precision 7B model needs ~16 GB VRAM before the optimizer state is counted."],
            estimated="minutes per 100 steps on a GPU, hours on a CPU",
            requires_gpu=False,
        )
    )
    return methods


def method_info(name: str, config: Config, hardware: HardwareProfile) -> MethodInfo | None:
    for info in evaluate_methods(config, hardware):
        if info.name == name:
            return info
    return None


def resolve_auto_method(config: Config, hardware: HardwareProfile, *, has_traces: bool = True) -> str:
    """Pick the method that actually works here: QLoRA when possible, otherwise the adapter."""
    preference = config.training.prefer_method
    if preference != "auto":
        return preference
    methods = {info.name: info for info in evaluate_methods(config, hardware)}
    if not has_traces:
        return "retrieval_index"
    if methods.get("qlora") and methods["qlora"].available:
        return "qlora"
    return "adapter_lite"


def collect_env_report() -> dict[str, Any]:
    """Versions of the toolchain, for the Training view's diagnostics panel."""
    report: dict[str, Any] = {"python": sys.version.split()[0], "executable": sys.executable, "nvidia_smi": shutil.which("nvidia-smi") or ""}
    for name in ("torch", "transformers", "peft", "bitsandbytes", "datasets", "accelerate", "trl"):
        if not module_available(name):
            report[name] = None
            continue
        try:
            module = __import__(name)
            report[name] = getattr(module, "__version__", "installed")
        except Exception as exc:  # pragma: no cover - a broken install
            report[name] = f"import error: {exc}"
    return report
