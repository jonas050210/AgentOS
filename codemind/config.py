"""Configuration schema, validation and persistence.

CodeMind's settings are declared once, as dataclasses with field metadata. That single
declaration drives:

* validation (types, ranges, choices) on every read *and* every write;
* the JSON file on disk (atomic write, backwards-compatible merge, unknown keys dropped);
* the Settings UI, which renders one real control per declared field and shows the help text.

No setting exists that nothing reads, and no control exists that writes nothing.
"""

from __future__ import annotations

import copy
import os
import threading
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from codemind.utils.files import atomic_write_json, read_json

SETTINGS_FILENAME = "settings.json"


class ConfigError(ValueError):
    """Raised when a settings payload cannot be validated."""


def meta(
    title: str,
    *,
    kind: str = "str",
    help: str = "",  # noqa: A002 - matches the field name used in the UI
    choices: Sequence[str] | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    step: float | None = None,
    unit: str = "",
    advanced: bool = False,
) -> dict[str, Any]:
    return {
        "title": title,
        "kind": kind,
        "help": help,
        "choices": tuple(choices) if choices else None,
        "minimum": minimum,
        "maximum": maximum,
        "step": step,
        "unit": unit,
        "advanced": advanced,
    }


# --------------------------------------------------------------------------- sections


@dataclass
class AppSettings:
    theme_mode: str = field(default="liquid_glass", metadata=meta("Appearance", kind="choice", choices=("liquid_glass", "graphite", "midnight"), help="Surface treatment of the desktop shell."))
    glass_intensity: float = field(default=0.72, metadata=meta("Glass intensity", kind="float", minimum=0.0, maximum=1.0, step=0.05, help="Translucency of panels over the ambient background."))
    motion: str = field(default="full", metadata=meta("Motion", kind="choice", choices=("full", "reduced", "off"), help="Animation level; 'reduced' keeps fades but drops movement."))
    accent: str = field(default="#5AC8FA", metadata=meta("Accent colour", kind="color", help="Primary accent used for active states and charts."))
    start_view: str = field(default="overview", metadata=meta("Start view", kind="choice", choices=("overview", "agent", "training", "sandbox", "memory", "github", "settings"), help="View shown when CodeMind starts."))
    restore_geometry: bool = field(default=True, metadata=meta("Restore window", kind="bool", help="Remember window size and position between runs."))
    autostart: bool = field(default=False, metadata=meta("Start with session", kind="bool", help="Install an XDG autostart entry so CodeMind launches with your desktop session."))
    minimize_to_tray: bool = field(default=False, metadata=meta("Minimise to tray", kind="bool", help="Keep the agent running in the tray instead of quitting on close."))
    show_splash: bool = field(default=True, metadata=meta("Show splash", kind="bool", help="Brief splash while the database and hardware probe finish."))
    window: dict[str, int] = field(default_factory=lambda: {"w": 1440, "h": 920, "x": -1, "y": -1}, metadata=meta("Window geometry", kind="json", advanced=True, help="Internal: last window rect."))


@dataclass
class ModelSettings:
    backend: str = field(default="auto", metadata=meta("Inference backend", kind="choice", choices=("auto", "ollama", "llama_cpp", "heuristic"), help="Which local runtime answers planning prompts. 'auto' picks the best reachable one; 'heuristic' is the built-in planner that needs no model."))
    ollama_host: str = field(default="http://127.0.0.1:11434", metadata=meta("Ollama address", kind="str", help="Where the Ollama server listens."))
    llama_server_host: str = field(default="http://127.0.0.1:8080", metadata=meta("llama.cpp address", kind="str", help="Address of a running llama-server instance."))
    active_model: str = field(default="", metadata=meta("Active model", kind="str", help="Tag of the model used for planning and code reasoning."))
    model_dirs: list[str] = field(default_factory=lambda: ["~/models"], metadata=meta("Model directories", kind="list", help="Folders scanned for GGUF/safetensors checkpoints."))
    context_tokens: int = field(default=8192, metadata=meta("Context window", kind="int", minimum=1024, maximum=131072, step=1024, unit="tokens", help="Prompt budget per planning call."))
    max_output_tokens: int = field(default=2048, metadata=meta("Max generated tokens", kind="int", minimum=128, maximum=32768, step=128, unit="tokens"))
    temperature: float = field(default=0.2, metadata=meta("Temperature", kind="float", minimum=0.0, maximum=1.5, step=0.05, help="Low values keep code edits deterministic."))
    keep_alive: str = field(default="10m", metadata=meta("Keep model loaded", kind="str", help="Ollama keep_alive duration between calls."))
    auto_pull: bool = field(default=True, metadata=meta("Allow downloads", kind="bool", help="Permit fetching models from the public registry when you press Download."))
    gpu_layers: int = field(default=-1, metadata=meta("GPU layers", kind="int", minimum=-1, maximum=200, help="-1 lets the runtime offload as much as fits in VRAM."))
    prefer_quantization: str = field(default="q4_k_m", metadata=meta("Preferred quantization", kind="choice", choices=("q4_k_m", "q5_k_m", "q8_0", "f16"), help="Used when suggesting a variant for your VRAM."))


