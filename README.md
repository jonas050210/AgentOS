# AgentOS

**A local-first AI operating environment** where every specialist has its own workspace, its own
model versions and its own measured benchmark history — and where the models are trained on this
machine, not simulated.

AgentOS is a working system, not a mockup. The orchestrator routes real requests, tools execute with
real permission checks, training runs perform real gradient descent (or build a real index, or induce
a real lexicon), promotions are decided by re-measuring the candidate against the previous version,
and every status indicator reflects the actual state of the running process. Anything that is *not*
implemented or not configured is labelled as such in the UI and in this document.

---

## Quick start

Requirements: **Node.js ≥ 20** (developed on 22) and npm. No database, no Python, no external service
required to start.

```bash
npm install          # install dependencies
npm run dev          # start backend (:4317) + desktop UI (:4318)
```

Open <http://localhost:4318>. The UI talks to the backend through Vite's `/api` proxy, so no CORS
configuration is needed in development.

Production-style run (single process, serves the built UI and the API):

```bash
npm run build        # type-check, build the UI, compile the server
npm start            # http://localhost:4317
```

Verification commands:

```bash
npm test             # 126 unit + integration tests (vitest, jsdom + node)
npm run typecheck    # strict TypeScript across client, server and shared types
npm run build        # production build (also type-checks)
npm run smoke        # boots the real backend and runs 50 end-to-end checks, incl. a real training run
```

---

## What works today

| Area | Status | Notes |
| --- | --- | --- |
| Specialist registry & workspaces | ✅ implemented | 5 built-in specialists + user-created ones; each gets its own tab, workspace, model versions, datasets and history |
| Unified routing | ✅ implemented | Auto-routing by intent, or pin a specialist; the reply always names the mechanism that answered it |
| Real training (in-process) | ✅ implemented | Softmax task head with SGD, BM25 retrieval index with tuning, speech-lexicon induction — 3 usable methods |
| Training Lab | ✅ implemented | Run configuration, live progress over SSE, per-epoch checkpoints, run history, version management |
| Benchmarks & promotion gate | ✅ implemented | Held-out task suites per capability, before/after comparisons, regression detection, keep-the-better-version |
| Model versions | ✅ implemented | Artefacts on disk, `active`/`candidate`/`rejected`/`superseded`, manual promote/reject, verification re-run |
| Specialist test bench | ✅ implemented | Run one request through a specialist, see the predicted label, confidence, tool trace, and save the input as training data |
| Model providers | ✅ implemented | Built-in offline engine (always available), Ollama adapter, OpenAI-compatible adapter |
| Real LLM inference | ⚠️ needs configuration | Requires Ollama running locally or an API key — the app says so until then |
| LLM adapter fine-tuning (LoRA/PEFT) | ❌ not available here | Declared, explained, refused with `409 METHOD_UNAVAILABLE`; needs a Python toolchain + a base checkpoint |
| Tools & permissions | ✅ implemented | 8 tools, 4 permission gates, path sandbox, structured input validation |
| Speech | ◐ partial | Browser speech-to-text/synthesis are wired and working; server-side engines are interface-ready stubs |
| Persistent memory | ✅ implemented | Notes stored in `data/memory.json`, searchable, plus `memory.save` / `memory.write` tools |
| Settings persistence | ✅ implemented | Atomic JSON writes, validated on load, secrets never sent to the browser |
| Activity log | ✅ implemented | Ring buffer + live server-sent-events stream, secret redaction, level filtering |
| Desktop shell (native window) | ❌ not implemented | Roadmap item — the app runs as a local web application today |

### Interface

- **Overview** — what is trained, what is measured, what the platform can actually do right now, plus
  a runtime honesty panel (provider reachability, engine availability, tool permissions).
- **AI Assistant** — one composer for the whole system, with auto-routing or a pinned specialist, a
  step-by-step execution trace, dictation and read-aloud.
- **Specialist workspaces** — one tab per registered specialist: model and version, test bench,
  training data, training history, evaluation results, tools and settings, all tailored to the kind
  of specialist it is.
- **Add AI** — pick a template, choose the tools it may call, see which training methods actually
  apply to that category.
- **Training Lab** — configure a run (specialist → method → dataset), watch live progress, inspect
  the resulting metrics and checkpoint trail, manage model versions.
