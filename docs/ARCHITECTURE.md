# AgentOS architecture

This document describes the modules, the contracts between them, and how to extend the system
without rewriting it. It reflects the code as implemented (v0.1.0).

## 1. Design principles

1. **One interface per replaceable part.** Providers, tools, capabilities and speech engines are
   described by small contracts; the orchestrator only knows those contracts.
2. **Honest status.** Availability is probed at runtime. A component that cannot run reports
   `unavailable`/`unconfigured` and the UI shows exactly that.
3. **Least surprise on failure.** Every external call has a timeout; every failure produces a
   structured, user-readable error instead of an exception escaping to the client.
4. **Typed end to end.** `shared/types.ts` is imported by both processes; TypeScript runs in strict
   mode with `noUnusedLocals`/`noUnusedParameters`.
5. **Minimal dependencies.** Express, zod, React, Tailwind, Vite, Vitest. No state-management
   library, no UI kit, no ORM.

## 2. Module map

### Shared
| File | Responsibility |
| --- | --- |
| `shared/types.ts` | Domain types (`Settings`, `ModelInfo`, `CapabilityInfo`, `ToolInfo`, `AgentChatResponse`, `ActivityEntry`, `HealthStatus`) and `DEFAULT_SETTINGS`. |

### Backend
| Module | Files | Responsibility |
| --- | --- | --- |
| Logging | `server/logger.ts` | Levelled, bounded (500) ring buffer, SSE fan-out, metadata redaction, console mirroring. |
| Settings | `server/settingsStore.ts` | `validate()` guards every read *and* write; atomic temp-file + rename persistence. |
| Providers | `server/providers/*` | `ModelProvider` contract; `EchoProvider`, `OllamaProvider`, `OpenAIProvider`; registry with availability probing and preference order `openai → ollama → builtin`. |
| Model catalog | `server/models/catalog.ts` | Merges declared models with live availability; declares *expected* models for unreachable providers so the UI can explain what is missing. |
| Capabilities | `server/capabilities/registry.ts` | Capability definitions (system prompt, bound tools, routing keywords), deterministic `routeCapability()`, status computation (`available`/`partial`/`unavailable`). |
| Tools | `server/tools/*` | `ToolDefinition` contract, registry with permission enforcement, `validateInput()`, structured outcomes, path sandbox. |
| Memory | `server/memory/*` | JSON-backed knowledge store (`add`, `search`, `list`, `remove`) + `memory.search` / `memory.save` tools. |
| Orchestration | `server/orchestration/*` | `handleChat()` pipeline, `planTools()` intent planner, `buildSystemPrompt()`, bounded `ConversationStore`. |
| Speech | `server/speech/engines.ts` | `SpeechProvider` contract + status reporting for browser and (unconfigured) server engines. |
| API | `server/routes/api.ts`, `server/app.ts` | REST + SSE surface, zod validation, `ApiError` mapping, CORS allow-list, static asset serving. |

### Frontend
| Module | Files | Responsibility |
| --- | --- | --- |
| State | `src/state/SystemContext.tsx` | Loads one consistent snapshot (health, providers, models, capabilities, tools, settings, activity), polls every 20 s, subscribes to the SSE stream, exposes `saveSettings`/`resetSettings` with typed results. Shape-checks every payload. |
| API client | `src/lib/api.ts` | Typed calls, 30 s timeout, `ApiError` with `status`/`code`, explicit network-failure message. |
| Speech hook | `src/hooks/useSpeech.ts` | Capability probing (never `in` checks alone), recognition lifecycle, synthesis with cleanup. |
| UI primitives | `src/components/ui.tsx` | `Card`, `Badge`, `StatusBadge`, `StatusDot`, `Toggle`, `ErrorBanner`, `EmptyState`, `Spinner`, `KeyValue`, `SectionTitle`. |
| Shell | `src/components/Sidebar.tsx`, `TopBar.tsx`, `src/App.tsx` | Navigation, live status indicators, error boundary, footer status bar. |
| Views | `src/views/*` | Chat, Models, Capabilities, Activity, Settings. |

## 3. Contracts

