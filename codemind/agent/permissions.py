"""Risk classification and the approval broker.

The brief is explicit about when the agent must stop and ask:

* destructive actions (anything that can lose work or break the machine),
* actions that expose credentials,
* irreversible external changes (pushing, opening pull requests, publishing).

Everything else that is reversible and local - reading files, editing code inside the task
workspace, running tests, inspecting the repository - proceeds without interrupting you, which
is what makes autonomous work possible. The classification also decides what *cannot* be
approved away: anything matching the blocked-command list is refused outright.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from codemind.config import Config
from codemind.events import BUS, Topics
from codemind.logging_setup import get_logger
from codemind.storage.db import Database

log = get_logger("agent.permissions")


class RiskLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    NETWORK = "network"
    DESTRUCTIVE = "destructive"
    EXTERNAL = "external"
    CREDENTIAL = "credential"

    @property
    def label(self) -> str:
        return {
            "read": "Read-only",
            "write": "Local write",
            "network": "Network access",
            "destructive": "Destructive",
            "external": "External change",
            "credential": "Credentials",
        }[self.value]


DESTRUCTIVE_PATTERNS = [
    r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|-rf|-fr)\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-z]*f",
    r"\bgit\s+push\b.*(--force|-f)\b",
    r"\bdrop\s+(table|database)\b",
    r"\btruncate\s+table\b",
    r"\bmkfs(\.|\b)",
    r"\bdd\s+if=",
    r"\bshutdown\b|\breboot\b|\bpoweroff\b",
    r"\bkill(all)?\s+-9\b",
    r"\bchmod\s+-R\s+777\b",
    r"\b>\s*/dev/(sd|nvme|hd)",
    r"\b(apt|dnf|pacman|yum)\s+(remove|purge|autoremove)\b",
    r"\bpip3?\s+uninstall\b",
    r"\bnpm\s+(uninstall|rm)\b.*-g",
    r"\bterraform\s+destroy\b",
    r"\bkubectl\s+delete\b",
]

EXTERNAL_PATTERNS = [
    r"\bgit\s+push\b",
    r"\bgh\s+pr\s+create\b",
    r"\bgh\s+release\b",
    r"\bnpm\s+publish\b",
    r"\btwine\s+upload\b",
    r"\bdocker\s+push\b",
]

NETWORK_PATTERNS = [
    r"\b(pip3?|pipx)\s+install\b",
    r"\bnpm\s+(ci|install|i)\b",
    r"\byarn\s+(install|add)\b",
    r"\bpnpm\s+(install|add)\b",
    r"\b(cargo|go)\s+(install|get|add)\b",
    r"\bapt(-get)?\s+install\b",
    r"\bcurl\b",
    r"\bwget\b",
    r"\bgit\s+(clone|fetch|pull)\b",
    r"\bollama\s+pull\b",
    r"\bhuggingface-cli\s+download\b",
]

CREDENTIAL_PATTERNS = [
    r"\.ssh/",
    r"\.aws/credentials",
    r"\.netrc",
    r"\bid_rsa\b|\bid_ed25519\b",
    r"\benv\b\s*$",
    r"\bprintenv\b",
    r"\bcat\s+.*(\.env|secrets?\.(json|yaml|yml|toml))\b",
    r"gh\s+auth\s+token",
    r"\bkeychain\b|\bpass\s+show\b",
]


@dataclass
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    risk: RiskLevel
    reason: str
    blocked: bool = False
    requirement: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "risk": self.risk.value,
            "risk_label": self.risk.label,
            "reason": self.reason,
            "blocked": self.blocked,
            "requirement": self.requirement,
        }


@dataclass
class ApprovalRequest:
    id: str
    task_id: str
    tool: str
    arguments: dict[str, Any]
    risk: RiskLevel
    detail: str
    created_at: float = field(default_factory=time.time)
    status: str = "pending"
    reason: str = ""
    resolved_at: float | None = None
    _event: threading.Event = field(default_factory=threading.Event, repr=False)
    decision: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "tool": self.tool,
            "arguments": self.arguments,
            "risk": self.risk.value,
            "risk_label": self.risk.label,
            "detail": self.detail,
            "created_at": self.created_at,
            "status": self.status,
            "reason": self.reason,
            "resolved_at": self.resolved_at,
        }

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the user answers. Timeouts mean *denied*, never approved."""
        finished = self._event.wait(timeout)
        if not finished:
            return False
        return bool(self.decision)