- **Benchmarks** — run a suite, compare two scorecards metric by metric, read the regression and
  trade-off report, see the untrained baseline next to the trained result.
- **Activity** — live event stream with level counters, text filter and follow mode.
- **Settings** — providers and credentials, permission switches, routing and training policy,
  appearance (motion, glass intensity, ambient glow), plus the inventory of model versions and
  registered capabilities.

All controls are functional or visibly disabled with an explanatory tooltip. Nothing is a decoration.

---

## How training actually works

Different kinds of assistant need different kinds of learning, so AgentOS ships four method
definitions and only claims the three that genuinely run in-process:

| Method | Kind | Applies to | What it really does |
| --- | --- | --- | --- |
| `task_head_sgd` | gradient | coding, general, research, custom | Real mini-batch SGD on a linear softmax head over hashed character/word n-grams: cross-entropy loss, momentum, balanced class weights, L2 decay, early stopping on a held-out split, calibrated decision threshold |
| `retrieval_index` | index | knowledge, research | Builds a real BM25 index over a document corpus and tunes its parameters against held-out queries |
| `lexicon_induction` | statistical | voice | Induces a pronunciation lexicon from aligned abbreviation → spoken-form examples with their contexts |
| `llm_adapter_finetune` | external | any | Declared for completeness: it needs Python + torch/transformers/peft and a base checkpoint. In this environment it reports `available: false` and the API refuses to start a run |

A training run is a real job with real output:

1. the dataset is loaded and split (train / held-out validation);
2. the method runs, emitting progress and per-epoch checkpoints over SSE;
3. an artefact is written to `data/models/<specialist>/<version>.json` and registered as a version;
4. the candidate is **re-measured** on its held-out benchmark suite;
5. `decidePromotion()` compares the new scorecard with the baseline (the previous version's card, or
   the `@untrained` card when there is none) and either promotes, keeps the active version, or files
   the candidate for review — the run record carries the reason either way.

The honest summary the UI repeats everywhere: a task head is a small linear classifier, **not**
fine-tuning of a language model. It improves routing and tool selection (which tool or strategy
answers an instruction) and it answers those decisions locally instead of escalating. Free-form prose
that no trained head covers still escalates to a provider — or to the labelled offline engine.

Measured on a fresh data directory (`npm run smoke`, and the probe runs in `docs/ACCEPTANCE.md`):

| Suite | Untrained baseline | After training |
| --- | --- | --- |
| Coding · decision routing | 52.2% | 83.3% |
| General · tool selection | 40.0% | 66.7% |
| Research · decision routing | 50.0% | 81.3% |
| Voice · speech normalisation | 15.4% | 92.3% |
| Knowledge · note retrieval | 0.0% | 100.0% |

### The promotion gate, in one sentence

A version is only promoted when its quality score improves on the baseline by at least
`training.minImprovement` (default 1 percentage point) in a re-measured benchmark; the comparison
verdict follows quality, while latency, CPU and heap costs are reported as named **trade-offs** rather
than being called regressions. Candidates are always kept: nothing is deleted, and nothing is
silently replaced.

---

## Architecture