@dataclass
class AgentSettings:
    autonomy: str = field(default="trusted", metadata=meta("Autonomy", kind="choice", choices=("manual", "supervised", "trusted", "autonomous"), help="How far the agent may go without asking. 'manual' asks before every write, 'trusted' auto-approves reversible local edits and tests, 'autonomous' also allows dependency installs and branches."))
    max_steps: int = field(default=40, metadata=meta("Max steps per task", kind="int", minimum=1, maximum=400, help="Hard ceiling on plan steps, prevents runaway loops."))
    max_parallel_tasks: int = field(default=1, metadata=meta("Parallel tasks", kind="int", minimum=1, maximum=4, help="How many agent tasks run at once. Tasks take priority over training."))
    step_timeout: float = field(default=600.0, metadata=meta("Step timeout", kind="float", minimum=10.0, maximum=7200.0, step=10.0, unit="s", help="Wall-clock limit for a single reasoning/execution step."))
    command_timeout: float = field(default=300.0, metadata=meta("Command timeout", kind="float", minimum=5.0, maximum=3600.0, step=5.0, unit="s"))
    verify_tests: bool = field(default=True, metadata=meta("Verify with tests", kind="bool", help="Run the detected test command after edits and parse the result."))
    auto_detect_tests: bool = field(default=True, metadata=meta("Detect test suites", kind="bool", help="Inspect the project for pytest/npm/cargo/go test commands."))
    max_attempts_per_step: int = field(default=3, metadata=meta("Attempts per step", kind="int", minimum=1, maximum=10, help="Retries before a step is reported as blocked. Identical retries are refused."))
    commit_changes: bool = field(default=False, metadata=meta("Auto-commit", kind="bool", help="Create a local git commit after a verified task."))
    require_clean_tree: bool = field(default=False, metadata=meta("Require clean git tree", kind="bool", help="Refuse to start when the project has uncommitted changes."))
    analysis_depth: int = field(default=1200, metadata=meta("Analysis file budget", kind="int", minimum=50, maximum=20000, step=50, unit="files", help="How many files the repository scan may read when building context."))
    explain_plan: bool = field(default=True, metadata=meta("Explain plans", kind="bool", help="Record a human-readable rationale for each plan step."))


