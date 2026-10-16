# CodeMind — final report

*Autonomous local coding assistant, native Ubuntu desktop application.*
Branch `arena/49ca5a03-agentos`, commit `608fa7a`. Everything below was executed on this machine; the
numbers are real, not projections.

---

## 1. What is implemented

### 1.1 Desktop application (`python3 main.py`)

A native PySide6/Qt6 window — **not** a web page. Seven views, all fed by live application state
through `CodeMindApp.overview_snapshot()` and domain APIs, all refreshing on timers or events:

| View | Content |
| --- | --- |
| **Overview** | CPU/RAM/GPU/storage/task/memory/learning tiles, real CPU and memory history sparklines, current work with running background processes, training summary, memory mix, autonomy proposals with Accept/Dismiss, "Run a session now" |
| **Coding Agent** | Task input with mode + project, live plan timeline with per-step evidence, tool-call stream, log, diffs, test output, cancel/pause/resume, approval card (Approve/Deny) |
| **Training** | Hardware/environment report, method availability with the exact missing requirement, dataset picker, Start/Cancel/Pause/Resume/Evaluate/Promote/Reject, live phase/step/loss/throughput tiles, loss+accuracy graph, progress, machine resource strip, runs table, checkpoints, comparisons |
| **Sandbox** | Workspace picker, command input with suggestion chips, run output, background processes with stop/stop-all and per-job logs, execution history, real resource limits and the enforced command policy |
| **Memory & Models** | Searchable memory table with kind filter, pin/unpin, forget, export JSONL, prune; detail pane with evidence; manual "Remember" form; indexed project knowledge table; local model catalogue with hardware fit notes and download actions |
| **GitHub** | Credential status and how it was found (gh CLI / env / settings), repositories, local checkouts, branch/commit/push/pull-request/checks controls, commit log, diff pane, action history |
| **Settings** | Every configuration value with its real type, range and help text, validated before being accepted, persisted to `settings.json` and applied live |

Design: dark translucent glass cards, gradients and hairline borders, consistent spacing and
typography, empty states that explain what to do, no fake data — a tile shows `—` when the sensor is
unavailable (GPU on this machine) instead of inventing a number.

Robustness worth noting: every view implements `safe_refresh()`, which returns the sections that
failed instead of letting one bad value blank the window; the status bar reports the first failure
and the rest of the interface keeps working.

### 1.2 The agent

`codemind/agent/` — planner, executor, `ToolRegistry` (26 tools), `PermissionPolicy` +
`ApprovalBroker`, `TaskStore` (SQLite), `TaskRunner` (queue, parallelism from the governor,
cancellation, pause/resume, retry), `FileJournal` (before-images and unified diffs).

Behaviour, as implemented and observed:

1. **Inspect before touching anything** — every task starts with `analyze_repository`: languages,
   entry points, dependencies, test files, *test commands*, top directories. The facts feed the plan
   and are quoted in the summary.
2. **Plan** — the deterministic planner (always available) or a local model when one is reachable;
   plans are validated: unknown tools are replaced, a verification step is guaranteed, an inspection
   step is inserted when the plan lacked one.
3. **Execute** — shell, Python, Git, package installers, project test suites, file edits; each step
   records arguments, result and origin (`plan`, `repair`, `repair:<n>`).
4. **Diagnose → repair → retry** — a failing step is diagnosed (signature, likely cause, suggested
   fix), a repair step is inserted, the original step is retried; a repeated identical failing action
   is refused and lessons are written to memory. Repair budget: 6.
5. **Verify with evidence** — `changes` + `tests` checks; `verified` requires all of them. A task
   with no evidence is reported as `failed`/`blocked`, never as completed.
6. **Report honestly** — summariser output states the planner used, tool calls, steps done, files
   changed with insertions/deletions, evidence and duration; blocked tasks say what they need
   (a local model, a credential, a missing tool).
7. **Persist and resume** — tasks, steps, tool calls, events and diffs live in SQLite; a restart
   restores the queue and the history and can resume interrupted tasks.

Permissions: reading, searching, running tests and editing inside the task workspace never prompt.
Destructive commands, credential access and irreversible external actions (push, PR, publishing)
require approval; blocked patterns are refused outright. Profiles: `manual`, `supervised`, `trusted`,
`autonomous`. Autonomous mode is limited to maintenance, bug fixes, test improvements, repository
analysis and experiments, with a daily minute budget.

### 1.3 Memory and learning (two separate systems)