```
shared/types.ts          # single source of truth for API/domain types (client + server)

server/
  index.ts               # process entry: HTTP listener, startup diagnostics, graceful shutdown
  app.ts                 # express app: CORS allow-list, request logging, static UI serving
  logger.ts              # levelled, bounded log buffer + SSE fan-out + secret redaction
  settingsStore.ts       # validated, atomic persistence of user settings
  providers/             # model provider adapters behind one interface
  models/
    catalog.ts           # merges provider declarations with live availability
    versions.ts          # model version store: artefacts on disk + version index + promotion
  ml/                    # the learning primitives, all deterministic and unit-tested
    rng.ts features.ts   # seeded RNG, hashed n-gram feature extraction
    softmax.ts           # mini-batch SGD softmax head with early stopping
    retrieval.ts         # BM25 index + held-out evaluation
    lexicon.ts calibration.ts
  training/
    corpora.ts datasets.ts  # generated corpora and the 8 trainable datasets
    methods.ts           # the 4 method definitions (spec + run function)
    runs.ts engine.ts    # run store, checkpoints, the training job pipeline
  specialists/
    store.ts registry.ts # specialist definitions, state computation (ready/training/degraded/untrained)
    inference.ts policy.ts  # versioned inference with escalation, rule policy, speech normalisation
  benchmarks/
    suites.ts store.ts runner.ts  # held-out task suites, scorecard store, real task runner
  capabilities/registry.ts  # capability definitions + keyword router
  tools/                 # tool contract, registry, permission gates, path sandbox, builtins
  memory/                # persistent knowledge store + memory tools
  speech/engines.ts      # interchangeable STT/TTS engine descriptors
  routes/api.ts          # core REST API + SSE, zod-validated payloads, error mapping
  routes/platform.ts     # specialists, training, versions, benchmarks, platform overview

src/                     # React desktop UI (React + Tailwind, no UI kit)
  state/SystemContext.tsx  # one live snapshot of system state + appearance, defensive against bad payloads
  lib/api.ts               # typed API client with explicit network/timeout errors
  components/              # design-system primitives + glass shell (sidebar, top bar)
  views/                   # Overview, Assistant, SpecialistWorkspace, AddAI, TrainingLab,
                           # Benchmarks, Activity, Settings (+ Models/Capabilities panels)
```

Deep dive: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Requirement-by-requirement verification: [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md).

### A request, step by step

1. `POST /api/chat` is validated (zod) and handed to the orchestrator.
2. The **router** picks a specialist: pinned by the user, or scored by intent keywords, or left to
   the configured default when `routing.autoRoute` is off.
3. The specialist's **own model decides the action** if it has a trained version and the prediction
   clears its calibrated threshold; otherwise the deterministic rule policy answers (when
   `routing.allowRuleFallback` is on), and otherwise the request **escalates** to a provider.
4. Planned **tools** are validated, permission-checked and executed inside a sandbox. Failures become
   trace entries, never crashes.
5. Relevant **memory** records are recalled and injected as grounding context.
6. The reply, its metadata (specialist, mechanism, version, confidence, degraded flag, tool outcomes,
   duration) and the full trace are stored in the conversation and returned to the UI.

Every reply states which of the four mechanisms answered it: a trained local model, the rule policy,
an external provider, or the offline engine.

### Extension points

- **New model provider** — implement `ModelProvider` and register it in `server/providers/registry.ts`.
- **New tool** — implement `ToolDefinition` (declare `requires`, `inputSchema`, `run`) and add it to
  `BUILTIN_TOOLS`. It appears in the UI with its permission badge automatically.
- **New training method** — add a `TrainingMethodDefinition` to `server/training/methods.ts` with its
  `datasetKinds`, `runtime` and `run` function; it appears in the Training Lab with its honest
  availability.
- **New dataset** — declare a `DatasetInfo` in `server/training/datasets.ts` and a generator in
  `server/training/corpora.ts`.
- **New benchmark suite** — add a suite to `server/benchmarks/suites.ts` with its tasks and metrics;
  `DATASET_SUITE` maps datasets to the suite that measures them.
- **New specialist template** — add a `SpecialistDefinition` to `server/specialists/store.ts`; a
  workspace, tab and training options follow automatically.

---

## Configuration

Copy [`.env.example`](.env.example) to `.env` (git-ignored) to override defaults:

| Variable | Default | Purpose |
| --- | --- | --- |
| `AGENTOS_PORT` | `4317` | API port |
| `AGENTOS_HOST` | `0.0.0.0` | API bind address |
| `AGENTOS_DATA_DIR` | `./data` | Settings, memory, datasets, models, runs and scorecards |
| `AGENTOS_WORKSPACE_DIR` | process cwd | Root that filesystem tools are confined to |
| `AGENTOS_ALLOWED_ORIGINS` | *(empty)* | Extra browser origins allowed to call the API |
| `AGENTOS_LOG_LEVEL` | `info` | Startup log level (then taken from settings) |

Persisted state (all under `AGENTOS_DATA_DIR`, `./data` by default):