@dataclass
class SandboxSettings:
    workspace_root: str = field(default="", metadata=meta("Workspace root", kind="path", help="Where isolated task workspaces are created. Empty = storage root/workspaces."))
    workspace_mode: str = field(default="clone", metadata=meta("Task workspace", kind="choice", choices=("clone", "copy", "in_place"), help="'clone' makes a local git clone per task (cheap, isolated), 'copy' copies the tree without history, 'in_place' edits the project directory directly."))
    allow_network: bool = field(default=True, metadata=meta("Allow network", kind="bool", help="Permit commands to reach the network inside the sandbox."))
    network_isolation: str = field(default="auto", metadata=meta("Network isolation", kind="choice", choices=("auto", "unshare", "none"), help="'unshare' runs commands in a network namespace when the kernel allows it."))
    allow_shell: bool = field(default=True, metadata=meta("Allow shell", kind="bool", help="Permit arbitrary shell commands (still subject to the blocked-command list)."))
    allow_destructive: bool = field(default=False, metadata=meta("Allow destructive commands", kind="bool", help="Permit rm -rf, git reset --hard, force pushes, and similar. Always asks for approval when enabled."))
    blocked_commands: list[str] = field(default_factory=lambda: ["sudo", "shutdown", "reboot", "mkfs", "dd if=", ":(){:|:&};:"], metadata=meta("Blocked commands", kind="list", help="Command patterns that are refused outright."))
    cpu_limit_percent: int = field(default=0, metadata=meta("CPU cap", kind="int", minimum=0, maximum=100, unit="%", help="0 = no artificial cap; otherwise a soft ceiling enforced by the resource governor."))
    memory_limit_mb: int = field(default=0, metadata=meta("Memory cap", kind="int", minimum=0, maximum=32768, step=128, unit="MB", help="Address-space limit applied with setrlimit (0 = unlimited)."))
    command_output_limit: int = field(default=400_000, metadata=meta("Captured output", kind="int", minimum=2000, maximum=4_000_000, step=2000, unit="chars", advanced=True))
    keep_workspaces: int = field(default=12, metadata=meta("Keep workspaces", kind="int", minimum=1, maximum=200, help="Oldest sandbox workspaces are pruned beyond this count."))
    allowed_paths: list[str] = field(default_factory=list, metadata=meta("Extra readable paths", kind="list", help="Read-only access outside the workspace for reference code."))
    use_workspace_venv: bool = field(default=True, metadata=meta("Isolated virtualenv", kind="bool", help="Install packages into a per-workspace virtualenv instead of touching the system interpreter (required on modern Ubuntu/Debian, which block system-wide pip)."))
    venv_dir: str = field(default=".codemind-venv", metadata=meta("Virtualenv folder", kind="text", advanced=True, help="Name of the per-workspace virtualenv folder."))
    auto_create_venv: bool = field(default=True, metadata=meta("Create virtualenv on demand", kind="bool", help="Create the workspace virtualenv automatically the first time a package is installed or a test needs it."))


@dataclass
class TrainingSettings:
    enabled: bool = field(default=True, metadata=meta("Training enabled", kind="bool", help="Allow headless training runs to be started and scheduled."))
    prefer_method: str = field(default="auto", metadata=meta("Method selection", kind="choice", choices=("auto", "qlora", "lora", "adapter_lite", "retrieval_index"), help="'auto' picks QLoRA/LoRA when the Python ML stack and a GPU are present, otherwise the dependency-free adapter."))
    base_model: str = field(default="", metadata=meta("Base model", kind="str", help="Checkpoint used by LoRA/QLoRA runs. Empty = the active chat model if it is a local path."))
    dataset_dir: str = field(default="", metadata=meta("Dataset directory", kind="path", help="Where curated JSONL datasets live. Empty = storage root/datasets."))
    eval_suite: str = field(default="coding_core", metadata=meta("Evaluation suite", kind="choice", choices=("coding_core", "tool_use", "regression", "instruction_following"), help="Held-out tasks used to compare a candidate against the baseline."))
    lora_rank: int = field(default=16, metadata=meta("LoRA rank", kind="int", minimum=4, maximum=128, step=4))
    lora_alpha: int = field(default=32, metadata=meta("LoRA alpha", kind="int", minimum=1, maximum=256, step=1))
    lora_dropout: float = field(default=0.05, metadata=meta("LoRA dropout", kind="float", minimum=0.0, maximum=0.5, step=0.01))
    learning_rate: float = field(default=0.0002, metadata=meta("Learning rate", kind="float", minimum=1e-7, maximum=0.01, step=1e-5, advanced=True))
    batch_size: int = field(default=1, metadata=meta("Micro batch", kind="int", minimum=1, maximum=64, help="Per-device batch size; raise only if VRAM allows."))
    grad_accum: int = field(default=8, metadata=meta("Gradient accumulation", kind="int", minimum=1, maximum=256))
    epochs: int = field(default=1, metadata=meta("Epochs", kind="int", minimum=1, maximum=20))
    max_steps: int = field(default=0, metadata=meta("Max steps", kind="int", minimum=0, maximum=200000, help="0 = derive from epochs and dataset size."))
    precision: str = field(default="auto", metadata=meta("Precision", kind="choice", choices=("auto", "4bit", "8bit", "bf16", "fp16", "fp32"), help="'auto' chooses 4-bit QLoRA when VRAM is tight."))
    max_vram_percent: int = field(default=85, metadata=meta("VRAM ceiling", kind="int", minimum=30, maximum=100, unit="%", help="Training stops growing its footprint beyond this share of VRAM."))
    checkpoint_every: int = field(default=50, metadata=meta("Checkpoint every", kind="int", minimum=5, maximum=5000, step=5, unit="steps"))
    resume: bool = field(default=True, metadata=meta("Resume interrupted runs", kind="bool", help="Restart a killed run from its last checkpoint."))
    background_priority: str = field(default="low", metadata=meta("Background priority", kind="choice", choices=("idle", "low", "normal"), help="How aggressively training competes with agent tasks."))
    auto_promote: bool = field(default=False, metadata=meta("Auto-promote winners", kind="bool", help="Promote a candidate automatically when the measured eval improves without regressions."))
    promote_min_gain: float = field(default=0.02, metadata=meta("Minimum gain", kind="float", minimum=0.0, maximum=0.5, step=0.005, help="Score improvement required before a candidate may be promoted."))
    quality_threshold: float = field(default=0.6, metadata=meta("Example quality gate", kind="float", minimum=0.0, maximum=1.0, step=0.05, help="Only task traces scoring at least this are exported as training examples."))
    curated_only: bool = field(default=True, metadata=meta("Verified examples only", kind="bool", help="Export just traces whose verification actually passed."))
    min_tool_examples: int = field(default=8, metadata=meta("Minimum training examples", kind="int", minimum=4, maximum=2000, advanced=True, help="A tool-selection run needs at least this many labelled examples before it will start; below it, CodeMind refuses instead of training on noise."))


