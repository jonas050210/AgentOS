# AgentOS architecture

This document describes the modules, the contracts between them, and how to extend the system
without rewriting it. It reflects the code as implemented (v0.2.0 — specialists, real training,
benchmarks, unified routing).

## 1. Design principles

1. **One interface per replaceable part.** Providers, tools, capabilities, speech engines, training
   methods and benchmark suites are described by small contracts; the orchestrator only knows those
   contracts.
2. **Honest status.** Availability is probed at runtime. A component that cannot run reports
   `unavailable`/`unconfigured`, an untrained specialist reports `untrained` with no score, and the
   UI shows exactly that.
3. **Measure before claiming.** A version is promoted only by a re-measured benchmark against the
   previous version or the untrained baseline; the promotion record carries the reason either way.
4. **Least surprise on failure.** Every external call has a timeout; every failure produces a
   structured, user-readable error instead of an exception escaping to the client.
5. **Typed end to end.** `shared/types.ts` is imported by both processes; TypeScript runs in strict
   mode with `noUnusedLocals`/`noUnusedParameters`.
6. **Minimal dependencies.** Express, zod, React, Tailwind, Vite, Vitest. No state-management
   library, no UI kit, no ML framework — the learning primitives are implemented and unit-tested here.

## 2. Module map

### Shared
| File | Responsibility |
| --- | --- |
| `shared/types.ts` | The whole domain contract: `Settings`, `ModelInfo`, `CapabilityInfo`, `ToolInfo`, `AgentChatResponse`, `ActivityEntry`, `SpecialistDefinition`/`SpecialistSummary`, `TrainingMethodInfo`/`TrainingMethodDefinition`, `TrainingRun`, `ModelVersionSummary`, `BenchmarkSuiteInfo`/`BenchmarkScorecard`/`BenchmarkComparison`, `SpecialistTestResult`, and `DEFAULT_SETTINGS`. |

### Backend
| Module | Files | Responsibility |
| --- | --- | --- |
| Logging | `server/logger.ts` | Levelled, bounded (500) ring buffer, SSE fan-out, metadata redaction, console mirroring. |
| Settings | `server/settingsStore.ts` | `validate()` guards every read *and* write; atomic temp-file + rename persistence. Holds `appearance`, `routing` and `training` policy. |
| Providers | `server/providers/*` | `ModelProvider` contract; `EchoProvider`, `OllamaProvider`, `OpenAIProvider`; registry with availability probing and preference order `openai → ollama → builtin`. |
| Model catalog | `server/models/catalog.ts` | Merges declared models with live availability; declares *expected* models for unreachable providers. |
| Model versions | `server/models/versions.ts` | `versionStore`: creates versions, writes the learned artefact to `models/<specialist>/<version>.json`, tracks `active`/`candidate`/`rejected`/`superseded`, `promote()` (demotes the previous active version), `remove()`, `totalBytes()`. |
| ML primitives | `server/ml/*` | `rng.ts` (seeded RNG), `features.ts` (hashed n-gram features + `autoFeatureDim`), `softmax.ts` (mini-batch SGD with momentum, L2, class weights, early stopping), `retrieval.ts` (BM25 index, `evaluateRetrieval`), `lexicon.ts` (lexicon induction), `calibration.ts` (decision-threshold sweep). |
| Training | `server/training/*` | `corpora.ts` (seeded corpora + `splitPool`), `datasets.ts` (8 datasets, user examples, `withValidation`), `methods.ts` (4 method definitions with `datasetKinds`, `runtime`, `run`), `runs.ts` (run store, checkpoints, cancellation), `engine.ts` (`startTraining` → artefact → benchmark → `decidePromotion`). |
| Specialists | `server/specialists/*` | `store.ts` (definitions, registry, state computation), `registry.ts` (`SpecialistSummary` with `state`/`stateDetail`), `inference.ts` (`runSpecialist`, `normalizeSpeechFor`, versioned inference + escalation), `policy.ts` (rule planner, intent keywords, confidence). |
| Benchmarks | `server/benchmarks/*` | `suites.ts` (8 deterministic task suites + `DATASET_SUITE`), `store.ts` (scorecards, `compare()` semantics, `findBaseline()`), `runner.ts` (runs tasks through the real specialist, records quality/latency/CPU/heap/per-task results). |
| Capabilities | `server/capabilities/registry.ts` | Capability definitions (system prompt, bound tools, routing keywords) and status computation. |
| Tools | `server/tools/*` | `ToolDefinition` contract, registry with permission enforcement, `validateInput()`, structured outcomes, path sandbox. |
| Memory | `server/memory/*` | JSON-backed knowledge store + `memory.search` / `memory.save` tools. |
| Orchestration | `server/orchestration/*` | `handleChat()` pipeline (route → conversation → memory → decide → tools → escalate/generate → trace), bounded `ConversationStore`. |
| Speech | `server/speech/engines.ts` | `SpeechProvider` contract + status reporting for browser and (unconfigured) server engines. |
| API | `server/routes/api.ts`, `server/routes/platform.ts`, `server/routes/helpers.ts`, `server/app.ts` | REST + SSE surface, zod validation, `ApiError` mapping, CORS allow-list, static asset serving. |

