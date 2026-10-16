#!/usr/bin/env python3
"""CodeMind — autonomous local coding assistant.

Launch the desktop application:

    python3 main.py

Other modes (all optional):

    python3 main.py --headless            # no GUI: run the background services only
    python3 main.py --train               # start a training run and follow it in the terminal
    python3 main.py --task "…"            # run one task and print the report
    python3 main.py --doctor              # print an environment/diagnostic report
    python3 main.py --check               # verify the installation and exit

Everything runs on this machine: inference, training and storage. Local open-source models only.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIN_PYTHON = (3, 10)


def _die(message: str, code: int = 1) -> "None":
    print(f"CodeMind: {message}", file=sys.stderr)
    raise SystemExit(code)


def _check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        _die(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required (found {sys.version.split()[0]}).",
            2,
        )


def _qt_missing_help(error: Exception) -> str:
    return (
        f"The desktop interface needs PySide6 ({error}).\n"
        "Install it with:\n"
        "    python3 -m venv .venv && . .venv/bin/activate && pip install PySide6\n"
        "or, if your distribution packages it:\n"
        "    sudo apt install python3-pyside6.qtwidgets\n"
        "On this machine the interface was expected to use the virtualenv in ./.venv."
    )


def build_app(args: argparse.Namespace):
    from codemind.app import CodeMindApp

    data_root = args.data_root or os.environ.get("CODEMIND_DATA_DIR")
    return CodeMindApp(data_root=data_root, start_background=True)


# --------------------------------------------------------------------------- headless modes
def run_check(args: argparse.Namespace) -> int:
    """Verify the installation: imports, storage, hardware, sandbox, agent tools."""
    print("CodeMind installation check")
    print("-" * 60)
    try:
        app = build_app(args)
    except Exception as exc:
        print(f"[FAIL] could not start the application context: {exc}")
        return 1
    problems = 0
    try:
        print(f"[ok]   data root        {app.data_root}")
        print(f"[ok]   database         {app.layout.db_path}")
        print(f"[ok]   logs             {app.info.log_path}")
        app.wait_for_hardware(60)
        print(f"[ok]   hardware         {app.hardware.summary_line()} (tier {app.hardware.tier()})")
        backend = app.models.active_backend_info()
        print(f"[ok]   inference        {backend['name']} — {backend['reason']}")
        print(f"[ok]   agent tools      {len(app.registry.names())} registered")
        isolation = app.sandbox.isolation
        print(f"[ok]   sandbox          {isolation.level}: {isolation.summary()}")
        methods = [m for m in app.training.methods() if m["available"]]
        print(f"[ok]   training         available: {', '.join(m['name'] for m in methods)}")
        unavailable = [m for m in app.training.methods() if not m["available"]]
        for method in unavailable:
            print(f"[--]   training         {method['name']} unavailable: {'; '.join(method['requirements'][:1])}")
        health = app.health()
        for check in health["checks"]:
            flag = "ok" if check["ok"] else "warn"
            print(f"[{flag}]   {check['name']:<14} {check['detail']}")
            problems += 0 if check["ok"] else 1
        config_warnings = app.info.config_warnings
        for warning in config_warnings:
            print(f"[warn] settings        {warning}")
            problems += 1
        try:
            import PySide6  # noqa: F401

            print("[ok]   PySide6         installed — the desktop interface can start")
        except Exception as exc:
            print(f"[warn] PySide6         not usable here: {exc}")
            print("       " + _qt_missing_help(exc).replace("\n", "\n       "))
            problems += 1
    finally:
        app.shutdown()
    print("-" * 60)
    print("check finished with " + ("no problems" if problems == 0 else f"{problems} warning(s)"))
    return 0


def run_doctor(args: argparse.Namespace) -> int:
    import json

    app = build_app(args)
    try:
        app.wait_for_hardware(60)
        report = {
            "data_root": str(app.data_root),
            "hardware": app.hardware.as_dict(),
            "inference": app.models.active_backend_info(),
            "training": app.training.environment(),
            "sandbox": app.sandbox.status()["isolation"],
            "policy": app.sandbox.status()["policy"],
            "health": app.health(),
            "storage": app.layout.summary(),
            "tools": app.registry.names(),
        }
        print(json.dumps(report, indent=2, default=str))
    finally:
        app.shutdown()
    return 0


def run_task(args: argparse.Namespace) -> int:
    app = build_app(args)
    try:
        app.wait_for_hardware(60)
        project = args.project or os.getcwd()
        task = app.submit_task(args.task, project_path=project, title=args.task[:70], mode=args.mode)
        print(f"task {task.id} queued on {project} (mode {args.mode})")
        deadline = time.time() + args.timeout
        last_status = ""
        while time.time() < deadline:
            fresh = app.tasks.get(task.id)
            if fresh is None:
                break
            if fresh.status != last_status:
                last_status = fresh.status
                print(f"  status: {last_status}")
                for step in fresh.steps:
                    marker = {"done": "✓", "failed": "✗", "running": "▶", "needs_model": "◇", "pending": "·"}.get(step.status, "·")
                    print(f"    {marker} {step.idx + 1}. {step.title[:70]} [{step.tool}]")
            if fresh.is_terminal:
                break
            time.sleep(1)
        fresh = app.tasks.get(task.id)
        print("-" * 70)
        print(fresh.summary or "(no summary)")
        print("-" * 70)
        print(f"status: {fresh.status} · tool calls: {fresh.tool_calls} · workspace: {fresh.workspace_id or '—'}")
        return 0 if fresh.status == "completed" else 1
    finally:
        app.shutdown()


def run_train(args: argparse.Namespace) -> int:
    app = build_app(args)
    try:
        app.wait_for_hardware(60)
        if getattr(args, "install_seed", False):
            result = app.training.install_seed_dataset()
            print(f"starter dataset: {result['examples']} train / {result['validation_examples']} validation")
        method = args.method
        dataset = args.dataset
        run = app.training.start_run(name=args.name, method=method, dataset=dataset, base_model=args.base_model)
        print(f"run {run['id']} started ({run['method']}, dataset {Path(str(run['dataset'])).name})")
        deadline = time.time() + args.timeout
        last = ""
        while time.time() < deadline:
            detail = app.training.run_detail(run["id"])
            if detail["status"] != last:
                print(f"  status: {detail['status']}")
                last = detail["status"]
            progress = detail.get("progress") or {}
            if progress.get("step"):
                line = (
                    f"\r  step {progress.get('step')}/{progress.get('total_steps') or '?'} "
                    f"loss {progress.get('loss', 0):.4f} acc {progress.get('accuracy', 0):.3f} "
                    f"{progress.get('throughput', 0):.0f} ex/s"
                )
                print(line[:150], end="", flush=True)
            if detail["status"] in ("completed", "failed", "cancelled", "interrupted", "paused"):
                break
            time.sleep(1)
        print()
        detail = app.training.run_detail(run["id"])
        print(f"status: {detail['status']}")
        if detail.get("error"):
            print(f"error: {detail['error']}")
        metrics = detail.get("metrics") or {}
        if metrics:
            print(f"final loss {metrics.get('final_loss')} · accuracy {metrics.get('accuracy')} · steps {metrics.get('steps')}")
        print(f"checkpoints: {len(detail.get('checkpoints') or [])}")
        print(f"artifacts: {detail.get('artifacts')}")
        if args.evaluate and detail["status"] == "completed":
            result = app.training.evaluate_run(run["id"], suite=args.suite)
            decision = result.get("decision") or {}
            print(f"evaluation [{result.get('suite')}]: {result.get('score')} vs baseline {result.get('baseline_score')}")
            print(f"decision: {decision.get('promote')} — {decision.get('reason')}")
            if args.promote and decision.get("promote"):
                print(f"promoted: {app.training.promote(run['id'], note='CLI evaluation')}")
        return 0 if detail["status"] == "completed" else 1
    finally:
        app.shutdown()


def run_headless(args: argparse.Namespace) -> int:
    app = build_app(args)
    print(f"CodeMind headless — data root {app.data_root}. Ctrl-C to stop.")
    app.wait_for_hardware(60)
    print(f"hardware: {app.hardware.summary_line()} (tier {app.hardware.tier()})")
    print(f"inference: {app.models.active_backend_info()['name']}")
    print(f"autonomy: {'enabled' if app.config.autonomy.enabled else 'disabled'} · training: {'enabled' if app.config.training.enabled else 'disabled'}")
    stop = {"flag": False}

    def handle(signum, frame):  # pragma: no cover - signal path
        stop["flag"] = True

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)
    try:
        while not stop["flag"]:
            time.sleep(1)
    finally:
        app.shutdown()
    return 0


# --------------------------------------------------------------------------- gui
def _reexec_with_project_venv() -> bool:
    """Use ``./.venv/bin/python`` when the current interpreter cannot import Qt but the venv can.

    A checkout that ships its own virtualenv should launch with a plain ``python3 main.py``; this
    keeps that promise without asking the user to activate anything. It never loops: the child
    gets ``CODEMIND_REEXEC=1``.
    """
    if os.environ.get("CODEMIND_REEXEC") == "1":
        return False
    if Path(sys.argv[0]).name != "main.py":
        # main.py was imported (a test runner, a REPL, an installer script): those processes must
        # never be replaced, and their arguments are not launcher arguments.
        return False
    venv_dir = (ROOT / ".venv").resolve()
    candidate = venv_dir / "bin" / "python"
    if not candidate.exists():
        return False
    # `sys.prefix` is the authoritative "am I inside this virtualenv?" test. Comparing resolved
    # interpreter paths is not: a venv built with a symlinked or copied interpreter resolves to the
    # very same binary as the system python, and the GUI would then never be reached.
    try:
        if Path(sys.prefix).resolve() == venv_dir:
            return False
    except OSError:  # pragma: no cover - defensive
        return False
    import subprocess

    probe = subprocess.run([str(candidate), "-c", "import PySide6"], capture_output=True, timeout=60)
    if probe.returncode != 0:
        return False
    forwarded = [str(argument) for argument in sys.argv[1:]]
    print(f"CodeMind: using the virtualenv at {ROOT / '.venv'} (the current interpreter has no PySide6)", flush=True)
    env = {**os.environ, "CODEMIND_REEXEC": "1"}
    os.execve(str(candidate), [str(candidate), str(ROOT / "main.py"), *forwarded], env)
    return True  # pragma: no cover - execve replaces the process


def run_gui(args: argparse.Namespace) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:
        if _reexec_with_project_venv():
            return 0  # pragma: no cover
        print(_qt_missing_help(exc), file=sys.stderr)
        print("\nStarting in headless mode instead — the agent and training still work.\n", file=sys.stderr)
        return run_headless(args)
    from codemind.ui import theme as T
    from codemind.ui.main_window import MainWindow

    qt_app = QApplication(sys.argv[:1])
    qt_app.setApplicationName("CodeMind")
    from codemind.version import APP_VERSION

    qt_app.setApplicationVersion(APP_VERSION)
    qt_app.setStyle("Fusion")
    T.ensure_fonts()
    fonts = T.fonts()
    base = T.font(12)
    qt_app.setFont(base)
    app = build_app(args)
    window = MainWindow(app)
    window.show()

    def shutdown() -> None:
        try:
            app.shutdown()
        except Exception:
            pass

    qt_app.aboutToQuit.connect(shutdown)
    signal.signal(signal.SIGINT, lambda *_: qt_app.quit())
    # let Ctrl-C reach the interpreter while Qt owns the event loop
    keep_alive = __import__("PySide6.QtCore", fromlist=["QTimer"]).QTimer()
    keep_alive.start(400)
    keep_alive.timeout.connect(lambda: None)
    return qt_app.exec()


# --------------------------------------------------------------------------- cli
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codemind", description="CodeMind — autonomous local coding assistant")
    parser.add_argument("--data-root", default="", help="storage root (default: $CODEMIND_DATA_DIR or ~/.local/share/codemind)")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true", help="run the background services without a window")
    mode.add_argument("--check", action="store_true", help="verify the installation and exit")
    mode.add_argument("--doctor", action="store_true", help="print a diagnostic report as JSON")
    mode.add_argument("--task", default="", help="run one task and print the report")
    mode.add_argument("--train", action="store_true", help="start a headless training run")
    parser.add_argument("--project", default="", help="project directory for --task (default: current directory)")
    parser.add_argument("--mode", default="assisted", choices=("assisted", "autonomous", "plan_only"), help="permission mode for --task")
    parser.add_argument("--timeout", type=float, default=3600.0, help="maximum seconds to wait for a task")
    parser.add_argument("--name", default="", help="run name for --train")
    parser.add_argument("--method", default="auto", help="training method for --train (auto/adapter_lite/retrieval_index/qlora/lora)")
    parser.add_argument("--dataset", default="", help="dataset name or path for --train")
    parser.add_argument("--base-model", default="", help="base model for LoRA/QLoRA runs")
    parser.add_argument("--evaluate", action="store_true", help="evaluate the run against the baseline after --train")
    parser.add_argument("--promote", action="store_true", help="promote the run when the evaluation justifies it")
    parser.add_argument("--suite", default="coding_core", help="evaluation suite for --train --evaluate")
    parser.add_argument("--install-seed", action="store_true", help="write the curated starter dataset before training")
    return parser


def main(argv: list[str] | None = None) -> int:
    _check_python()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        from codemind.version import APP_NAME, APP_TAGLINE, APP_VERSION

        print(f"{APP_NAME} {APP_VERSION} — {APP_TAGLINE}")
        return 0
    if args.check:
        return run_check(args)
    if args.doctor:
        return run_doctor(args)
    if args.task:
        return run_task(args)
    if args.train:
        return run_train(args)
    if args.headless:
        return run_headless(args)
    return run_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())