- **Memory** (`codemind/memory/`): SQLite + FTS5, kinds (architecture, preference, solution, mistake,
  task summary, note, knowledge), content-hash dedupe keys, quality scores, pinning, pruning, JSONL
  export, retrieval with a context block injected into planning. Verified solutions are stored after a
  successful, verified task; failures produce lessons that are fed back as prior lessons.
- **Training** (`codemind/training/`): genuinely separate. Datasets (JSONL, validated, train/validation
  split, rejection report) → methods (QLoRA, LoRA, lightweight adapter, retrieval index) → a worker
  process/thread that writes checkpoints, metrics and JSONL logs → evaluation against a labelled
  benchmark suite *and* the untouched baseline → promotion only on measured improvement with no
  regressions. The GUI is a monitor; training needs no GUI.

### 1.4 Sandbox, system, storage, GitHub

- Workspaces per task (copy/isolate mode), rlimits + cgroup v2 accounting, address-space and CPU
  limits, timeouts, process-tree kills, blocked command patterns, configurable network, secrets
  blacklisted from `git` operations, execution history with policy snapshot.
- Hardware probe (CPU model/cores, RAM, GPUs via nvidia-smi, disks, OS/kernel/Python, ML stack) and a
  resource governor that samples load and decides eco/balanced/performance behaviour: agent
  parallelism, training threads, nice levels — no fixed artificial limits.
- Storage: `StorageLayout` resolves categories (databases, logs, projects, datasets, models,
  checkpoints, workspaces, backups, cache) under an internal or detected USB root; USB volumes are
  discovered from `/proc/self/mountinfo` + sysfs removability, never hardcoded; absence is handled
  without losing task state (pending-sync list).
- GitHub: `gh` CLI or token (never printed), `status`, `repo_status`, `log`, `diff`, `branches`,
  `create_branch`, `commit`, `push`, `create_pull_request`, `list_pull_requests`, `checks`,
  `clone`, `list_repositories`, action history; every payload and log line passes the redactor.

### 1.5 Training, concretely

- Curated starter set: **171 hand-written examples covering all 26 tools** (154 train / 17 validation),
  guarded by `overlaps_benchmark()` which fails the install if any instruction also appears in the
  evaluation suite.
- Evaluation suites: `coding_core`, `tool_use` (21 labelled items), `instruction_following`,
  `regression`, `all`.
- An example, from a real run in this environment (2 cores, no GPU):

```
tool-selection training run     1848 steps, final loss 2.6453, val accuracy 0.2941
benchmark (tool_use, 21 items)  baseline  0.2381  (5/21)
                                candidate 0.8571 (18/21)
decision                        promote: "Improved by +0.619 (required ≥ +0.020), no regressions"
artefact                        models/active/codemind-adapter/adapter.json
```

The promoted adapter is loaded by the planner as an advisor (`suggest_tool`), so the benchmark result
has a visible effect: with it active, tool choices in real tasks are adopted when confidence is high.

---

### 1.6 Evidence: proving the work (added after the first report)

The promise "no success without evidence" is now something the product measures and hands over:

- **`app.quality_report()`** (Overview tile *Evidence*, status-bar pill, `snapshot["quality"]`) counts
  completed tasks that carry passing checks and *names* the ones that do not; unproven claims appear
  under *Attention* with their task id.
- **Change passport** (`codemind/report/passport.py`, button *Passport*, `Ctrl+E`): one self-contained
  record per task — goal, plan, every step with tool/status/origin, every tool call, the diffs, the
  verification checks, the backend and (if present) the trained adapter — exported as JSON and
  Markdown into `artifacts/passports/`. All entries are chained with SHA-256
  (`h_n = sha256(h_{n-1} ‖ entry_n)`), so a later edit of the file is detectable; `verify_chain()`
  re-checks it. The chain proves the file was not modified after it was written — it does not prove
  authorship, and the report says so instead of implying more.
- **Rollback** (`codemind/agent/rollback.py`): `FileJournal` now stores the pre-image of every written
  file in the database (`task_backups`: path, timestamp, text, whether the file existed at all).
  `app.rollback_task(task_id)` restores the earliest pre-image per path, deletes files that did not
  exist before, reports every action, and refuses outright while the task is still running or when a
  task never changed a file.

### 1.7 Interface work in this phase

`AppSettings` (`theme_mode`, `glass_intensity`, `accent`, `motion`, `start_view`, `restore_geometry`,
`minimize_to_tray`, `show_splash`) was previously declared but read by nothing. It now drives the
window: three surface presets (`liquid_glass`, `graphite`, `midnight`), a glass-intensity factor
applied inside `paint_glass`, one accent that is re-read by every style string, and three motion
levels (`full`, `reduced`, `off`) that every animation in `codemind/ui/anim.py` respects. Added on
top: a `Ctrl+K` command palette built from the live application (22 actions, fuzzy-filtered), a tray
icon with notifications, remembered geometry, a splash during the hardware probe, animated stat
tiles and charts, a cross-fading page stack, and a status bar that reports the backend, the tool
count, the evidence rate and the governor instead of a static boot message.