### Frontend
| Module | Files | Responsibility |
| --- | --- | --- |
| State | `src/state/SystemContext.tsx` | Loads one consistent snapshot (health, providers, models, capabilities, tools, settings, activity) *plus* the platform snapshot (specialists, runs, counts, scorecards, versions, methods, datasets, suites), subscribes to `/api/training/stream` and `/api/activity/stream`, and applies `appearance` to `<html>` as `data-motion`/`data-glow`/`data-glass`. Shape-checks every payload. |
| API client | `src/lib/api.ts` | Typed calls for core and platform routes, 30 s timeout, `ApiError` with `status`/`code`, explicit network-failure message, `trainingStreamUrl()`. |
| Design system | `src/components/ui.tsx`, `src/index.css`, `tailwind.config.js` | Glass surfaces, rails, meter bars, delta bars, sparklines, chips, skeletons, motion tokens; `specialistTone`/`versionTone` map domain state to colour. |
| Shell | `src/components/Sidebar.tsx`, `TopBar.tsx`, `src/App.tsx` | Navigation (registry-driven specialist list, `aria-current`), live status indicators, hash deep links (`#/specialist/<id>`), error boundary, footer status bar. |
| Views | `src/views/*` | Overview, Assistant, SpecialistWorkspace, AddAI, TrainingLab, Benchmarks, Activity, Settings (whose inventory tab embeds the Models and Capabilities panels). |

## 3. Contracts

```ts
// server/providers/types.ts
interface ModelProvider {
  readonly id: string; readonly name: string; readonly type: 'local' | 'cloud' | 'remote';
  info(): ProviderInfo;
  isAvailable(): Promise<boolean>;
  listModels(): Promise<ModelInfo[]>;
  generate(req: GenerateRequest): Promise<GenerateResponse>;
}

// server/tools/types.ts
interface ToolDefinition<Input, Output> {
  id: string; name: string; description: string; category: string;
  requires: Permission[];                 // read | write | network | execute
  inputSchema: Record<string, unknown>;   // JSON-schema-ish, drives validation + UI
  run(input: Input, ctx: ToolContext): Promise<Output>;
}

// server/training/methods.ts
interface TrainingMethodDefinition extends TrainingMethodInfo {
  run(ctx: TrainingContext): Promise<TrainingMethodResult>;
}
// TrainingMethodInfo carries: id, name, kind ('gradient' | 'statistical' | 'index' | 'external'),
// runtime ('in-process' | 'external'), available, description, artifactKind, requirements[],
// appliesTo[] (specialist categories), datasetKinds[], hyperparameters[], honesty — plus `run`.

// server/training/engine.ts
startTraining(request: TrainingRunRequest): TrainingRun   // queued → running → completed|failed|cancelled
// TrainingMethodResult: { artifactKind, artifact, metrics, primaryMetric, baseModel? }

// server/specialists/inference.ts
runSpecialist(id: string, input: string, opts?): Promise<SpecialistRunResult>
// route: trained head (calibrated threshold) → rule policy (if allowRuleFallback) → provider escalation

// server/benchmarks/store.ts
compare(candidate: BenchmarkScorecard, baseline: BenchmarkScorecard | null): BenchmarkComparison
// verdict follows the quality score; latency/CPU/heap costs are reported as `tradeoffs`
```

## 4. The training pipeline

```
POST /api/training/runs {specialistId, methodId, datasetId, hyperparameters?, runBenchmarkAfter?, autoPromote?}
   │  zod validation → 400 VALIDATION_ERROR
   │  method/dataset compatibility → 400 METHOD_NOT_APPLICABLE | DATASET_INCOMPATIBLE
   │  external runtime unavailable → 409 METHOD_UNAVAILABLE
   ▼
startTraining()                                    (returns 202 with the run record immediately)
   ├─ dataset      load + split (seeded shuffle; every n-th example held out; 1/5 of the rest for validation)
   ├─ method.run   real computation with onEpoch / onCheckpoint / shouldStop callbacks
   │                 → progress = min(0.9, 0.05 + 0.8 · epoch/expected)
   │                 → training/<runId>/checkpoints/*.json every checkpointEvery epochs
   ├─ artefact     versionStore.create() writes models/<specialist>/<version>.json (real weights/index/lexicon)
   ├─ benchmark    runBenchmark() on the specialist's primary suite, versionId = the new version
   ├─ baseline     benchmarkStore.findBaseline() → previous card of the same version/suite, else the @untrained card
   ├─ decide       decidePromotion() → promoted | awaiting_review | kept_active (+ metric, delta, reason)
   │                 promoted only when the quality delta ≥ settings.training.minImprovement
   └─ finish       run record stores metrics, promotion, versionId, scorecardId, artifactPath
```

