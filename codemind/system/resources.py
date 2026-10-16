"""Live resource sampling.

CPU and memory are read from ``/proc`` (Linux) with a psutil fast-path when it happens to be
installed; GPU figures come from ``nvidia-smi`` (with the same subprocess throttled, because
the call is not free). Samples feed the Overview meters, the governor and the resource
history chart, and are persisted at a low rate so the database stays small.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:  # optional accelerator, never required
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.system.hardware import HardwareProfile, _nvidia_gpus

log = get_logger("system.resources")


@dataclass
class ResourceSnapshot:
    ts: float = field(default_factory=time.time)
    cpu_percent: float = 0.0
    cpu_per_core: list[float] = field(default_factory=list)
    load1: float = 0.0
    load5: float = 0.0
    load15: float = 0.0
    ram_used_mb: int = 0
    ram_total_mb: int = 0
    ram_available_mb: int = 0
    swap_used_mb: int = 0
    gpu_util: float | None = None
    vram_used_mb: int | None = None
    vram_total_mb: int | None = None
    gpu_temp_c: float | None = None
    disk_free_gb: float | None = None
    processes: int = 0
    source: str = "procfs"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "cpu_percent": round(self.cpu_percent, 1),
            "cpu_per_core": [round(v, 1) for v in self.cpu_per_core],
            "load1": self.load1,
            "load5": self.load5,
            "load15": self.load15,
            "ram_used_mb": self.ram_used_mb,
            "ram_total_mb": self.ram_total_mb,
            "ram_available_mb": self.ram_available_mb,
            "swap_used_mb": self.swap_used_mb,
            "gpu_util": self.gpu_util,
            "vram_used_mb": self.vram_used_mb,
            "vram_total_mb": self.vram_total_mb,
            "gpu_temp_c": self.gpu_temp_c,
            "disk_free_gb": self.disk_free_gb,
            "processes": self.processes,
            "source": self.source,
        }

    @property
    def ram_percent(self) -> float:
        if not self.ram_total_mb:
            return 0.0
        return 100.0 * self.ram_used_mb / self.ram_total_mb

    @property
    def vram_percent(self) -> float | None:
        if not self.vram_total_mb:
            return None
        return 100.0 * (self.vram_used_mb or 0) / self.vram_total_mb


def _read_proc_stat() -> tuple[float, list[float]] | None:
    try:
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return None
    total_idle = 0.0
    total_all = 0.0
    per_core: list[float] = []
    for line in lines:
        if not line.startswith("cpu"):
            continue
        parts = line.split()
        label = parts[0]
        try:
            values = [float(v) for v in parts[1:]]
        except ValueError:
            continue
        if len(values) < 4:
            continue
        idle = values[3] + (values[4] if len(values) > 4 else 0.0)
        total = sum(values)
        busy = max(0.0, total - idle)
        percent = 100.0 * busy / total if total else 0.0
        if label == "cpu":
            total_idle, total_all = idle, total
        else:
            per_core.append(percent)
    if not total_all:
        return None
    return total_idle, per_core  # type: ignore[return-value]


def _read_meminfo() -> tuple[int, int, int, int]:
    used = total = swap_used = swap_total = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            value = int((rest.strip().split(" ")[0] or 0))
            if key == "MemTotal":
                total = value // 1024
            elif key == "MemAvailable":
                used = max(0, total - value // 1024) if total else 0
            elif key == "SwapTotal":
                swap_total = value // 1024
            elif key == "SwapFree":
                swap_used = max(0, swap_total - value // 1024) if swap_total else 0
    except (OSError, ValueError):
        return 0, 0, 0, 0
    return used, total, swap_used, swap_total


def _count_processes() -> int:
    try:
        return sum(1 for entry in os.listdir("/proc") if entry.isdigit())
    except OSError:
        return 0


class ResourceSampler:
    """Background sampler that publishes :data:`Topics.RESOURCE_SAMPLE` events."""

    def __init__(
        self,
        interval: float = 2.0,
        history: int = 900,
        disk_path: str | Path = ".",
        gpu: bool = True,
        persist: Callable[[ResourceSnapshot], None] | None = None,
        persist_every: float = 15.0,
    ) -> None:
        self.interval = max(0.25, float(interval))
        self.disk_path = Path(disk_path)
        self.gpu_enabled = gpu
        self.history: deque[ResourceSnapshot] = deque(maxlen=history)
        self.persist = persist
        self.persist_every = persist_every
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._prev: tuple[float, float] | None = None  # (idle, total)
        self._last_persist = 0.0
        self._gpu_error_logged = False
        self.latest: ResourceSnapshot = ResourceSnapshot()
        self.sample()

    # -- lifecycle --------------------------------------------------------
    def start(self) -> "ResourceSampler":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="codemind-resources", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.sample()
            except Exception as exc:  # pragma: no cover - sampler must not die
                log.warning("resource sample failed", cm={"error": str(exc)})

    # -- sampling ---------------------------------------------------------
    def sample(self) -> ResourceSnapshot:
        snapshot = current_snapshot(
            previous=self._prev,
            disk_path=self.disk_path,
            gpu=self.gpu_enabled,
            gpu_error_logged=self._gpu_error_logged,
        )
        if snapshot.source == "procfs" and not self._gpu_error_logged and snapshot.vram_total_mb is None and self.gpu_enabled:
            self._gpu_error_logged = True
        with self._lock:
            self.history.append(snapshot)
            self.latest = snapshot
        BUS.publish(Topics.RESOURCE_SAMPLE, **snapshot.as_dict())
        if self.persist and snapshot.ts - self._last_persist >= self.persist_every:
            self._last_persist = snapshot.ts
            try:
                self.persist(snapshot)
            except Exception as exc:  # pragma: no cover
                log.debug("resource persistence failed", cm={"error": str(exc)})
        return snapshot

    def series(self, field_name: str = "cpu_percent", limit: int = 120) -> list[float]:
        with self._lock:
            items = list(self.history)[-limit:]
        out: list[float] = []
        for item in items:
            value = getattr(item, field_name, None)
            if value is None:
                continue
            out.append(float(value))
        return out

    def stats(self) -> dict[str, Any]:
        with self._lock:
            items = list(self.history)
        return {
            "samples": len(items),
            "interval": self.interval,
            "oldest": items[0].ts if items else None,
            "newest": items[-1].ts if items else None,
        }


def current_snapshot(
    *,
    previous: tuple[float, float] | None = None,
    disk_path: str | Path = ".",
    gpu: bool = True,
    gpu_error_logged: bool = False,
) -> ResourceSnapshot:
    """Take one reading (CPU deltas need a previous raw reading; pass None for a warm-up read)."""
    source = "psutil" if psutil is not None else "procfs"
    snapshot = ResourceSnapshot(source=source)
    if psutil is not None:
        try:
            snapshot.cpu_percent = float(psutil.cpu_percent(interval=None))
            snapshot.cpu_per_core = [float(v) for v in psutil.cpu_percent(interval=None, percpu=True)]
            vm = psutil.virtual_memory()
            snapshot.ram_used_mb = int((vm.total - vm.available) // (1024 * 1024))
            snapshot.ram_total_mb = int(vm.total // (1024 * 1024))
            snapshot.ram_available_mb = int(vm.available // (1024 * 1024))
            swap = psutil.swap_memory()
            snapshot.swap_used_mb = int(swap.used // (1024 * 1024))
            snapshot.load1, snapshot.load5, snapshot.load15 = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
            snapshot.processes = len(psutil.pids())
        except Exception:  # pragma: no cover
            source = "procfs"
            snapshot.source = source
    if source == "procfs":
        raw = _read_proc_stat()
        if raw:
            snapshot.cpu_per_core = [round(v, 1) for v in raw[1]]
            snapshot.cpu_percent = round(sum(raw[1]) / len(raw[1]), 1) if raw[1] else 0.0
        try:
            snapshot.load1, snapshot.load5, snapshot.load15 = os.getloadavg()
        except (OSError, AttributeError):
            pass
        used, total, swap_used, _swap_total = _read_meminfo()
        snapshot.ram_used_mb = used
        snapshot.ram_total_mb = total
        snapshot.ram_available_mb = max(0, total - used)
        snapshot.swap_used_mb = swap_used
        snapshot.processes = _count_processes()

    if gpu and not gpu_error_logged:
        try:
            gpus = _nvidia_gpus()
            if gpus:
                primary = gpus[0]
                snapshot.gpu_util = primary.utilization
                snapshot.vram_used_mb = primary.vram_used_mb
                snapshot.vram_total_mb = primary.vram_total_mb
                snapshot.gpu_temp_c = primary.temperature_c
        except Exception:  # pragma: no cover
            pass

    try:
        from shutil import disk_usage

        usage = disk_usage(str(disk_path))
        snapshot.disk_free_gb = round(usage.free / (1024 ** 3), 1)
    except OSError:
        snapshot.disk_free_gb = None
    return snapshot


def process_usage(pid: int | None) -> dict[str, Any]:
    """RSS, CPU and thread count for one process (training runs, sandbox children)."""
    if not pid:
        return {}
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            with proc.oneshot():  # type: ignore[attr-defined]
                mem = proc.memory_info()
                return {
                    "pid": pid,
                    "rss_mb": round(mem.rss / (1024 * 1024), 1),
                    "cpu_percent": round(proc.cpu_percent(interval=None), 1),
                    "threads": proc.num_threads(),
                    "status": proc.status(),
                    "cmdline": " ".join(proc.cmdline()[:4]),
                }
        except Exception:
            return {}
    # /proc fallback
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
        rss = 0
        threads = 0
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) // 1024
            elif line.startswith("Threads:"):
                threads = int(line.split()[1])
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace").split()
        utime, stime, starttime = float(stat[13]), float(stat[14]), float(stat[21])
        ticks = os.sysconf("SC_CLK_TCK") or 100
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        elapsed = max(0.001, uptime - starttime / ticks)
        cpu_percent = round(100.0 * (utime + stime) / ticks / elapsed, 1)
        return {"pid": pid, "rss_mb": rss, "cpu_percent": cpu_percent, "threads": threads, "status": "running", "cmdline": ""}
    except (OSError, ValueError, IndexError):
        return {}


def prlimit_wrapper(limits: dict[str, int]) -> Callable[[], None] | None:  # pragma: no cover - used via Popen
    """Build a ``preexec_fn`` that applies resource limits to a child process."""
    if not limits:
        return None

    def _apply() -> None:
        import resource

        if limits.get("cpu_seconds"):
            resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_seconds"], limits["cpu_seconds"] + 30))
        if limits.get("address_space_mb"):
            size = limits["address_space_mb"] * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (size, size))
        if limits.get("file_size_mb"):
            size = limits["file_size_mb"] * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))
        if limits.get("processes"):
            resource.setrlimit(resource.RLIMIT_NPROC, (limits["processes"], limits["processes"]))
        try:
            os.nice(int(limits.get("nice", 5)))
        except OSError:
            pass

    return _apply
