"""Hardware detection and capability assessment.

The result drives the whole product: which models are suggested, how large a training run
may be, how many CPU threads the sandbox may use, whether LoRA/QLoRA is realistic and what
to do instead when it is not. Detection is best-effort and never raises.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codemind.logging_setup import get_logger
from codemind.utils.files import disk_usage, human_bytes
from codemind.utils.process import build_env, run_command

log = get_logger("system.hardware")

_GPU_QUERY = "index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,driver_version,compute_cap"
_ML_PROBE = """
import json, sys
report = {"torch": False, "cuda": False, "peft": False, "transformers": False, "datasets": False,
          "bitsandbytes": False, "accelerate": False, "trl": False, "numpy": False, "psutil": False}
try:
    import numpy  # noqa
    report["numpy"] = True
except Exception:
    pass
try:
    import psutil  # noqa
    report["psutil"] = True
except Exception:
    pass
try:
    import torch
    report["torch"] = True
    report["torch_version"] = torch.__version__
    report["cuda"] = bool(torch.cuda.is_available())
    if report["cuda"]:
        report["cuda_version"] = torch.version.cuda or ""
        report["device_name"] = torch.cuda.get_device_name(0)
        props = torch.cuda.get_device_properties(0)
        report["vram_bytes"] = int(props.total_memory)
        report["capability"] = list(torch.cuda.get_device_capability(0))
        report["bf16"] = bool(torch.cuda.is_bf16_supported())
except Exception as exc:
    report["torch_error"] = str(exc)[:200]
for name in ("transformers", "datasets", "peft", "bitsandbytes", "accelerate", "trl"):
    try:
        module = __import__(name)
        report[name] = True
        report[name + "_version"] = getattr(module, "__version__", "?")
    except Exception:
        pass