@dataclass
class ResourceSettings:
    governor: str = field(default="auto", metadata=meta("Resource governor", kind="choice", choices=("auto", "balanced", "performance", "eco", "manual"), help="How CodeMind divides CPU/RAM/VRAM between the agent and training."))
    cpu_reserve_percent: int = field(default=15, metadata=meta("CPU reserve", kind="int", minimum=0, maximum=80, unit="%", help="Share of cores kept free for the desktop and the agent."))
    ram_reserve_mb: int = field(default=1024, metadata=meta("RAM reserve", kind="int", minimum=128, maximum=16384, step=128, unit="MB"))
    vram_reserve_mb: int = field(default=512, metadata=meta("VRAM reserve", kind="int", minimum=0, maximum=8192, step=128, unit="MB"))
    training_cpu_percent: int = field(default=60, metadata=meta("Training CPU share", kind="int", minimum=5, maximum=100, unit="%", help="Ceiling for dataloader and CPU-side work while tasks are queued."))
    io_priority: str = field(default="low", metadata=meta("Training I/O priority", kind="choice", choices=("low", "normal", "high")))
    sample_interval: float = field(default=2.0, metadata=meta("Sampling interval", kind="float", minimum=0.5, maximum=30.0, step=0.5, unit="s", help="How often hardware usage is sampled for the Overview."))
    history_minutes: int = field(default=30, metadata=meta("History window", kind="int", minimum=5, maximum=240, unit="min", advanced=True))


@dataclass
class MemorySettings:
    enabled: bool = field(default=True, metadata=meta("Persistent memory", kind="bool", help="Store project knowledge, verified solutions and lessons across sessions."))
    retrieval_top_k: int = field(default=6, metadata=meta("Recall depth", kind="int", minimum=1, maximum=30, help="How many memories are injected into a task context."))
    learn_from_failures: bool = field(default=True, metadata=meta("Learn from failures", kind="bool", help="Record failed attempts so the same mistake is not repeated."))
    max_entries: int = field(default=20000, metadata=meta("Max memories", kind="int", minimum=100, maximum=1_000_000, step=100))
    min_quality: float = field(default=0.35, metadata=meta("Minimum quality", kind="float", minimum=0.0, maximum=1.0, step=0.05, help="Entries below this score are kept but not injected into prompts."))
    index_projects: bool = field(default=True, metadata=meta("Index projects", kind="bool", help="Build a searchable symbol/file index for each project on first use."))


