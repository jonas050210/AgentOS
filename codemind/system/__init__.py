"""System observation: hardware probing and live resource sampling.

No statistics are invented here. CPU and memory come from ``/proc``; GPU utilisation and
VRAM come from ``nvidia-smi`` when an NVIDIA driver is present (otherwise the fields are
``None`` and the UI says "no GPU detected"); library availability (torch/CUDA/peft) is
probed by importing in a subprocess so a broken ML stack can never take the GUI down.
"""

from __future__ import annotations

from codemind.system.hardware import HardwareProfile, detect_hardware, ml_stack_report
from codemind.system.resources import ResourceSampler, ResourceSnapshot, current_snapshot, process_usage
from codemind.system.governor import ResourceGovernor, governor_plan

__all__ = [
    "HardwareProfile",
    "detect_hardware",
    "ml_stack_report",
    "ResourceSampler",
    "ResourceSnapshot",
    "current_snapshot",
    "process_usage",
    "ResourceGovernor",
    "governor_plan",
]
