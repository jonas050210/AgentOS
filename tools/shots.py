"""Render every view headlessly and save screenshots (development aid, not part of the app).

Usage (on a machine with a display):
    .venv/bin/python tools/shots.py /tmp/shots

On a headless machine this is how the sandbox verification runs it:
    LD_LIBRARY_PATH=/tmp/stublibs QT_QPA_PLATFORM=vnc CODEMIND_DATA_DIR=/tmp/x \
        .venv/bin/python tools/shots.py /tmp/shots
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def seed(app, qt_app) -> None:
    """Create real state so the screenshots exercise populated code paths."""
    projects = [p for p in ("/tmp/sampleproj", "/tmp/proj2") if Path(p).exists()]
    tasks = []
    if projects:
        tasks.append(app.submit_task("Run the test suite of this project and report exactly what happens", project_path=projects[0], title="baseline test run"))
        if len(projects) > 1:
            tasks.append(app.submit_task("Explain the structure of this project and where the entry points are", project_path=projects[1], title="structure walkthrough"))
    deadline = time.time() + 240
    while tasks and time.time() < deadline:
        states = [app.tasks.get(task.id) for task in tasks]
        if all(state and state.is_terminal for state in states):
            break
        qt_app.processEvents()
        time.sleep(0.25)
    app.training.install_seed_dataset()
    run = app.training.start_run(name="screenshot run", method="adapter_lite", dataset="planner_seed")
    deadline = time.time() + 300
    while time.time() < deadline:
        detail = app.training.run_detail(run["id"])
        if detail["status"] in ("completed", "failed", "cancelled"):
            break
        qt_app.processEvents()
        time.sleep(0.25)
    if app.sandbox.list_workspaces():
        workspace = app.sandbox.list_workspaces()[0]
        app.sandbox.execute("echo sandbox smoke test", workspace=workspace, purpose="screenshot")
        app.jobs.start("http server", ["/bin/bash", "-lc", "python3 -m http.server 8123"], cwd=workspace.path)
        time.sleep(0.8)


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/codemind-shots")
    target.mkdir(parents=True, exist_ok=True)
    from PySide6.QtWidgets import QApplication

    qt_app = QApplication([sys.argv[0]])
    from codemind.app import CodeMindApp
    from codemind.ui.main_window import MainWindow, VIEWS

    app = CodeMindApp(start_background=True)
    app.wait_for_hardware(90)
    if "--seed" in sys.argv:
        seed(app, qt_app)
    window = MainWindow(app)
    window.resize(1480, 940)
    window.show()
    failures = 0
    for index, (title, _glyph, _cls) in enumerate(VIEWS):
        window.show_view(index)
        for _ in range(8):
            qt_app.processEvents()
            time.sleep(0.05)
        view = window.stack.currentWidget()
        path = target / f"{index + 1:02d}-{title.lower().replace(' ', '-').replace('&', 'and')}.png"
        if not view.grab().save(str(path)):
            print(f"could not save {path}")
            failures += 1
        print(f"{title:<16} -> {path}")
    # a second pass after the timer has collected live samples
    window.show_view(0)
    for _ in range(20):
        qt_app.processEvents()
        time.sleep(0.1)
    window.grab().save(str(target / "00-window.png"))
    print(f"window           -> {target / '00-window.png'}")
    app.shutdown()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