## 2. What was tested, and the results

### 2.1 Automated suite

```bash
python3 -m unittest discover -s tests -t .                        # GUI tests skip themselves headless
./.venv/bin/python -m unittest discover -s tests -t .             # includes the GUI + launcher tests
# headless machines with a display plugin available:
LD_LIBRARY_PATH=<gl-stubs> QT_QPA_PLATFORM=vnc ./.venv/bin/python -m unittest discover -s tests -t .
```

| Module | Tests | Coverage |
| --- | --- | --- |
| `test_structure.py` | 7 | no shadowed definitions, no hardcoded machine paths, no stubs, no cloud-inference endpoints, every module imports, Qt only in the UI, README/entry points present |
| `test_core.py` | 8 | database round-trip + integrity, memory write/search, storage layout and USB detection, settings persistence and validation, hardware probe, governor under pressure, sandbox execute/policy denial/history, background jobs, event bus |
| `test_agent.py` | 12 | tool inventory and risk levels, permission classification, layout-aware test-command detection (executed for real), `git_status` both ways, repository analysis, a full run with verification evidence, the diagnose→repair→retry loop, cancellation that really kills a 90 s command, restart survival, no invented code, planner verification step |
| `test_training.py` | 7 | curated set without benchmark leakage, import/export/delete datasets, training examples only from verified successes, method availability honesty, a real training run → evaluation → promotion, restart persistence, rejection |
| `test_utils.py` | 19 | text/file helpers (`shorten(None)`), command timeout and process termination, child processes do not inherit secrets, redaction of seven credential shapes, secrets absent from log file, execution history and tool-call history, config validation and repair, storage layout, background-job logs on disk, USB category routing |
| `test_ui.py` | 11 | `--version`, `--check`, `--doctor`, `--task` exit codes and output, re-exec guards, all seven views refreshing twice with real state (job + memory), one broken section not blanking the window, the command palette (every action callable, filtering, running a command), appearance settings reaching the theme (surface, accent, glass, motion, start view), geometry memory and that the glass surfaces really paint |
| `test_evidence.py` | 10 | writes record pre-images and roll back (restore/delete/refuse), the change passport's contents, hash chain, tamper detection and JSON/Markdown export, the honesty report counting only proven work, the snapshot the interface polls |

Result on this machine (2 threads, 3.8 GB RAM, no GPU):

```
Ran 74 tests in 70.0 s — OK          (with Qt available, also clean under -W error::ResourceWarning)
```

No test needs a GPU, a model download or network access.

### 2.2 Manual verification

| Check | Result |
| --- | --- |
| `python3 main.py --check` | `check finished with no problems` — PySide6 installed, 26 tools, sandbox `limited: rlimits, cgroup v2 accounting`, training `adapter_lite, retrieval_index` available, `qlora/lora` marked unavailable with reasons, GitHub `Authenticated as jonas050210` |
| `python3 main.py --doctor` | JSON with hardware, inference, training, sandbox, policy, health, storage and the 26-tool inventory |
| `python3 main.py` (plain, system python) | detects the missing PySide6, re-executes into `./.venv/bin/python`, opens the native window (verified under a virtual display), writes structured JSONL logs, creates the full storage layout on first launch |
| `python3 main.py --task "Run the test suite …" --project …` | `status: completed`, 6 steps, evidence `[PASS] changes`, `[PASS] tests: Tests: 1 passed` |
| Same command on a layout the detector cannot handle | reported `failed` with the accurate reason — the agent does not claim success without evidence |
| GUI renders | all seven views captured and reviewed (`assets/screenshot-overview.png`, `assets/screenshot-agent.png`); icon rendered by `tools/make_icon.py` |
| Restart behaviour | tasks, memories, settings, datasets, runs and the active adapter all survive a new process |
| Appearance settings | `theme_mode`, `glass_intensity`, `accent`, `motion`, `start_view`, `restore_geometry` verified against the live theme (surfaces change the page gradient, motion `off` renders instantly, the start view is honoured) |
| Command palette | 22 real actions, filtered by typing, `Enter` runs the highlighted one; the palette is the only place where every feature must be reachable and it is covered by a test |
| Change passport | written for a finished task, `verify_chain()` clean, content includes plan, tool calls, diffs and checks; flipping one entry makes the verification fail |
| Rollback | a task that edited one file and created another was rolled back: file byte-identical again, the created file removed, the action recorded as a task event |
| Renders reviewed | all seven views plus the command palette (`tools/shots.py`), including a live background job and a trained adapter |