```ts
// server/providers/types.ts
interface ModelProvider {
  readonly id: string;
  readonly name: string;
  readonly type: 'local' | 'cloud' | 'remote';
  info(): ProviderInfo;
  isAvailable(): Promise<boolean>;
  listModels(): Promise<ModelInfo[]>;
  generate(req: GenerateRequest): Promise<GenerateResponse>;
}

// server/tools/types.ts
interface ToolDefinition<Input, Output> {
  id: string;
  name: string;
  description: string;
  category: string;
  requires: Permission[];            // read | write | network | execute
  inputSchema: Record<string, unknown>; // JSON-schema-ish, drives validation + UI
  run(input: Input, ctx: ToolContext): Promise<Output>;
}

// server/capabilities/registry.ts
interface CapabilityDefinition {
  id: string; name: string; description: string;
  category: 'coding' | 'research' | 'knowledge' | 'general' | 'speech';
  systemPrompt: string;
  toolIds: string[];
  keywords: string[];
}

// server/speech/engines.ts
interface SpeechProvider {
  readonly id: string; readonly name: string; readonly kind: 'stt' | 'tts';
  status(): Promise<SpeechEngineStatus>;
}
```

## 4. A chat turn, step by step

```
POST /api/chat {message, conversationId?, capabilityId?}
   │  zod validation → 400 VALIDATION_ERROR on malformed input
   ▼
handleChat()
   ├─ route          capability pinned by user, else keyword routing
   ├─ conversation   existing id, else new conversation (bounded history)
   ├─ plan           planTools(): URL → http.fetch, "calculate …" → calc.evaluate,
   │                 file reference → workspace.read, "remember …" → memory.save,
   │                 capability fallbacks (coding → workspace.list, research → memory.search)
   ├─ execute        ToolRegistry.execute(): schema validation → permission gate →
   │                 sandboxed run → structured outcome (never throws to the caller)
   ├─ recall         memory.search results injected as grounding context
   ├─ select         providerRegistry.preferred() probes availability in order
   ├─ generate       provider.generate() with the capability's system prompt
   │                 failure → trace entry(status=error) + degraded answer (HTTP 200)
   └─ respond        {conversationId, message(+meta), trace[]}
```

Trace steps are surfaced verbatim in the Chat view, which is how the UI stays honest about what the
system actually did.

## 5. Persistence

| Artefact | Location | Written | Loaded |
| --- | --- | --- | --- |
| Settings | `$AGENTOS_DATA_DIR/settings.json` | On every change (atomic rename) | At startup, validated |
| Memory | `$AGENTOS_DATA_DIR/memory.json` | On add/remove | Lazily per operation |
| Notes | `$AGENTOS_DATA_DIR/notes/*.md` | `memory.write` tool (write permission) | Not auto-loaded |

Corrupt or hand-edited files never crash startup: `validate()` falls back to defaults and logs a
warning.

## 6. Error taxonomy

| Situation | Behaviour |
| --- | --- |
| Malformed request body | `400 VALIDATION_ERROR` with the offending field list |
| Missing/unknown resource | `404 NOT_FOUND` |
| Tool permission disabled | `{success:false, error:"Permission "x" is required…", output:{code:"PERMISSION_DENIED"}}`, logged at `warn` |
| Path escape attempt | `{success:false, error:"…outside the permitted directory"}` with `PATH_OUTSIDE_SANDBOX` |
| Provider unreachable/timeout | Trace step `error`, assistant message explains the failure, HTTP 200 with the degraded answer |
| Backend down (UI) | `ApiError` `NETWORK_ERROR`, offline banner with Retry, views degrade to empty states |
| Unexpected server exception | `500 INTERNAL_ERROR` (details logged, never returned) |

## 7. Extension recipes

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

**Capability**
```ts
CAPABILITIES.push({
  id: 'data.analysis', name: 'Data analysis', description: 'Explore datasets.',
  category: 'research', systemPrompt: '…', toolIds: ['db.query', 'calc.evaluate'],
  keywords: ['dataset', 'correlate', 'chart'],
});
```

## 8. Testing strategy

| Layer | File | Covers |
| --- | --- | --- |
| Settings | `server/settingsStore.test.ts` | Defaults, restart persistence, partial merges, corrupt files, invalid enum values, reset |
| Tools | `server/tools/registry.test.ts` | Execution, arithmetic errors, unknown tools, permission denial, input validation, path traversal, absolute paths, notes sandboxing, network failures |
| Orchestration | `server/orchestration/orchestrator.test.ts` | Validation limits, degraded mode, routing, pinned capabilities, tool traces, conversation continuity/eviction, provider failure |
| API | `server/routes/api.test.ts` | Health, providers, catalogue honesty, capabilities/tools, chat, validation errors, 404s, tool execution, secret handling, settings persistence, activity redaction, memory CRUD, speech status, provider outage |
| UI | `src/components/ui.test.tsx`, `src/views/ChatView.test.tsx`, `src/views/SettingsView.test.tsx` | Primitives, chat send/reply/trace, network failure, backend validation errors, disabled states, voice gating, offline banners, settings round-trip |
| End-to-end | `scripts/smoke.mjs` | 30 checks against a real booted server (including SSE and on-disk verification) |
