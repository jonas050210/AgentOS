# Acceptance / Quality Check

Mapping of the AgentOS brief (v0.2.0: specialists, real training, benchmarks, unified intelligence) to
the implementation, with the evidence used to verify each item. Commands were run in this repository
(`npm test`, `npm run typecheck`, `npm run build`, `npm run smoke`), plus live probe scripts against a
real backend on a temporary data directory.

## A. Completion criteria

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Installable and startable via documented instructions | ✅ | `README.md` quick start; `npm install && npm run dev` verified (backend `:4317`, UI `:4318`); `npm run build && npm start` verified serving UI + API on one port |
| 2 | Interface and core application flow work | ✅ | All 8 navigation entries implemented and wired to live data; `src/App.test.tsx` walks every view; the dev server was exercised against a live backend with trained artefacts (live probe, §G) |
| 3 | Orchestration, model-management and capability interfaces implemented | ✅ | `server/orchestration/orchestrator.ts`, `server/providers/*`, `server/models/{catalog,versions}.ts`, `server/specialists/*`, `server/training/*`, `server/benchmarks/*`, `server/tools/*`, `server/speech/engines.ts` |
| 4 | Status indicators reflect actual system states | ✅ | Health probes providers at request time; specialist state is computed from version/run/permission facts (`ready`/`training`/`degraded`/`untrained`/`disabled`); unreachable providers render `unreachable`/`unconfigured`; every reply names the mechanism that answered |
| 5 | Settings persist across restarts | ✅ | Atomic write in `SettingsStore.update()`; `server/settingsStore.test.ts`, `server/routes/api.test.ts` (on-disk assertion + new store instance), smoke check reads `settings.json` |
| 6 | Critical failure cases handled and tested | ✅ | Permission denial, path traversal, invalid tool input, unknown tool, provider outage, provider that answers, provider that returns an empty completion, unreachable backend, corrupt settings file, malformed payloads, unknown specialist, empty dataset, incompatible method, external method, suite mismatch — each has a dedicated test |
| 7 | Automated tests and build checks pass | ✅ | 143 tests passing across 16 files; `tsc` clean for client and server configs; `npm run build` clean; `npm run smoke` → **50/50 checks including a real training run** |
| 8 | Documentation accurate about features, setup and limitations | ✅ | `README.md` (status table, training section, promotion gate, limitations, roadmap), `docs/ARCHITECTURE.md`, this file |

## B. Product & interface requirements

| Requirement | Status | Where |
| --- | --- | --- |
| Premium visual language: translucent surfaces, soft reflections, gradients, spacing, restrained motion | ✅ | `tailwind.config.js` design tokens; `src/index.css` glass system (`glass`, `glass-subtle`, `reflective`, `lift`, aurora/noise layers, `nav-rail`, skeletons, meters) |
| Motion respects the user's preference | ✅ | Global `prefers-reduced-motion` block plus the explicit `[data-motion='off']` override driven by `appearance.reduceMotion`; `SystemContext` applies motion/glow/glass to `<html>` |
| Left navigation for Overview, Assistant, specialists, Add AI, Training Lab, Benchmarks, Activity, Settings | ✅ | `src/components/Sidebar.tsx` (specialists rendered from the registry, grouped sections, `aria-current`), `src/App.tsx` view switch |
| Every specialist has a tab and dedicated workspace | ✅ | `src/views/SpecialistWorkspaceView.tsx` (Overview / Test bench / Training & versions / Settings tabs, tailored per category); `#/specialist/<id>` deep links |
| Central assistant with routing or explicit selection | ✅ | `src/views/AssistantView.tsx`: auto-route or pin a specialist, per-reply mechanism badge, trace panel, escalation chain explanation |
| Specialists stay individually configurable and testable | ✅ | `POST /api/specialists/:id/test`, `PATCH /api/specialists/:id`, per-specialist tool lists, thresholds, prompts; `src/views/AddAIView.tsx` for creation |
| Consistent, reusable components and proper error states | ✅ | `src/components/ui.tsx` primitives reused across all views; `ErrorBanner`, `EmptyState`, `ErrorBoundary` in `App.tsx`; `HiddenDescription` for screen-reader-only context |
| Every visible control works or is clearly unavailable | ✅ | Disabled controls carry reasons (external method card, blocked tools, unconfigured engines, shell permission with no registered tool); `Badge title` explains why |

## C. Specialists, training and improvement