class PermissionPolicy:
    """Turn (tool, arguments, autonomy) into a decision."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def update_config(self, config: Config) -> None:
        self.config = config

    # -- classification ---------------------------------------------------
    def classify(self, tool: str, arguments: dict[str, Any], base_risk: RiskLevel) -> RiskLevel:
        command = str(arguments.get("command") or arguments.get("cmd") or "")
        blob = f"{command} {arguments.get('path', '')} {arguments.get('url', '')}"
        if command:
            for pattern in CREDENTIAL_PATTERNS:
                if re.search(pattern, command):
                    return RiskLevel.CREDENTIAL
            for pattern in DESTRUCTIVE_PATTERNS:
                if re.search(pattern, command):
                    return RiskLevel.DESTRUCTIVE
            for pattern in EXTERNAL_PATTERNS:
                if re.search(pattern, command):
                    return RiskLevel.EXTERNAL
            for pattern in NETWORK_PATTERNS:
                if re.search(pattern, command):
                    return max(base_risk, RiskLevel.NETWORK, key=_risk_order)
        if tool in ("fetch_docs", "download_model", "install_package"):
            return RiskLevel.NETWORK
        if tool in ("git_push", "create_pull_request", "publish"):
            return RiskLevel.EXTERNAL
        if tool == "read_file" and re.search(r"(\.env$|id_rsa|credentials)", blob, re.IGNORECASE):
            return RiskLevel.CREDENTIAL
        return base_risk

    # -- decision ---------------------------------------------------------
    def decide(self, tool: str, arguments: dict[str, Any], *, base_risk: RiskLevel = RiskLevel.READ, inside_workspace: bool = True) -> PolicyDecision:
        autonomy = self.config.agent.autonomy
        risk = self.classify(tool, arguments, base_risk)

        # Hard refusals first: these cannot be approved at runtime.
        for pattern in self.config.sandbox.blocked_commands:
            if pattern and pattern.lower() in str(arguments.get("command", "")).lower():
                return PolicyDecision(False, False, risk, f"Command matches blocked pattern {pattern!r}.", blocked=True)

        if risk is RiskLevel.CREDENTIAL:
            return PolicyDecision(
                True, True, risk,
                "This action would touch credentials or secrets.",
                requirement="Approve once, or change the command to avoid the secret.",
            )

        if risk is RiskLevel.DESTRUCTIVE:
            if self.config.security.confirm_destructive:
                return PolicyDecision(True, True, risk, "Destructive action: it can lose work or break the environment.",
                                      requirement="Explicit approval required (confirm-destructive is enabled).")
            if autonomy == "autonomous":
                return PolicyDecision(True, False, risk, "Destructive action allowed by the autonomous profile with confirmations disabled.")
            return PolicyDecision(True, True, risk, "Destructive action outside autonomous mode.")

        if risk is RiskLevel.EXTERNAL:
            if autonomy == "autonomous":
                return PolicyDecision(True, False, risk, "External change allowed by the autonomous profile.")
            return PolicyDecision(True, True, risk, "External, irreversible change (push / pull request / publish).")

        if risk is RiskLevel.NETWORK:
            if not self.config.sandbox.allow_network:
                return PolicyDecision(False, False, risk, "Network access is disabled in Settings → Sandbox.", blocked=True)
            if autonomy in ("manual", "supervised"):
                return PolicyDecision(True, True, risk, "Network access is requested (dependency install or download).")
            return PolicyDecision(True, False, risk, f"Network access permitted by the '{autonomy}' autonomy profile.")

        if risk is RiskLevel.WRITE:
            if not inside_workspace and not self.config.security.allow_outside_workspace_writes:
                return PolicyDecision(True, True, risk, "Write outside the task workspace.",
                                      requirement="Either work inside the workspace or enable writes outside it in Settings → Security.")
            if autonomy == "manual":
                return PolicyDecision(True, True, risk, "'manual' autonomy asks before every write.")
            return PolicyDecision(True, False, risk, "Reversible local edit inside the workspace.")

        return PolicyDecision(True, False, risk, "Read-only action.")

    def describe_profiles(self) -> list[dict[str, str]]:
        return [
            {"id": "manual", "label": "Manual", "summary": "Asks before every write. Best when you want to review each edit."},
            {"id": "supervised", "label": "Supervised", "summary": "Runs reads freely; asks before writes, installs, pushes and anything destructive."},
            {"id": "trusted", "label": "Trusted (default)", "summary": "Edits code and runs tests without asking; still asks for destructive or external actions."},
            {"id": "autonomous", "label": "Autonomous", "summary": "Also installs dependencies and pushes branches without asking; destructive actions still ask unless you disable that."},
        ]


class ApprovalBroker:
    """Coordinates pending approvals between the agent thread and the GUI."""

    def __init__(self, db: Database | None = None) -> None:
        self._lock = threading.RLock()
        self._pending: dict[str, ApprovalRequest] = {}
        self._history: list[ApprovalRequest] = []
        self.db = db
        self._listeners: list[Callable[[ApprovalRequest], None]] = []

    def subscribe(self, listener: Callable[[ApprovalRequest], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def request(self, task_id: str, tool: str, arguments: dict[str, Any], risk: RiskLevel, detail: str) -> ApprovalRequest:
        request = ApprovalRequest(id=uuid.uuid4().hex[:12], task_id=task_id, tool=tool, arguments=arguments, risk=risk, detail=detail)
        with self._lock:
            self._pending[request.id] = request
        if self.db is not None:
            try:
                self.db.insert(
                    "approvals",
                    {
                        "task_id": task_id,
                        "ts": request.created_at,
                        "action": tool,
                        "risk": risk.value,
                        "detail": detail[:2000],
                        "status": "pending",
                    },
                )
            except Exception as exc:  # pragma: no cover
                log.debug("could not persist approval", cm={"error": str(exc)})
        BUS.publish(Topics.APPROVAL_REQUESTED, **request.as_dict())
        for listener in list(self._listeners):
            try:
                listener(request)
            except Exception:  # pragma: no cover
                pass
        log.info("approval requested", cm={"task": task_id, "tool": tool, "risk": risk.value})
        return request

    def resolve(self, request_id: str, approved: bool, reason: str = "") -> bool:
        with self._lock:
            request = self._pending.pop(request_id, None)
        if request is None:
            return False
        request.decision = approved
        request.status = "approved" if approved else "denied"
        request.reason = reason
        request.resolved_at = time.time()
        request._event.set()
        with self._lock:
            self._history.append(request)
            if len(self._history) > 500:
                del self._history[: len(self._history) - 500]
        if self.db is not None:
            try:
                self.db.execute("DELETE FROM approvals WHERE task_id = ? AND action = ? AND status = 'pending'", (request.task_id, request.tool))
                self.db.insert(
                    "approvals",
                    {
                        "task_id": request.task_id,
                        "ts": request.resolved_at,
                        "action": request.tool,
                        "risk": request.risk.value,
                        "detail": request.detail[:2000],
                        "status": request.status,
                        "resolved_at": request.resolved_at,
                        "reason": reason[:400],
                    },
                )
            except Exception:  # pragma: no cover
                pass
        BUS.publish(Topics.APPROVAL_RESOLVED, **request.as_dict())
        log.info("approval resolved", cm={"task": request.task_id, "tool": request.tool, "approved": approved})
        return True

    def pending(self) -> list[ApprovalRequest]:
        with self._lock:
            return list(self._pending.values())

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._history)[-limit:]
        return [item.as_dict() for item in reversed(items)]

    def stored_history(self, limit: int = 100) -> list[dict[str, Any]]:
        if self.db is None:
            return self.history(limit)
        rows = self.db.query("SELECT * FROM approvals ORDER BY ts DESC LIMIT ?", (limit,))
        return [dict(row) for row in rows]

    def deny_all(self, reason: str = "task cancelled") -> None:
        for request in self.pending():
            self.resolve(request.id, False, reason)


def _risk_order(level: RiskLevel) -> int:
    return {
        RiskLevel.READ: 0,
        RiskLevel.WRITE: 1,
        RiskLevel.NETWORK: 2,
        RiskLevel.EXTERNAL: 3,
        RiskLevel.DESTRUCTIVE: 4,
        RiskLevel.CREDENTIAL: 5,
    }[level]


def summarize_arguments(arguments: dict[str, Any], limit: int = 200) -> str:
    parts = []
    for key, value in list(arguments.items())[:6]:
        text = str(value)
        if len(text) > 80:
            text = text[:77] + "…"
        parts.append(f"{key}={text}")
    return ", ".join(parts)[:limit]