- `settings.json` — validated settings, written atomically (temp file + rename).
- `memory.json`, `notes/*.md` — knowledge-store records and notes.
- `specialists/specialists.json` — the registry, including specialists you created.
- `datasets/<specialistId>.examples.jsonl` — examples captured from the test bench.
- `models/<specialistId>/index.json` + `<version>.json` — version metadata and the learned artefact.
- `training/runs.json`, `training/<runId>/checkpoints/*.json` — run history and checkpoint trail.
- `benchmarks/scorecards.json` — every recorded measurement, including untrained baselines.

### Enabling real models

**Local (Ollama).** Install Ollama, run `ollama pull llama3.2`, keep the default base URL
(`http://127.0.0.1:11434`) in *Settings → Model providers*. The catalogue picks up pulled models on
the next probe.

**API (OpenAI-compatible).** Paste a key and optional base URL in *Settings → Model providers*.
The key is stored server-side in `settings.json`, is never returned to the browser, and is redacted
in logs. Works with OpenAI, Azure-compatible gateways, LM Studio, vLLM, etc.

**Nothing configured.** Chat keeps working: a trained head or retrieval index answers the decisions it
learned, and everything else reaches the built-in deterministic engine. Those replies are clearly
labelled and the UI marks them as degraded — it never pretends a model answered.

### Enabling adapter fine-tuning

`llm_adapter_finetune` is implemented as a *declaration plus refusal*: the method card explains what it
would need (Python 3.11 with torch/transformers/peft, a local base checkpoint, a provider runtime) and
the API answers `409 METHOD_UNAVAILABLE` when asked to run it here. Wiring it up to a machine that has
that toolchain is an extension of `run` in `server/training/methods.ts`, not a change to the rest of
the platform — the run pipeline, promotion gate and benchmark trail are method-agnostic.

---

## Security model

- Tools declare required permissions (`read`, `write`, `network`, `execute`); every switch defaults
  to **off** except read access to the workspace.
- Filesystem tools resolve paths inside `AGENTOS_WORKSPACE_DIR` / `AGENTOS_DATA_DIR` and reject
  `..`, absolute paths and symlink escapes (`server/tools/paths.ts`).
- No shell tool exists in this build. The shell permission is exposed but explicitly marked as having
  no effect yet — unchecked capability is not silently granted.
- `http.fetch` is GET-only, time-bounded, size-capped and requires the network permission.
- Credentials live only in the server-side settings file; API responses replace keys with a
  `hasApiKey` flag, and the logger redacts any metadata field matching `key|token|secret|password`.
- Training data never leaves the machine: corpora are generated locally, captured examples are stored
  in the data directory, and no training path calls an external service.
- Incoming payloads are validated with zod; unknown fields are rejected.
- The browser origin allow-list accepts localhost, `*.e2b.app` preview hosts and explicitly
  configured origins.

---

## Limitations (explicitly not done)

1. **Task heads are not language models.** `task_head_sgd` is genuine gradient descent on a small
   linear classifier over hashed n-grams. It changes routing and tool selection; it does not rewrite
   prose and it is not fine-tuning. Deep fine-tuning is the declared external method.
2. **No native desktop window.** AgentOS runs as a local web application; packaging with Tauri/Electron
   is a later milestone.
3. **Server-side speech engines are stubs.** Whisper/Piper adapters return `available: false`; the
   working speech path is the browser Web Speech API. The Voice specialist's lexicon induction is real
   and improves text normalisation, but it is not a speech-synthesis model.
4. **A trained head only covers the decisions in its dataset.** Instructions outside its label set fall
   back to the rule policy or escalate, and the reply says so.
5. **Conversations are in memory only.** They reset on restart; settings, memory, datasets, model
   versions and scorecards are persistent.
6. **No authentication.** The API is designed for a local, single-user machine. Do not expose the port
   to untrusted networks.
7. **One training run at a time per specialist.** Runs are sequential in-process; cancellation is
   supported and leaves no partial version behind.

## Roadmap

1. Adapter fine-tuning behind the declared external runtime (LoRA/PEFT), with the same promotion gate.
2. Additional method families per specialist kind (sequence labelling for extraction, embeddings for
   retrieval, acoustic-model adapters for voice).
3. Conversation persistence + export, and exporting datasets in training-ready formats.
4. Server-side speech engines (Whisper/Piper) behind the existing interface.
5. Desktop packaging (Tauri) with OS-level permission prompts.

## License

Unlicensed prototype — add a license before distributing.