@dataclass
class GithubSettings:
    enabled: bool = field(default=False, metadata=meta("GitHub integration", kind="bool", help="Enable repository, commit and pull-request workflows."))
    token_env: str = field(default="GITHUB_TOKEN", metadata=meta("Token variable", kind="str", help="Name of the environment variable (or gh CLI auth) holding your token. The token itself is never stored."))
    login: str = field(default="", metadata=meta("Default account", kind="str", help="Account used for API calls when the token does not identify one."))
    default_branch_prefix: str = field(default="codemind/", metadata=meta("Branch prefix", kind="str"))
    author_name: str = field(default="", metadata=meta("Commit author", kind="str", advanced=True))
    author_email: str = field(default="", metadata=meta("Commit email", kind="str", advanced=True))
    draft_prs: bool = field(default=True, metadata=meta("Open PRs as draft", kind="bool"))
    run_checks_after_push: bool = field(default=True, metadata=meta("Watch CI checks", kind="bool", help="Poll the check suite after pushing a branch and report the outcome."))
    repositories: list[str] = field(default_factory=list, metadata=meta("Repositories", kind="list", help="owner/name entries shown in the GitHub view."))


@dataclass
class AutonomySettings:
    enabled: bool = field(default=False, metadata=meta("Autonomous mode", kind="bool", help="Let CodeMind work on approved maintenance between your requests."))
    proposal_mode: str = field(default="approve_first", metadata=meta("Proposals", kind="choice", choices=("approve_first", "notify", "auto"), help="How proposals from autonomous runs are handled before any code changes."))
    categories: list[str] = field(default_factory=lambda: ["repo_analysis", "test_improvement", "bug_fix", "docs"], metadata=meta("Approved work", kind="list", choices=("repo_analysis", "test_improvement", "bug_fix", "docs", "refactor", "experiment"), help="Kinds of work autonomous mode may propose."))
    repos: list[str] = field(default_factory=list, metadata=meta("Watched projects", kind="list", help="Project paths or owner/repo slugs autonomous mode may analyse."))
    interval_minutes: int = field(default=120, metadata=meta("Interval", kind="int", minimum=5, maximum=2880, step=5, unit="min", help="Time between autonomous sessions."))
    daily_budget_minutes: int = field(default=60, metadata=meta("Daily budget", kind="int", minimum=5, maximum=1440, step=5, unit="min", help="Total autonomous working time per day."))
    max_actions_per_session: int = field(default=6, metadata=meta("Actions per session", kind="int", minimum=1, maximum=100))
    quiet_hours: str = field(default="", metadata=meta("Quiet hours", kind="str", help="e.g. 22:00-07:00 — autonomous runs pause in this window."))
    respect_tasks: bool = field(default=True, metadata=meta("Yield to tasks", kind="bool", help="Pause autonomous work while you have queued tasks."))


@dataclass
class SecuritySettings:
    redact_secrets: bool = field(default=True, metadata=meta("Redact secrets", kind="bool", help="Strip credentials from logs, tool output and diffs."))
    confirm_destructive: bool = field(default=True, metadata=meta("Confirm destructive actions", kind="bool", help="Always ask before anything irreversible, even in autonomous mode."))
    allow_outside_workspace_writes: bool = field(default=False, metadata=meta("Write outside workspace", kind="bool", help="Allow the agent to edit files outside a task workspace (e.g. ~/.gitconfig)."))
    audit_log: bool = field(default=True, metadata=meta("Audit log", kind="bool", help="Persist every tool call with its outcome for review."))
    max_tool_calls_per_task: int = field(default=500, metadata=meta("Tool call ceiling", kind="int", minimum=10, maximum=10000, help="Safety valve against pathological loops."))