Failure never leaves a half-registered version: an error before `versionStore.create()` finishes the
run as `failed` with the message and no version; cancellation before that leaves nothing behind.

## 5. The benchmark pipeline

```
POST /api/benchmarks/run {suiteId, specialistId, versionId?, repeats?}
   │  cross-category suite → 400 SUITE_MISMATCH
   ▼
runBenchmark()
   ├─ suite        deterministic task list for the specialist's category
   ├─ per task     runSpecialist() with the requested version → predicted label / answer / error
   │                 repeats > 1 exercises consistency (same input, N runs)
   ├─ scorecard    quality, taskSuccessRate, tasksPassed/tasksTotal, latency p50/p95/mean,
   │                 cpuMs, heapDeltaMb, reliability, failures, per-task results (capped at 150)
   │                 + mechanism counts (headAnswered / policyAnswered / escalated)
   ├─ baseline     a run without a trained version measures the rule policy and is stored with
   │                 versionId = <specialistId>@untrained
   └─ store        benchmarkStore.save() → data/benchmarks/scorecards.json
```

`compare()` walks a fixed metric list, marks a row as a regression only when the change crosses its
threshold (quality 0.02 absolute, latency +25%, resource +50%), and derives the verdict from the
quality row — so a candidate that is 30 points better but 50× slower is reported as *improved with a
named trade-off*, not as a regression. Two cards of the same version are a repeat measurement and are
never called progress.

## 6. A chat turn, step by step

```
POST /api/chat {message, conversationId?, specialistId?}
   │  zod validation → 400 VALIDATION_ERROR
   ▼
handleChat()
   ├─ route          pinned specialist, else intent keywords, else the configured default
   ├─ conversation   existing id, else new conversation (bounded history)
   ├─ decide         trained head (calibrated threshold) → rule policy → escalate
   ├─ plan           planTools(): URL → http.fetch, "calculate …" → calc.evaluate,
   │                 file reference → workspace.read, "remember …" → memory.save, category fallbacks
   ├─ execute        ToolRegistry.execute(): schema validation → permission gate →
   │                 sandboxed run → structured outcome (never throws to the caller)
   ├─ recall         memory.search results injected as grounding context
   ├─ select         providerRegistry.preferred() probes availability in order
   ├─ generate       provider.generate() with the specialist's system prompt
   │                 failure → trace entry(status=error) + degraded answer (HTTP 200)
   └─ respond        {conversationId, message(+meta: answeredBy, versionId, confidence), trace[]}
```

Trace steps are surfaced verbatim in the Assistant view, which is how the UI stays honest about what
the system actually did.

## 7. Persistence

| Artefact | Location | Written | Loaded |
| --- | --- | --- | --- |
| Settings | `$AGENTOS_DATA_DIR/settings.json` | On every change (atomic rename) | At startup, validated |
| Memory | `$AGENTOS_DATA_DIR/memory.json` | On add/remove | Lazily per operation |
| Notes | `$AGENTOS_DATA_DIR/notes/*.md` | `memory.write` tool (write permission) | Not auto-loaded |
| Specialists | `$AGENTOS_DATA_DIR/specialists/specialists.json` | On create/update/delete | At startup, validated |
| User examples | `$AGENTOS_DATA_DIR/datasets/<specialistId>.examples.jsonl` | From the test bench | When a dataset is built |
| Model versions | `$AGENTOS_DATA_DIR/models/<specialistId>/index.json` + `<version>.json` | On training and promotion | When a specialist answers |
| Training runs | `$AGENTOS_DATA_DIR/training/runs.json`, `training/<runId>/checkpoints/*.json` | During and after each run | On view load |
| Scorecards | `$AGENTOS_DATA_DIR/benchmarks/scorecards.json` | After each run (bounded ring) | On view load |

Corrupt or hand-edited files never crash startup: `validate()` falls back to defaults and logs a
warning.

## 8. Error taxonomy