| Requirement | Status | Where / Evidence |
| --- | --- | --- |
| Training is a real capability, not a dashboard | ✅ | `server/training/engine.ts` runs real jobs; artefacts on disk (`data/models/<specialist>/<version>.json`) contain learned weights; smoke asserts `weights.length > 0 && trainingSize > 0` |
| Different AI types use genuinely suitable methods | ✅ | `task_head_sgd` (gradient) for coding/general/research/custom, `retrieval_index` (BM25 + tuning) for knowledge/research, `lexicon_induction` (statistical) for voice; `METHOD_NOT_APPLICABLE` (400) for a method that does not fit |
| Nothing is claimed that cannot run | ✅ | `llm_adapter_finetune` reports `available: false`, `runtime: 'external'`, lists its requirements, and the API refuses with `409 METHOD_UNAVAILABLE`; the Training Lab shows it as unavailable |
| Central Training Lab: configure, monitor, inspect, manage | ✅ | `src/views/TrainingLabView.tsx` + `server/routes/platform.ts`: method/dataset catalogue, run configuration, SSE progress, checkpoint trail, run history, version table with promote/reject |
| Each specialist has its own training history and evaluation results | ✅ | `runStore.list({specialistId})`, `versionStore.list(specialistId)`, `benchmarkStore.list({specialistId})`; the workspace shows all three plus its datasets |
| Progress is honest | ✅ | `progress` is `min(0.9, 0.05 + 0.8·epoch/expected)`; a run reports `completed` only after the artefact is stored and the benchmark has run |
| Checkpoints are real | ✅ | `training/<runId>/checkpoints/*.json` written every `checkpointEvery` epochs with loss/accuracy and exposed through `GET /api/training/runs/:id` |
| Training data comes from real sources | ✅ | Generated corpora (seeded from the workspace's own knowledge) + examples captured from the test bench (`datasets/<specialistId>.examples.jsonl`) |

## D. Benchmarks, promotion and models

| Requirement | Status | Where / Evidence |
| --- | --- | --- |
| Objective before/after testing | ✅ | `server/benchmarks/runner.ts` runs held-out suites per specialist category, captures an untrained baseline (`versionId: <specialistId>@untrained`) before training, and re-measures after |
| Measurable quality, task success, speed, reliability, resource usage | ✅ | Scorecard fields: `quality`, `taskSuccessRate`, `tasksPassed/tasksTotal`, `latencyMsP50/P95/Mean`, `reliability`, `failures`, `cpuMs`, `heapDeltaMb`, plus per-task results |
| Detect improvements and regressions | ✅ | `benchmarkStore.compare()` rows with thresholds (quality 0.02 absolute, latency +25%, resource +50%); verdict follows quality; latency/CPU/heap costs are named trade-offs |
| Keep better versions instead of blindly replacing | ✅ | `decidePromotion()`; promotion only with `delta ≥ training.minImprovement` against the best available baseline; `kept_active`/`candidate`/`rejected` outcomes retain the candidate and its evidence; only one version is active per specialist (asserted in tests and smoke) |
| Manual review possible | ✅ | `POST /api/models/versions/:id/promote` (with optional verification re-run) and `/reject`; `verify: true` re-measures before switching |
| Every number is traceable | ✅ | Scorecards keep per-task results (capped at 150), engine string, thresholds and timestamps; the Benchmarks view shows the comparison table, the trade-offs and the task-by-task list |

## E. Reliability & security requirements

| Requirement | Status | Where / Test |
| --- | --- | --- |
| Persist user settings, datasets, versions, runs and scorecards | ✅ | `settingsStore.test.ts`, `api.test.ts`, `platform.test.ts`, smoke (artefact + scorecard on disk) |
| Handle missing dependencies / unavailable services / network failure gracefully | ✅ | Provider probe failures, `http.fetch` failures, unreachable backend banner, provider-outage chat test, external training method refusal |
| Keep credentials out of source and logs | ✅ | `.gitignore` excludes `data/`, API returns `hasApiKey` only, `Logger.sanitize()` redacts, tests + smoke assert no key leakage |
| Permission checks on tools and desktop actions | ✅ | `assertAllowed()` per declared permission; registry test asserts denial and re-enable; defaults restrictive; blocked tools mark the specialist `degraded` |
| No uncontrolled command execution or unauthorized file modification | ✅ | No shell tool registered; filesystem tools confined by `resolveInside()`; traversal tests; training writes only inside `AGENTOS_DATA_DIR` |
| Maintainable, typed, few dependencies | ✅ | Strict TS across three configs, shared types, 5 runtime deps (express, cors, dotenv, zod, react) |

## F. Test inventory

| File | Tests | Focus |
| --- | --- | --- |
| `server/settingsStore.test.ts` | 6 | Persistence, corruption, validation, reset |
| `server/tools/registry.test.ts` | 13 | Tool execution, permissions, sandboxing, failures |
| `server/orchestration/orchestrator.test.ts` | 27 | Routing to specialists, tool planning, escalation, degradation, conversations, provider authorship/model reporting, memory grounding, empty-response handling |
| `server/routes/api.test.ts` | 18 | HTTP surface, validation, secrets, persistence, provider outages and authorship reporting |
| `server/routes/platform.test.ts` | 15 | Specialist CRUD, test bench, examples, normalisation, training runs, versions, benchmarks, overview, comparison baselines |
| `server/training/corpora.test.ts` | 5 | Corpus generation, label pools, deterministic splits |
| `server/ml/softmax.test.ts` | 5 | Real SGD: convergence, holdout generalisation, auto feature dimension, early stopping |
| `server/ml/calibration.test.ts` | 5 | Threshold sweeps, tie-breaking, escalation behaviour |
| `server/benchmarks/runner.test.ts` | 10 | Suite coverage, real measurement, regression detection, repeat runs, comparison semantics |
| `src/App.test.tsx` | 4 | Shell, all-view navigation, workspace deep link, offline mode |
| `src/views/SpecialistWorkspaceView.test.tsx` | 6 | Trained workspace, dataset facts, test bench, provider authorship, untrained honesty, unknown id |
| `src/views/TrainingLabView.test.tsx` | 5 | Method/dataset catalogue, POST payload, unavailable method, run list |
| `src/views/BenchmarksView.test.tsx` | 7 | Scorecards + baseline chip, comparison/trade-off, running a suite, overview honesty |
| `src/views/ChatView.test.tsx` | 9 | Routed replies + trace, structured errors, validation errors, provider-unreachable labelling |
| `src/views/SettingsView.test.tsx` | 4 | Settings tabs, PUT payloads, secret masking, offline state |
| `src/components/ui.test.tsx` | 4 | Design-system primitives |
| **Total** | **143** | 16 files (`npx vitest run`) |
| `scripts/smoke.mjs` | 50 checks | Live boot + end-to-end API verification, including a real training run with artefact inspection |

## G. Live verification (real backend, temporary data directory)

A probe script created five specialists' worth of data on a fresh `AGENTOS_DATA_DIR` and asserted the
resulting files, not just HTTP status codes:

- Training: coding-ai (120 epochs, holdout 81.1%, calibrated threshold 0.40, benchmark 83.3% →
  promoted), general-ai (holdout 63.3%, benchmark 66.7% → promoted), research-ai (holdout 87.5%,
  benchmark 81.3% → promoted), knowledge-ai (recall@5 1.0, benchmark 100% → promoted), voice-ai
  (exact match 1.0, benchmark 92.3% → promoted).
- Before/after pairs, each measured on the *same* suite before and after training: coding
  52.2% → 83.3%, general 40.0% → 66.7%, research 50.0% → 81.3%, voice 15.4% → 92.3%,
  knowledge 0.0% → 100.0% (10 scorecards: 5 untrained baselines + 5 trained versions).
- A candidate that does not beat its baseline is *not* promoted: research-ai's head measured
  identically to the rule policy on the extraction suite, so the run was filed as a candidate and
  the specialist's state said so instead of claiming an improvement.
- Honesty: the failing run (`coding.user_examples` with a single label) reported
  *"Routing training needs at least two distinct decision labels."* and created **no** version.
- Routing: `what is 128 * 4` → `calc.evaluate` at confidence 0.9473, answered by the trained head;
  prose requests that no head covers fell back to the labelled offline engine.
- Voice: `The API responds in 250 ms for $15 — see README.md.` →
  `The A P I responds in two hundred fifty milliseconds for fifteen dollars — see README.md.`
- Versions: `docs-ai` was created untrained (no fake score), reported
  `rule planner (built-in) — Docs AI has no trained version` when tested, and every promoted
  specialist kept exactly one active version with its earlier cards intact.

The desktop shell was additionally rendered against that live backend (jsdom + real HTTP) across all
eight views: Overview showed the trained/untrained split, Training Lab showed 5 completed + 1 failed
run, Benchmarks showed the scorecards with `improved` badges and the trade-off note, and the Coding
workspace showed `Best measured quality 81.1% · rule baseline 52.2%`.

## H. Known gaps / not verified

- No automated visual-regression or browser end-to-end test: no headless browser is available in the
  build environment. UI behaviour is covered by jsdom component tests plus the live render probe above.
- Ollama and OpenAI adapters are exercised through their interfaces and error paths but were not
  driven against a live third-party service here.
- `llm_adapter_finetune` was never executed: it is a declared external method, refused by design in
  this environment. Nothing in the product presents it as available.
- Corpora are generated from the repository's own content, so the reported accuracy is measured on
  those held-out splits — it is an honest before/after for *this* data, not a claim about arbitrary
  user traffic.
- Conversations are intentionally in-memory: chat-history persistence is out of scope for this
  milestone and listed in the README roadmap.

## I. Post-merge reliability fix: accurate provider reporting

The first full run after the v0.2.0 merge reported 123 passing / 4 failing tests in
`server/orchestration/orchestrator.test.ts` (three of them 5 s vitest timeouts plus one wrong reply).
Both causes were reproduced and fixed:

| # | Root cause | Evidence | Fix |
| --- | --- | --- | --- |
| 1 | `runSpecialist` set `answeredBy: 'offline-engine'` for *every* successful escalation, so an answer from a real provider was reported as the degraded offline engine, and the orchestrator's trace claimed *"No external provider available"* while the LLM's text was in the reply | A stub Ollama server on `127.0.0.1:11434` answering `Hello! I'm AgentOS Concierge. How can I assist you today?` reproduced the reported failure exactly (`expected 'Hello! I'm AgentOS Concierge. How ca…' to match /offline engine/i`) on the unmodified merge commit | `escalate()` now returns the provider that answered (`providerId`, `providerName`, `model`); `answeredBy` is `offline-engine` only for the built-in engine and `provider` otherwise |
| 2 | Every escalated turn probed provider availability twice (once to escalate, once purely for reporting). Against a port that accepts and never answers, each probe costs 1.5 s, so an idle turn cost ~3 s and two-turn tests exceeded the 5 s test timeout under load | Measured with a silent TCP listener: pre-fix 2 probes / 3010 ms per turn, post-fix 1 probe / 1507 ms | The orchestrator reports from the specialist result instead of probing again; the tests stub provider availability, so no suite depends on the machine's network (verified: the full suite passes, with 0 provider calls, while a live LLM listens on `127.0.0.1:11434`) |
| 3 | The orchestrator derived `providerId`/`model` from settings rather than from the response, the offline engine was handed Ollama's model name, and an empty completion would have been stored as a blank reply | New regression tests assert `builtin` / `offline-reasoner` for the fallback, `ollama` / `llama3.2` for a provider, and a graceful failure for an empty completion | Model name is passed only to the provider that is called; empty completions raise a provider error; `escalationReason` reports a failed tool instead of `null` |
| 4 | `summarizeToolOutput` read `records[].content` from `memory.search`, which answers with `results[].snippet`: every successful note lookup was answered with *"No stored note matched."* while the trace reported the tool as executed. `toolInputFor('memory.write')` also sent `content` where the tool requires `body`, so that branch could never pass validation | Live turn `what did we save about the release train?` answered "No stored note matched." with a `tool:memory.search` step in the trace; both mismatches are covered by new tests | The summarizer reads the tool's real output shape (and the legacy one), names where a `memory.write` note was stored, and the planner builds the schema the `memory.write` tool declares |

Test isolation: `server/test-suite.setup.ts` gives every run a throwaway `AGENTOS_DATA_DIR`, so the
import-time stores never read or write the working tree's `data/` directory (verified by running the full
suite with a hostile `data/settings.json` and `data/memory.json` present — 141/141 passed and the files
were left untouched).

Contract now asserted by tests (server and HTTP layer): `answeredBy: 'provider'` ⟺
`degraded: false` and a trace step naming that provider; `answeredBy: 'offline-engine'` ⟺
`providerId: 'builtin'`, `degraded: true` and a warning trace step; a failed provider ⟹
`providerError` set, `model: null`, error trace steps and no authorship claim.