@dataclass
class LoggingSettings:
    level: str = field(default="info", metadata=meta("Log level", kind="choice", choices=("debug", "info", "warning", "error"), help="Verbosity of the structured log (also written as JSONL)."))
    keep_days: int = field(default=14, metadata=meta("Keep logs", kind="int", minimum=1, maximum=365, unit="days"))
    console: bool = field(default=False, metadata=meta("Echo to console", kind="bool", help="Print log lines to stdout in addition to the file."))


@dataclass
class StorageSettings:
    root: str = field(default="", metadata=meta("Storage root", kind="path", help="Where the database, logs and artefacts live. Empty = XDG data directory."))
    usb_path: str = field(default="", metadata=meta("USB drive", kind="path", help="Override auto-detection with an explicit mount path."))
    usb_marker: str = field(default=".codemind", metadata=meta("USB marker file", kind="str", help="A file with this name marks a drive as CodeMind storage."))
    usb_categories: list[str] = field(default_factory=lambda: ["datasets", "checkpoints", "models", "logs"], metadata=meta("USB-managed data", kind="list", choices=("datasets", "checkpoints", "models", "logs", "artifacts", "projects"), help="Categories stored on the USB drive when it is attached."))
    usb_auto_create_marker: bool = field(default=True, metadata=meta("Mark new drives", kind="bool", help="Offer to initialise a detected removable drive for CodeMind."))
    projects_root: str = field(default="", metadata=meta("Projects root", kind="path", help="Where repositories and sandboxes are cloned. Empty = storage root/projects."))
    prune_workspaces_days: int = field(default=14, metadata=meta("Prune workspaces", kind="int", minimum=1, maximum=365, unit="days", help="Sandbox workspaces older than this are deleted."))


SECTIONS: dict[str, type] = {
    "app": AppSettings,
    "models": ModelSettings,
    "agent": AgentSettings,
    "sandbox": SandboxSettings,
    "training": TrainingSettings,
    "resources": ResourceSettings,
    "memory": MemorySettings,
    "github": GithubSettings,
    "autonomy": AutonomySettings,
    "security": SecuritySettings,
    "logging": LoggingSettings,
    "storage": StorageSettings,
}

SECTION_TITLES = {
    "app": "Application",
    "models": "Models & inference",
    "agent": "Coding agent",
    "sandbox": "Sandbox & execution",
    "training": "Training",
    "resources": "Resources",
    "memory": "Memory & learning",
    "github": "GitHub",
    "autonomy": "Autonomy",
    "security": "Security & permissions",
    "logging": "Logging",
    "storage": "Storage",
}


