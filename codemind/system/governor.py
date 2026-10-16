"""The resource governor.

CodeMind runs two kinds of work at once: interactive coding tasks (latency sensitive) and
training (throughput hungry). The governor turns the hardware profile plus live samples into
a concrete allocation plan - how many CPU threads training may use, how much RAM it must
leave free, whether it should pause entirely because a task is running, and how many tasks
may run in parallel.

It is deliberately a pure function of inputs (profile + config + sample + activity), so the
GUI can display *why* it made each decision, and tests can assert the arithmetic.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from codemind.config import Config
from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.system.hardware import HardwareProfile
from codemind.system.resources import ResourceSnapshot

log = get_logger("system.governor")


@dataclass
class GovernorPlan:
    mode: str
    training_threads: int
    training_max_parallel: int
    ram_budget_mb: int
    vram_budget_mb: int
    nice: int
    pause_training: bool
    pause_reason: str
    agent_parallel: int
    agent_thread_budget: int
    sampling_interval: float
    recommendations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "training_threads": self.training_threads,
            "training_max_parallel": self.training_max_parallel,
            "ram_budget_mb": self.ram_budget_mb,
            "vram_budget_mb": self.vram_budget_mb,
            "nice": self.nice,
            "pause_training": self.pause_training,
            "pause_reason": self.pause_reason,
            "agent_parallel": self.agent_parallel,
            "agent_thread_budget": self.agent_thread_budget,
            "sampling_interval": self.sampling_interval,
            "recommendations": self.recommendations,
        }


def governor_plan(
    profile: HardwareProfile,
    config: Config,
    snapshot: ResourceSnapshot | None = None,
    *,
    active_tasks: int = 0,
    queued_tasks: int = 0,
    training_runs: int = 0,
) -> GovernorPlan:
    """Compute the allocation plan. Deterministic and side-effect free."""
    resources = config.resources
    cores = max(1, profile.logical_cores)
    reserve_ratio = max(0.0, min(0.8, resources.cpu_reserve_percent / 100.0))
    available_cores = cores * (1.0 - reserve_ratio)
    mode = resources.governor
    if mode == "auto":
        if profile.tier() in ("minimal", "limited"):
            mode = "eco"
        elif profile.tier() in ("performance", "workstation"):
            mode = "performance"
        else:
            mode = "balanced"

    mode_share = {"eco": 0.35, "balanced": 0.6, "performance": 0.8, "manual": 0.6}.get(mode, 0.6)
    training_share = min(mode_share, max(0.05, resources.training_cpu_percent / 100.0))
    training_threads = max(1, int(math.floor(available_cores * training_share)))
    training_threads = min(training_threads, max(1, cores - 1))

    ram_reserve = resources.ram_reserve_mb
    ram_available = (snapshot.ram_available_mb if snapshot and snapshot.ram_available_mb else profile.ram_available_mb) or profile.ram_total_mb
    ram_budget = max(256, ram_available - ram_reserve)
    vram_total = profile.vram_total_mb
    vram_reserve = resources.vram_reserve_mb
    used_vram = (snapshot.vram_used_mb if snapshot and snapshot.vram_used_mb is not None else 0) or 0
    vram_budget = max(0, int(vram_total * config.training.max_vram_percent / 100.0) - vram_reserve)

    nice = {"low": 10, "normal": 0, "high": -5}.get(resources.io_priority if mode != "manual" else config.training.background_priority, 10)
    if mode == "eco":
        nice = max(nice, 15)
    elif mode == "performance":
        nice = min(nice, 5)

    recommendations: list[str] = []
    pause_training = False
    pause_reason = ""
    if active_tasks or queued_tasks:
        if mode in ("eco", "balanced") or config.autonomy.respect_tasks:
            if active_tasks:
                pause_training = True
                pause_reason = f"{active_tasks} coding task(s) active — training paused to keep the agent responsive"
            else:
                training_threads = max(1, training_threads // 2)
                pause_reason = f"{queued_tasks} task(s) queued — training throttled to {training_threads} thread(s)"

    if snapshot is not None:
        if snapshot.ram_percent > 88:
            pause_training = True
            pause_reason = f"memory pressure at {snapshot.ram_percent:.0f}% — training paused"
            recommendations.append("Close memory-heavy applications or raise the RAM reserve.")
        if snapshot.vram_percent is not None and snapshot.vram_percent > 92 and training_runs:
            pause_training = True
            pause_reason = f"VRAM at {snapshot.vram_percent:.0f}% — training paused to avoid a CUDA OOM"
        if snapshot.cpu_percent > 92 and not active_tasks:
            training_threads = max(1, training_threads - 1)
            recommendations.append("The CPU is saturated; training is using one thread fewer than its budget.")

    agent_parallel = max(1, min(config.agent.max_parallel_tasks, max(1, cores // 4)))
    agent_threads = max(1, int(available_cores) - training_threads) if not pause_training else max(2, int(available_cores))

    if profile.ram_total_mb and profile.ram_total_mb < 6000:
        recommendations.append(
            "This machine has little RAM: keep datasets small, prefer the built-in adapter over LoRA, "
            "and expect the sandbox to run one command at a time."
        )
    if not profile.has_cuda:
        recommendations.append(
            "No CUDA GPU: inference runs on the CPU. A 1.5B model is the practical ceiling; larger models "
            "will be much slower than real time."
        )
    elif vram_total and vram_total <= 9000:
        recommendations.append(
            f"{vram_total} MB of VRAM: 4-bit QLoRA on a 1.5B base fits; leave the reserve so the desktop "
            "compositor never has to swap GPU memory."
        )
    if vram_budget and used_vram > vram_budget:
        recommendations.append("Current VRAM use already exceeds the training budget; close GPU applications first.")

    plan = GovernorPlan(
        mode=mode,
        training_threads=training_threads,
        training_max_parallel=1 if profile.tier() in ("minimal", "limited", "balanced") else 2,
        ram_budget_mb=ram_budget,
        vram_budget_mb=vram_budget,
        nice=nice,
        pause_training=pause_training,
        pause_reason=pause_reason,
        agent_parallel=agent_parallel,
        agent_thread_budget=agent_threads,
        sampling_interval=max(0.5, resources.sample_interval),
        recommendations=recommendations,
    )
    return plan


class ResourceGovernor:
    """Holds the latest plan and applies it to running subsystems."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._plan: GovernorPlan | None = None
        self._plan_ts: float = 0.0
        self._listeners: list[Any] = []

    def update(
        self,
        profile: HardwareProfile,
        config: Config,
        snapshot: ResourceSnapshot | None,
        *,
        active_tasks: int = 0,
        queued_tasks: int = 0,
        training_runs: int = 0,
        publish: bool = True,
    ) -> GovernorPlan:
        plan = governor_plan(
            profile,
            config,
            snapshot,
            active_tasks=active_tasks,
            queued_tasks=queued_tasks,
            training_runs=training_runs,
        )
        previous = self._plan
        with self._lock:
            self._plan = plan
            self._plan_ts = time.time()
        if publish and (previous is None or previous.as_dict() != plan.as_dict()):
            BUS.publish(Topics.GOVERNOR, **plan.as_dict())
            if previous is not None and previous.pause_training != plan.pause_training:
                log.info("training %s", "paused" if plan.pause_training else "resumed", cm={"reason": plan.pause_reason})
        for listener in list(self._listeners):
            try:
                listener(plan)
            except Exception:  # pragma: no cover
                pass
        return plan

    @property
    def plan(self) -> GovernorPlan | None:
        with self._lock:
            return self._plan

    @property
    def age(self) -> float:
        return time.time() - self._plan_ts if self._plan_ts else float("inf")

    def subscribe(self, listener: Any) -> Any:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            plan = self._plan
        return {
            "plan": plan.as_dict() if plan else None,
            "age_seconds": round(self.age, 2) if plan else None,
        }