print(json.dumps(report))
"""


@dataclass
class GpuInfo:
    index: int
    name: str
    vram_total_mb: int
    vram_used_mb: int
    vram_free_mb: int
    utilization: float | None
    temperature_c: float | None
    driver: str
    compute_capability: str

    @property
    def is_nvidia(self) -> bool:
        return bool(self.name)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "vram_total_mb": self.vram_total_mb,
            "vram_used_mb": self.vram_used_mb,
            "vram_free_mb": self.vram_free_mb,
            "utilization": self.utilization,
            "temperature_c": self.temperature_c,
            "driver": self.driver,
            "compute_capability": self.compute_capability,
        }


@dataclass
class HardwareProfile:
    cpu_model: str
    physical_cores: int
    logical_cores: int
    cpu_max_mhz: int | None
    ram_total_mb: int
    ram_available_mb: int
    swap_total_mb: int
    gpus: list[GpuInfo] = field(default_factory=list)
    disks: list[dict[str, Any]] = field(default_factory=list)
    ml: dict[str, Any] = field(default_factory=dict)
    os_name: str = ""
    kernel: str = ""
    python: str = ""
    probed_at: float = field(default_factory=time.time)
    notes: list[str] = field(default_factory=list)

    # -- derived ----------------------------------------------------------
    @property
    def vram_total_mb(self) -> int:
        return max([gpu.vram_total_mb for gpu in self.gpus], default=0)

    @property
    def has_cuda(self) -> bool:
        return bool(self.ml.get("cuda")) and any(gpu.vram_total_mb > 0 for gpu in self.gpus)

    @property
    def gpu_summary(self) -> str:
        if not self.gpus:
            return "No CUDA GPU detected"
        primary = self.gpus[0]
        return f"{primary.name} · {primary.vram_total_mb} MB VRAM"

    def tier(self) -> str:
        """Coarse capability tier used to pick default models and run sizes."""
        vram = self.vram_total_mb
        cores = self.logical_cores
        ram = self.ram_total_mb
        score = 0
        if vram >= 20000:
            score += 4
        elif vram >= 11000:
            score += 3
        elif vram >= 7000:
            score += 2
        elif vram >= 4000:
            score += 1
        if cores >= 16:
            score += 2
        elif cores >= 8:
            score += 1
        if ram >= 32000:
            score += 2
        elif ram >= 16000:
            score += 1
        if score >= 7:
            return "workstation"
        if score >= 5:
            return "performance"
        if score >= 3:
            return "balanced"
        if score >= 1:
            return "limited"
        return "minimal"

    def recommended_model(self) -> dict[str, Any]:
        """Best default local model for this machine, with an honest rationale."""
        vram = self.vram_total_mb
        ram = self.ram_total_mb
        if vram >= 12000:
            return {
                "name": "qwen2.5-coder:7b-instruct-q5_K_M",
                "params": "7B", "quant": "q5_K_M", "est_vram_mb": 6800,
                "why": "7B runs fully on the GPU with headroom, giving the best code quality per token.",
            }
        if vram >= 7000:
            return {
                "name": "qwen2.5-coder:7b-instruct-q4_K_M",
                "params": "7B", "quant": "q4_K_M", "est_vram_mb": 5200,
                "why": "7B at 4-bit fits in ~8 GB VRAM and leaves room for the KV cache and the desktop.",
            }
        if vram >= 4000:
            return {
                "name": "qwen2.5-coder:3b-instruct-q4_K_M",
                "params": "3B", "quant": "q4_K_M", "est_vram_mb": 2600,
                "why": "3B keeps the whole model resident and still follows structured edit instructions.",
            }
        if ram >= 16000:
            return {
                "name": "qwen2.5-coder:1.5b-instruct-q4_K_M",
                "params": "1.5B", "quant": "q4_K_M", "est_vram_mb": 0,
                "why": "No usable GPU: a 1.5B model on CPU keeps planning responsive, and the built-in "
                        "heuristic planner covers the rest.",
            }
        return {
            "name": "heuristic",
            "params": "—", "quant": "—", "est_vram_mb": 0,
            "why": "This machine cannot host a local model comfortably; CodeMind falls back to its "
                    "deterministic planner and still edits code, runs tests and verifies results.",
        }

    def training_plan(self) -> dict[str, Any]:
        """What kind of real training this hardware supports."""
        vram = self.vram_total_mb
        stack_ok = bool(self.ml.get("torch")) and bool(self.ml.get("peft"))
        if vram >= 7000 and stack_ok:
            return {
                "method": "qlora",
                "model": "Qwen2.5-Coder-1.5B-Instruct",
                "quantization": "4bit",
                "max_seq_len": 1024,
                "batch": 1,
                "grad_accum": 8,
                "feasible": True,
                "notes": "4-bit QLoRA fits comfortably in 8 GB VRAM for a 1.5B model; a 7B base would need "
                         "more VRAM or CPU offload and would be slow.",
            }
        if vram >= 4000 and stack_ok:
            return {
                "method": "qlora",
                "model": "Qwen2.5-Coder-0.5B-Instruct",
                "quantization": "4bit",
                "max_seq_len": 768,
                "batch": 1,
                "grad_accum": 16,
                "feasible": True,
                "notes": "Small VRAM budget: a 0.5B base with short sequences keeps the run stable.",
            }
        if stack_ok and self.ram_total_mb >= 8000:
            return {
                "method": "lora",
                "model": "Qwen2.5-Coder-0.5B-Instruct",
                "quantization": "fp32",
                "max_seq_len": 512,
                "batch": 1,
                "grad_accum": 16,
                "feasible": True,
                "notes": "CPU-only LoRA on a small base model: real gradient updates, but expect a few "
                         "tokens per second rather than thousands.",
            }
        return {
            "method": "adapter_lite",
            "model": "built-in",
            "quantization": "—",
            "max_seq_len": 2048,
            "batch": 32,
            "grad_accum": 1,
            "feasible": True,
            "notes": "The PyTorch stack is missing (or the machine is too small for LoRA). CodeMind falls "
                     "back to its dependency-free adapter: real backprop over hashed n-gram features that "
                     "learns tool selection and answer routing from your own task traces.",
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "cpu_model": self.cpu_model,
            "physical_cores": self.physical_cores,
            "logical_cores": self.logical_cores,
            "cpu_max_mhz": self.cpu_max_mhz,
            "ram_total_mb": self.ram_total_mb,
            "ram_available_mb": self.ram_available_mb,
            "swap_total_mb": self.swap_total_mb,
            "gpus": [gpu.as_dict() for gpu in self.gpus],
            "disks": self.disks,
            "ml": self.ml,
            "os_name": self.os_name,
            "kernel": self.kernel,
            "python": self.python,
            "tier": self.tier(),
            "gpu_summary": self.gpu_summary,
            "recommended_model": self.recommended_model(),
            "training_plan": self.training_plan(),
            "notes": self.notes,
            "probed_at": self.probed_at,
        }

    def summary_line(self) -> str:
        return (
            f"{self.cpu_model or 'CPU'} · {self.logical_cores} threads · "
            f"{human_bytes(self.ram_total_mb * 1024 * 1024)} RAM · {self.gpu_summary}"
        )


def _cpu_info() -> tuple[str, int, int, int | None]:
    model = ""
    physical = logical = 0
    max_mhz: int | None = None
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if text:
        models = re.findall(r"^model name\s*:\s*(.+)$", text, flags=re.MULTILINE)
        if models:
            model = models[0].strip()
        physical_ids = set()
        core_ids = set()
        for block in text.split("\n\n"):
            pid = re.search(r"^physical id\s*:\s*(\d+)", block, flags=re.MULTILINE)
            cid = re.search(r"^core id\s*:\s*(\d+)", block, flags=re.MULTILINE)
            if pid and cid:
                physical_ids.add(pid.group(1))
                core_ids.add((pid.group(1), cid.group(1)))
        logical = len(re.findall(r"^processor\s*:", text, flags=re.MULTILINE))
        physical = len(core_ids) if core_ids else logical
    if not logical:
        logical = os.cpu_count() or 1
    if not physical:
        physical = logical
    try:
        mhz = Path("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq").read_text().strip()
        max_mhz = int(int(mhz) / 1000)
    except (OSError, ValueError):
        try:
            text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
            found = re.findall(r"^cpu MHz\s*:\s*([\d.]+)", text, flags=re.MULTILINE)
            max_mhz = int(float(max(found))) if found else None
        except (OSError, ValueError):
            max_mhz = None
    return model or platform.processor() or "unknown CPU", physical, logical, max_mhz


def _meminfo() -> tuple[int, int, int]:
    total = available = swap = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            value = int(re.sub(r"[^\d]", "", rest) or 0) // 1024
            if key == "MemTotal":
                total = value
            elif key == "MemAvailable":
                available = value
            elif key == "SwapTotal":
                swap = value
    except (OSError, ValueError):
        pass
    if not total:  # pragma: no cover - non-Linux fallback
        total = 8192
        available = 4096
    return total, available, swap


def _nvidia_gpus() -> list[GpuInfo]:
    if not shutil.which("nvidia-smi"):
        return []
    result = run_command(
        ["nvidia-smi", f"--query-gpu={_GPU_QUERY}", "--format=csv,noheader,nounits"],
        timeout=12,
        env=build_env(),
    )
    if not result.ok:
        return []
    gpus: list[GpuInfo] = []
    for line in result.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            gpus.append(
                GpuInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    vram_total_mb=int(float(parts[2])),
                    vram_used_mb=int(float(parts[3])),
                    vram_free_mb=int(float(parts[4])),
                    utilization=_maybe_float(parts[5]),
                    temperature_c=_maybe_float(parts[6]),
                    driver=parts[7] if len(parts) > 7 else "",
                    compute_capability=parts[8] if len(parts) > 8 else "",
                )
            )
        except (ValueError, IndexError):
            continue
    return gpus


def _amd_gpus() -> list[GpuInfo]:
    """ROCm boards via rocm-smi, when present."""
    if not shutil.which("rocm-smi"):
        return []
    result = run_command(["rocm-smi", "--showmeminfo", "vram", "--showuse", "--csv"], timeout=10)
    if not result.ok:
        return []
    gpus: list[GpuInfo] = []
    for line in result.stdout.splitlines():
        if "card" not in line.lower():
            continue
        numbers = [int(n) for n in re.findall(r"\d+", line)]
        if len(numbers) >= 3:
            total_b = numbers[1]
            used_b = numbers[2]
            gpus.append(
                GpuInfo(
                    index=len(gpus),
                    name="AMD GPU",
                    vram_total_mb=total_b // (1024 * 1024),
                    vram_used_mb=used_b // (1024 * 1024),
                    vram_free_mb=max(0, (total_b - used_b) // (1024 * 1024)),
                    utilization=float(numbers[3]) if len(numbers) > 3 else None,
                    temperature_c=None,
                    driver="rocm",
                    compute_capability="",
                )
            )
    return gpus


def _maybe_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ml_stack_report(timeout: float = 25.0, python: str | None = None) -> dict[str, Any]:
    """Import the ML stack in a subprocess; never crashes the caller."""
    interpreter = python or sys.executable
    result = run_command([interpreter, "-c", _ML_PROBE], timeout=timeout)
    if not result.ok:
        return {"torch": False, "cuda": False, "peft": False, "transformers": False, "probe_error": result.summary(200)}
    line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "{}"
    try:
        report = json.loads(line)
    except json.JSONDecodeError:
        report = {"probe_error": "unparseable probe output"}
    report["interpreter"] = interpreter
    return report


def _disks(paths: list[str]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            continue
        try:
            key = str(path.resolve())
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        usage = disk_usage(path)
        if not usage:
            continue
        total, used, free = usage
        out.append({"path": key, "total_bytes": total, "used_bytes": used, "free_bytes": free})
    return out


def detect_hardware(extra_paths: list[str] | None = None, probe_ml: bool = True) -> HardwareProfile:
    """Probe the machine. Safe to call from a worker thread; never raises."""
    cpu_model, physical, logical, max_mhz = _cpu_info()
    ram_total, ram_available, swap = _meminfo()
    gpus: list[GpuInfo] = []
    notes: list[str] = []
    try:
        gpus = _nvidia_gpus()
    except Exception as exc:  # pragma: no cover - driver weirdness
        notes.append(f"nvidia-smi failed: {exc}")
    if not gpus:
        try:
            gpus = _amd_gpus()
        except Exception as exc:  # pragma: no cover
            notes.append(f"rocm-smi failed: {exc}")
    ml = ml_stack_report() if probe_ml else {}
    paths = [os.getcwd(), str(Path.home())]
    if extra_paths:
        paths.extend(extra_paths)
    profile = HardwareProfile(
        cpu_model=cpu_model,
        physical_cores=max(1, physical),
        logical_cores=max(1, logical),
        cpu_max_mhz=max_mhz,
        ram_total_mb=ram_total,
        ram_available_mb=ram_available,
        swap_total_mb=swap,
        gpus=gpus,
        disks=_disks(paths),
        ml=ml,
        os_name=f"{platform.system()} {platform.release()}",
        kernel=platform.release(),
        python=platform.python_version(),
        notes=notes,
    )
    if not gpus:
        profile.notes.append("No CUDA/ROCm GPU detected: inference and training will use the CPU.")
    if not ml.get("torch"):
        profile.notes.append("PyTorch is not installed for this interpreter: LoRA/QLoRA training is unavailable "
                             "until you install it (see docs/SETUP.md). The built-in adapter still trains.")
    log.info(
        "hardware detected",
        cm={
            "cpu": profile.cpu_model,
            "cores": profile.logical_cores,
            "ram_mb": profile.ram_total_mb,
            "gpu": profile.gpu_summary,
            "tier": profile.tier(),
        },
    )
    return profile