@dataclass
class Config:
    app: AppSettings = field(default_factory=AppSettings)
    models: ModelSettings = field(default_factory=ModelSettings)
    agent: AgentSettings = field(default_factory=AgentSettings)
    sandbox: SandboxSettings = field(default_factory=SandboxSettings)
    training: TrainingSettings = field(default_factory=TrainingSettings)
    resources: ResourceSettings = field(default_factory=ResourceSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    github: GithubSettings = field(default_factory=GithubSettings)
    autonomy: AutonomySettings = field(default_factory=AutonomySettings)
    security: SecuritySettings = field(default_factory=SecuritySettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    schema_version: int = 3

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {name: _section_to_dict(getattr(self, name)) for name in SECTIONS} | {"schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "Config":
        config = cls()
        if not payload:
            return config
        for name, section_cls in SECTIONS.items():
            raw = payload.get(name)
            if isinstance(raw, Mapping):
                setattr(config, name, _section_from_dict(section_cls, raw))
        try:
            config.schema_version = int(payload.get("schema_version", config.schema_version))
        except (TypeError, ValueError):
            pass
        return config

    # -- access -----------------------------------------------------------
    def section(self, name: str) -> Any:
        if name not in SECTIONS:
            raise ConfigError(f"unknown settings section {name!r}")
        return getattr(self, name)

    def get(self, dotted: str) -> Any:
        section, _, name = dotted.partition(".")
        if not name:
            raise ConfigError(f"expected 'section.field', got {dotted!r}")
        return getattr(self.section(section), name)

    def set(self, dotted: str, value: Any) -> None:
        section, _, name = dotted.partition(".")
        target = self.section(section)
        if name not in {f.name for f in fields(target)}:
            raise ConfigError(f"unknown setting {dotted!r}")
        descriptor = _descriptor(target, name)
        setattr(target, name, _coerce(descriptor, value, dotted))

    def apply(self, values: Mapping[str, Any]) -> list[str]:
        """Apply a {dotted: value} mapping, returning the list of changed keys."""
        changed: list[str] = []
        for key, value in values.items():
            before = self.get(key)
            self.set(key, value)
            if self.get(key) != before:
                changed.append(key)
        return changed

    def validate(self) -> None:
        for name in SECTIONS:
            section = getattr(self, name)
            for spec in fields(section):
                value = getattr(section, spec.name)
                setattr(section, spec.name, _validate_value(f"{name}.{spec.name}", spec.metadata or meta(spec.name), value))

    def copy(self) -> "Config":
        return Config.from_dict(copy.deepcopy(self.to_dict()))

    def schema(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for name in SECTIONS:
            section = getattr(self, name)
            entries: list[dict[str, Any]] = []
            for spec in fields(section):
                descriptor = dict(spec.metadata or meta(spec.name))
                descriptor["name"] = spec.name
                descriptor["key"] = f"{name}.{spec.name}"
                descriptor["section"] = name
                descriptor["value"] = getattr(section, spec.name)
                if spec.default is not MISSING and spec.default_factory is not MISSING:  # type: ignore[misc]
                    descriptor["default"] = getattr(section, spec.name)
                elif spec.default is not MISSING and not isinstance(spec.default, type(MISSING)):
                    pass
                entries.append(descriptor)
            out[name] = entries
        return out


# --------------------------------------------------------------------------- helpers


def _descriptor(section: Any, name: str) -> dict[str, Any]:
    for spec in fields(section):
        if spec.name == name:
            return dict(spec.metadata or meta(name))
    raise ConfigError(f"unknown field {name}")


def _section_to_dict(section: Any) -> dict[str, Any]:
    return {spec.name: _jsonable(getattr(section, spec.name)) for spec in fields(section)}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return list(value)
    if is_dataclass(value):  # pragma: no cover - sections are flat
        return _section_to_dict(value)
    return value


def _section_from_dict(section_cls: type, payload: Mapping[str, Any]) -> Any:
    kwargs: dict[str, Any] = {}
    for spec in fields(section_cls):
        if spec.name in payload:
            kwargs[spec.name] = _coerce(dict(spec.metadata or meta(spec.name)), payload[spec.name], f"{section_cls.__name__}.{spec.name}")
    instance = section_cls(**kwargs)
    for spec in fields(section_cls):
        value = getattr(instance, spec.name)
        setattr(instance, spec.name, _validate_value(f"{section_cls.__name__}.{spec.name}", dict(spec.metadata or meta(spec.name)), value))
    return instance


def _coerce(descriptor: Mapping[str, Any], value: Any, key: str) -> Any:
    kind = descriptor.get("kind", "str")
    if kind == "json":
        return value if isinstance(value, dict) else {}
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return True
            if lowered in ("0", "false", "no", "off"):
                return False
        raise ConfigError(f"{key} expects true/false, got {value!r}")
    if kind == "int":
        try:
            return int(round(float(value)))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key} expects an integer, got {value!r}") from exc
    if kind == "float":
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key} expects a number, got {value!r}") from exc
    if kind == "list":
        if isinstance(value, str):
            parts = [p.strip() for p in value.replace("\n", ",").split(",")]
            return [p for p in parts if p]
        if isinstance(value, (list, tuple, set)):
            return [str(v) for v in value]
        raise ConfigError(f"{key} expects a list, got {value!r}")
    if kind == "choice":
        text = str(value)
        choices = descriptor.get("choices") or ()
        if choices and text not in choices:
            raise ConfigError(f"{key} must be one of {', '.join(choices)}")
        return text
    return "" if value is None else str(value)


def _validate_value(key: str, descriptor: Mapping[str, Any], value: Any) -> Any:
    value = _coerce(descriptor, value, key)
    kind = descriptor.get("kind", "str")
    if kind in ("int", "float"):
        minimum, maximum = descriptor.get("minimum"), descriptor.get("maximum")
        if minimum is not None and value < minimum:
            raise ConfigError(f"{key} must be ≥ {minimum} (got {value})")
        if maximum is not None and value > maximum:
            raise ConfigError(f"{key} must be ≤ {maximum} (got {value})")
    if kind in ("path", "str") and value:
        if "\x00" in value:
            raise ConfigError(f"{key} contains a null byte")
    if kind == "list":
        for item in value:
            if "\x00" in item:
                raise ConfigError(f"{key} contains a null byte")
    return value


class ConfigStore:
    """Loads, validates and persists the settings file, notifying subscribers on change."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._listeners: list[Any] = []
        self._config = Config()
        self.load_errors: list[str] = []

    # -- io ---------------------------------------------------------------
    def load(self) -> Config:
        payload = read_json(self.path, default=None)
        errors: list[str] = []
        if payload is None:
            config = Config()
        elif not isinstance(payload, Mapping):
            errors.append(f"{self.path} does not contain a JSON object; defaults restored")
            config = Config()
        else:
            try:
                config = Config.from_dict(payload)
            except ConfigError as exc:
                errors.append(f"settings were reset to defaults: {exc}")
                config = Config()
        try:
            config.validate()
        except ConfigError as exc:
            errors.append(f"invalid value replaced by default: {exc}")
            config = _repair(config, exc)
        self._apply_env_overrides(config)
        with self._lock:
            self._config = config
            self.load_errors = errors
        return config

    def _apply_env_overrides(self, config: Config) -> None:
        overrides = {
            "CODEMIND_BACKEND": "models.backend",
            "CODEMIND_MODEL": "models.active_model",
            "CODEMIND_LOG_LEVEL": "logging.level",
            "CODEMIND_STORAGE_ROOT": "storage.root",
            "CODEMIND_USB_PATH": "storage.usb_path",
            "CODEMIND_PROJECTS_ROOT": "storage.projects_root",
            "CODEMIND_AGENT_AUTONOMY": "agent.autonomy",
            "CODEMIND_BASE_MODEL": "training.base_model",
        }
        for env_name, key in overrides.items():
            value = os.environ.get(env_name)
            if value:
                try:
                    config.set(key, value)
                except ConfigError:
                    continue

    @property
    def config(self) -> Config:
        with self._lock:
            return self._config

    def save(self, config: Config | None = None) -> None:
        target = config or self.config
        target.validate()
        with self._lock:
            self._config = target
            atomic_write_json(self.path, target.to_dict())

    def update(self, values: Mapping[str, Any]) -> list[str]:
        with self._lock:
            candidate = self._config.copy()
            changed = candidate.apply(values)
            candidate.validate()
            self._config = candidate
            atomic_write_json(self.path, candidate.to_dict())
        for listener in list(self._listeners):
            try:
                listener(candidate, changed)
            except Exception:  # pragma: no cover - listener isolation
                pass
        return changed

    def reset_section(self, section: str) -> None:
        if section not in SECTIONS:
            raise ConfigError(f"unknown settings section {section!r}")
        with self._lock:
            candidate = self._config.copy()
            setattr(candidate, section, SECTIONS[section]())
            candidate.validate()
            self._config = candidate
            atomic_write_json(self.path, candidate.to_dict())
        for listener in list(self._listeners):
            try:
                listener(candidate, [f"{section}.*"])
            except Exception:  # pragma: no cover
                pass

    def subscribe(self, listener: Any) -> Any:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None


def _repair(config: Config, error: ConfigError) -> Config:
    """Fall back to defaults for the offending key, keeping everything else."""
    message = str(error)
    key = message.split(" ", 1)[0]
    if "." in key:
        section, _, name = key.partition(".")
        if section in SECTIONS and hasattr(getattr(config, section), name):
            setattr(getattr(config, section), name, getattr(SECTIONS[section](), name))
    return config


def iter_fields(config: Config) -> Iterable[tuple[str, dict[str, Any], Any]]:
    for section, entries in config.schema().items():
        for entry in entries:
            yield section, entry, entry["value"]


def default_config_path(data_root: Path) -> Path:
    return Path(data_root) / SETTINGS_FILENAME
