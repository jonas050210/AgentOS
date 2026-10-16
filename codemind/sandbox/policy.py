"""What this machine can actually enforce, measured - not assumed.

The Sandbox view shows exactly which mechanisms are available (namespaces, cgroups,
rlimits, privilege dropping) and which are not, because "sandboxed" must mean something
concrete. Nothing here is reported as active unless the probe proved it works.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codemind.logging_setup import get_logger
from codemind.utils.process import build_env, run_command

log = get_logger("sandbox.policy")


@dataclass
class IsolationReport:
    unshare_available: bool = False
    unshare_network: bool = False
    unshare_mount: bool = False
    user_namespaces: bool = False
    bwrap_available: bool = False
    firejail_available: bool = False
    rlimits_available: bool = False
    cgroup_v2: bool = False
    nice_available: bool = False
    seccomp_available: bool = False
    mount_user_ns: bool = False
    kernel: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def level(self) -> str:
        if self.bwrap_available or self.firejail_available:
            return "strong"
        if self.unshare_available and (self.unshare_network or self.unshare_mount):
            return "namespaces"
        if self.rlimits_available:
            return "limited"
        return "minimal"

    def summary(self) -> str:
        parts = []
        if self.bwrap_available:
            parts.append("bubblewrap")
        if self.firejail_available:
            parts.append("firejail")
        if self.unshare_network:
            parts.append("network namespace")
        if self.unshare_mount:
            parts.append("mount namespace")
        if self.rlimits_available:
            parts.append("rlimits")
        if self.cgroup_v2:
            parts.append("cgroup v2 accounting")
        return ", ".join(parts) if parts else "process isolation only (rlimits unavailable)"

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "summary": self.summary(),
            "unshare_available": self.unshare_available,
            "unshare_network": self.unshare_network,
            "unshare_mount": self.unshare_mount,
            "user_namespaces": self.user_namespaces,
            "bwrap_available": self.bwrap_available,
            "firejail_available": self.firejail_available,
            "rlimits_available": self.rlimits_available,
            "cgroup_v2": self.cgroup_v2,
            "seccomp_available": self.seccomp_available,
            "kernel": self.kernel,
            "details": self.details,
        }


def _probe_unshare(flag: str, command: list[str]) -> bool:
    if not shutil.which("unshare"):
        return False
    result = run_command(["unshare", flag, *command], timeout=8)
    return result.ok


def probe_isolation(force: bool = False) -> IsolationReport:
    """Probe the isolation mechanisms available to this process."""
    report = IsolationReport()
    report.kernel = os.uname().release
    report.unshare_available = shutil.which("unshare") is not None
    report.bwrap_available = shutil.which("bwrap") is not None
    report.firejail_available = shutil.which("firejail") is not None
    if report.unshare_available:
        report.unshare_network = _probe_unshare("--net", ["true"])
        report.unshare_mount = _probe_unshare("--mount", ["true"])
        report.user_namespaces = _probe_unshare("--user", ["true"])
        report.mount_user_ns = _probe_unshare("--user", ["--mount", "true"]) if report.user_namespaces else False
    try:
        import resource  # noqa: F401

        report.rlimits_available = True
    except Exception:  # pragma: no cover - non-POSIX
        report.rlimits_available = False
    report.cgroup_v2 = Path("/sys/fs/cgroup/cgroup.controllers").exists()
    report.nice_available = hasattr(os, "nice")
    report.seccomp_available = Path("/proc/self/status").exists() and "Seccomp" in Path("/proc/self/status").read_text(errors="replace")
    report.details = {
        "uid": os.getuid() if hasattr(os, "getuid") else None,
        "euid": os.geteuid() if hasattr(os, "geteuid") else None,
        "cgroup_controllers": _cgroup_controllers(),
        "python": os.environ.get("VIRTUAL_ENV", ""),
    }
    log.info("isolation probe", cm={"level": report.level, "summary": report.summary()})
    return report


def _cgroup_controllers() -> str:
    try:
        return Path("/sys/fs/cgroup/cgroup.controllers").read_text().strip()
    except OSError:
        return ""


def build_sandbox_argv(
    argv: list[str],
    *,
    report: IsolationReport,
    workspace: Path,
    allow_network: bool,
    network_isolation: str = "auto",
    extra_read_paths: tuple[str, ...] = (),
) -> tuple[list[str], dict[str, Any]]:
    """Wrap a command with the strongest isolation this machine supports.

    Returns the argv to execute plus a description of what was applied, which is stored in
    the execution history and shown in the UI.
    """
    applied: dict[str, Any] = {"network": "open" if allow_network else "open (no isolation available)"}
    if report.bwrap_available:
        prefix = ["bwrap", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--die-with-parent", "--ro-bind", "/usr", "/usr"]
        for path in ("/lib", "/lib64", "/bin", "/sbin", "/etc"):
            if Path(path).exists():
                prefix += ["--ro-bind", path, path]
        prefix += ["--bind", str(workspace), str(workspace), "--chdir", str(workspace)]
        if not allow_network:
            prefix.append("--unshare-net")
            applied["network"] = "blocked (bubblewrap)"
        applied["mechanism"] = "bubblewrap"
        return prefix + ["--"] + argv, applied
    if report.firejail_available:
        prefix = ["firejail", "--quiet", f"--whitelist={workspace}"]
        if not allow_network:
            prefix.append("--net=none")
            applied["network"] = "blocked (firejail)"
        applied["mechanism"] = "firejail"
        return prefix + argv, applied
    if network_isolation == "unshare" and report.unshare_network:
        applied["mechanism"] = "unshare --net"
        applied["network"] = "blocked (network namespace)"
        return ["unshare", "--net", *argv], applied
    if network_isolation == "auto" and not allow_network and report.unshare_network:
        applied["mechanism"] = "unshare --net"
        applied["network"] = "blocked (network namespace)"
        return ["unshare", "--net", *argv], applied
    applied["mechanism"] = "none"
    applied["note"] = "Process isolation only: rlimits, timeouts and process-group teardown."
    return argv, applied


def network_policy_description(allow_network: bool, report: IsolationReport) -> str:
    if allow_network:
        return "Network allowed inside the sandbox."
    if report.bwrap_available or report.firejail_available or report.unshare_network:
        return "Network blocked with " + report.summary() + "."
    return "Network requested as blocked, but this system provides no namespace support; commands may still reach the network."


def available_commands(names: tuple[str, ...] = ("python3", "pip3", "node", "npm", "git", "go", "cargo", "make", "gcc", "cmake", "pytest", "rustc", "java", "mvn", "gh", "ollama")) -> dict[str, str | None]:
    return {name: shutil.which(name) for name in names}