| Situation | Behaviour |
| --- | --- |
| Malformed request body | `400 VALIDATION_ERROR` with the offending field list |
| Missing/unknown resource | `404 NOT_FOUND` |
| Unknown training method or suite | `404 NOT_FOUND` / `400 UNKNOWN_METHOD` |
| External method requested | `409 METHOD_UNAVAILABLE` with its requirements |
| Method does not fit the specialist category | `400 METHOD_NOT_APPLICABLE` |
| Dataset empty or of the wrong kind | `400 EMPTY_DATASET` / `400 DATASET_INCOMPATIBLE` |
| A run for that specialist is already active | `409 RUN_IN_PROGRESS` |
| Suite run against the wrong category | `400 SUITE_MISMATCH` |
| Benchmark task threw | Recorded as a failed task (`source: 'error'`); the run still produces a card |
| Tool permission disabled | `{success:false, error:"Permission \"x\" is required…", output:{code:"PERMISSION_DENIED"}}`, logged at `warn` |
| Path escape attempt | `{success:false, error:"…outside the permitted directory"}` with `PATH_OUTSIDE_SANDBOX` |
| Provider unreachable/timeout | Trace step `error`, assistant message explains the failure, HTTP 200 with the degraded answer |
| Backend down (UI) | `ApiError` `NETWORK_ERROR`, offline banner with Retry, views degrade to empty states |
| Unexpected server exception | `500 INTERNAL_ERROR` (details logged, never returned) |

## 9. Extension recipes

**Provider**
```ts
class LlamaCppProvider implements ModelProvider { /* … */ }
providerRegistry.register(new LlamaCppProvider());
```

**Tool**
```ts
const dbQueryTool: ToolDefinition<{ sql: string }, unknown> = {
  id: 'db.query', name: 'Query database', description: 'Read-only SQL query.',
  category: 'knowledge', requires: ['read'],
  inputSchema: { type: 'object', properties: { sql: { type: 'string', required: true } } },
  async run({ sql }, ctx) { /* … */ },
};
toolRegistry.register(dbQueryTool);
```

**Training method**
```ts
export const sequenceLabelling: TrainingMethodDefinition = {
  id: 'sequence_label', name: 'Sequence labelling (CRF-style)', kind: 'gradient', runtime: 'in-process',
  available: true, appliesTo: ['research', 'custom'], datasetKinds: ['corpus'],
  description: 'Learns span boundaries over token sequences.',
  artifactKind: 'sequence_label · linear-chain tagger',
  requirements: [], honesty: 'Learns span boundaries; it does not generate prose.',
  hyperparameters: [{ key: 'epochs', label: 'Epochs', type: 'number', default: 40 }],
  async run(ctx) { /* return { artifactKind, artifact, metrics, primaryMetric } */ },
};
// then add it to TRAINING_METHODS and to the specialists' trainingMethodIds
```

**Benchmark suite**
```ts
// server/benchmarks/suites.ts — tasks are held-out items; `expected` is what the runner scores
const packingSuite: BenchmarkSuite = {
  id: 'packing.planning', name: 'Packing · planning', category: 'custom',
  description: 'Plans a packing order from a constraint list.',
  metrics: ['quality', 'taskSuccessRate', 'latencyMsP50', 'reliability'],
  taskCount: splitPool(PACKING_POOL, 3).evaluation.length,
  tasks: splitPool(PACKING_POOL, 3).evaluation.map((item, index) => ({
    id: `packing.planning#${index + 1}`, input: item.input, expected: item.expected, kind: 'label',
  })),
};
// then add the id to BenchmarkSuiteId (shared/types.ts), export the suite in
// BENCHMARK_SUITES, and point DATASET_SUITE at it
```

## 10. Testing strategy

| Layer | File | Covers |
| --- | --- | --- |
| Settings | `server/settingsStore.test.ts` | Defaults, restart persistence, partial merges, corrupt files, invalid enums, reset |
| Tools | `server/tools/registry.test.ts` | Execution, permissions, sandboxing, failures |
| Orchestration | `server/orchestration/orchestrator.test.ts` | Routing to specialists, tool planning, escalation, degradation, conversations |
| Core API | `server/routes/api.test.ts` | Health, providers, catalogue honesty, chat, validation, secrets, persistence, outages |
| Platform API | `server/routes/platform.test.ts` | Specialist CRUD/test/examples, normalisation, training end to end, versions, benchmarks, overview, comparison baselines |
| Learning primitives | `server/ml/*.test.ts`, `server/training/corpora.test.ts` | Real SGD convergence and holdout generalisation, feature auto-sizing, calibration sweeps, seeded corpus splits |
| Benchmarks | `server/benchmarks/runner.test.ts` | Suite coverage, real measurement, regression detection, repeat-run semantics, comparison trade-offs |
| UI | `src/**/*.test.tsx` | Shell and navigation, every view against mocked platform fixtures, primitives, offline banners |
| End-to-end | `scripts/smoke.mjs` | 50 checks against a real booted server: core API, specialists, a real training run, artefact inspection, benchmark baseline, normalisation, SSE |