### 2.3 Defects found and fixed while testing

Each of these was a real bug, not a hypothetical:

1. `GithubClient` defined `status()` twice — the second definition shadowed the local git status, so
   the **`git_status` tool failed at runtime**. Found by the duplicate-definition scan now in
   `test_structure.py`.
2. `SandboxView` referenced an undefined `jobs_card` — the Sandbox view failed to refresh whenever a
   background job existed. Fixed and covered by refreshing views *with* a running job.
3. `python3 main.py` never reached the GUI when the venv's interpreter resolved to the same binary as
   the system one; venv detection now uses `sys.prefix`.
4. The same re-exec path forwarded the host process's `argv`, so importing `main.py` could replace the
   calling process. It now only re-executes from a real `main.py` invocation.
5. Test-command detection was layout-blind: a `tests/` directory without `__init__.py` gave
   "Ran 0 tests", and the agent rightly reported a failure. Detection now validates the layout and the
   detected command is executed in the test suite to prove it collects tests.
6. The Training view displayed learning rate `0.25` while the run used the stored `0.0002` — a fake
   value; it now shows and sends the configured default.
7. `Memory & Models` lost its ampersand in the sidebar (Qt mnemonic), and was then elided; the label
   is escaped and the sidebar is wide enough.
8. `shorten(None)` raised on NULL `reason` columns, blanking a view; the helper is total now.
9. Pipes leaked in `run_command` and the limited sandbox path (visible only under
   `-W error::ResourceWarning`); they are closed and reaped.
10. `sandbox.history()` crashed on background-job rows whose `argv`/`policy` were plain text rather
    than JSON.
11. **Background job logs were never written.** `JobManager._on_line` called `stat()` on the log file
    before creating it; the resulting `OSError` was swallowed, so every job had an empty log on disk
    and only lived in memory. Found by the new job-log test.
12. **The pre-image of a written file was never stored** (and a newly created file was recorded as if
    it had existed), because "first touch" was derived from the diffing map instead of the pre-write
    snapshot. Rollback would have restored nothing. Found by the rollback tests.
13. **One bad value in a hand-edited settings file discarded the whole file** — a single out-of-range
    number reset every section to defaults. Repairs are now per value and reported
    (`settings were reset` → `agent.max_steps was reset to 40: …`), neighbours survive.
14. Unknown settings raised `AttributeError` instead of the documented `ConfigError`.
15. The project picker offered a hardcoded `/tmp/sampleproj` from development; it now lists the
    configured projects root and the indexed projects only.
16. The status bar read `runner["active_tasks"]`, a key the runner never returned, so a running task
    was invisible there; it now uses `active_count`/`active_task` plus the real step counters.
17. `app.rollback_task()` called a non-existent `reconcile_artifacts()`; the passport wrote its
    backend under a key (`backend`) that the Markdown renderer did not read.
18. Two palette glyphs (`⌘`, `⌫`) rendered as empty boxes without a symbol font, and the palette
    button's `&` was a Qt mnemonic; both replaced.
19. `app.accent` defaulted to `#5AC8FA` inside the config but `#6EA8FE` inside the theme — two sources
    of truth for the same colour; the theme now follows the config, and a colour setting is validated
    as `#rrggbb`.

---

## 3. Remaining limitations (stated plainly)

1. **LoRA/QLoRA were not executed here.** This machine has no CUDA stack (no `torch`, `transformers`,
   `peft`, `bitsandbytes`), so the heavy path is implemented and reports its requirements precisely,
   but its training loop is *unverified*. What is verified end to end is `adapter_lite` and
   `retrieval_index`. On your RTX 4060 Ti, install the CUDA torch build from `requirements.txt` and
   run `.venv/bin/python main.py --train --method qlora --base-model <hf-id>`; until that has run once,
   treat QLoRA as untested rather than working.
2. **No local generative model was available in this environment**, so agent *authoring* tasks
   (writing new modules) are refused with an explanation instead of being faked — that is deliberate.
   Plans, analysis, tests, fixes to existing files and all tooling run with the deterministic planner.
   Point CodeMind at Ollama or a llama.cpp server (Settings → Models) to enable model-written code.
3. **GitHub write operations were not exercised** (no push/PR was performed against your GitHub
   account from here). Token discovery, repository listing and status are verified live; branch,
   commit, push and PR construction are verified structurally and through the git commands they run.
4. **GPU telemetry** comes from `nvidia-smi`; there is no GPU in this sandbox, so the GPU tile is
   empty here and the numbers are unverified on hardware.
5. **USB storage**: detection, category layout and absence handling are tested; writing to a real
   128 GB drive was not possible in this environment.
6. **Long-horizon autonomy** (scheduled sessions across days, budget accounting) is implemented and
   produces proposals that can be accepted, but multi-day behaviour is not proven.
7. Correctness of the interface is tested; **aesthetic quality** was judged by reviewing renders, not
   by users. The glass look is painted in Qt (gradients, translucency, hairlines) — there is no real
   backdrop blur behind the window, because Qt offers no cheap compositor blur; on a compositing
   desktop it is a close approximation, not the real thing.
7b. The **evidence rate** counts what the run recorded. A task whose verification step is weak is
   still only as trustworthy as its checks — which is exactly why the passport prints the checks
   instead of just a verdict.
7c. **Animations** are Qt property animations; on a slow machine the `reduced`/`off` motion settings
   exist because frame timing is not guaranteed there.
8. A few executor internals (`_verify` build guard, resume edge cases) are covered indirectly through
   whole-task tests rather than by unit tests.
9. The legacy TypeScript/React prototype is still in the tree (`src/`, `server/`, `shared/`,
   `docs/AGENTOS_LEGACY_*`). It is inert and kept for reference; it also slightly influences file
   ranking when the agent analyses *this* repository.

---

## 4. Exact launch instructions

```bash
cd /path/to/AgentOS                  # this branch
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt      # PySide6; optional CUDA extras are listed as comments

python3 main.py                      # native desktop application
```

`python3 main.py` works even without activating the venv: the launcher re-executes itself with
`./.venv/bin/python` when the current interpreter cannot import PySide6.

Self-check and headless use:

```bash
python3 main.py --check                          # human-readable installation check
python3 main.py --doctor                         # same as JSON
python3 main.py --task "Add tests for the parser" --project ~/code/project
python3 main.py --task "Fix the failing build" --mode autonomous --project ~/code/project
python3 main.py --train --name night-1 --method auto --install-seed --evaluate --promote
python3 main.py --data-root /media/$USER/CODEMIND   # keep all state on the USB drive
```

Tests:

```bash
./.venv/bin/python -m unittest discover -s tests -t .
```

Desktop entry (optional):

```bash
mkdir -p ~/.local/share/applications
sed "s#/opt/codemind#$(pwd)#g" assets/codemind.desktop > ~/.local/share/applications/codemind.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null || true
```

---

## 5. The most valuable further work

This phase implemented the first slice of `docs/ROADMAP_IDEAS.md`: **A1 Change Passport** (including
the hash chain), **A2 rollback/counterfactual basics** (rollback and its pre-images; the recorded
replay is still open) and **A3 evidence rate**, plus the whole interface group **D1–D8** (appearance
settings, motion layer, command palette, tray, geometry, splash, status truth). Still open from that
list: the replay view, the personal CI lane, the USB vault workflow, and every model/training item
(C1–C8) — in particular the QLoRA proof, which needs the CUDA stack that this machine does not have.


1. **Run the QLoRA path once on the 4060 Ti** and record the real curve (base model
   `Qwen2.5-Coder-7B-Instruct` in 4-bit, `train_on_inputs=false`). Everything except that loop is
   already in place; this single run is what turns "implemented" into "proven" for fine-tuning.
2. **Attach a real local model** (llama.cpp with a 7B GGUF, or Ollama) so authoring tasks can complete
   end to end. The planner/executor already treat model tool choices as suggestions and keep the
   deterministic planner as the fallback — the missing piece is model availability, not plumbing.
3. **Probe the test command before trusting it.** Detection now adapts to the layout; the next step is
   to run the candidate command in the workspace during analysis (cheap, ≤ a few seconds) and only
   believe it when it collects tests.
4. **Grow the evaluation suite** (`coding_core`, `regression`) with real failures from your own
   projects, and make promotion history visible in the GUI so model changes can be traced.
5. **Close the autonomy loop**: proposals currently become tasks; feeding their outcomes back into the
   scheduler (so categories that keep failing are tried less often) is the difference between a
   scheduler and a colleague.
6. **Interaction polish**: keyboard shortcuts and a command palette for the agent view, Pause/Resume on
   the Overview, zoomable training charts, and a "why?" panel that shows which memory entries
   influenced a plan.
7. **Package it**: a `.deb`/AppImage plus a systemd user unit for headless training on the PC, so the
   GUI becomes optional in practice as well as in principle.
