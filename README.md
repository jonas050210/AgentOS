# AgentOS

**A modular AI desktop system** that unifies specialized models, voice interaction, persistent
memory and tool-using agents in one local-first application.

AgentOS is a working foundation, not a mockup: the orchestrator routes real requests, tools execute
with real permission checks, settings persist across restarts, and every status indicator reflects
the actual state of the running system. Anything that is *not* implemented yet is labelled as such
in the UI and in this document.

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
npm test             # 67 unit + integration tests (vitest, jsdom + node)
npm run typecheck    # strict TypeScript across client, server and shared types
npm run build        # production build (also type-checks)
npm run smoke        # boots the real backend and runs 30 end-to-end API checks
```

---

## What works today

| Area | Status | Notes |
| --- | --- | --- |
| Agent orchestration | ✅ implemented | Deterministic router + tool planner + provider selection, every step traced |
| Model providers | ✅ implemented | Built-in offline engine (always available), Ollama adapter, OpenAI-compatible adapter |
| Real LLM inference | ⚠️ needs configuration | Requires Ollama running locally or an API key — the app says so until then |
| Tools & permissions | ✅ implemented | 8 tools, 4 permission gates, path sandbox, structured input validation |
| Capabilities | ✅ implemented | Coding, research, knowledge management, general assistance, voice interface |
| Speech | ◐ partial | Browser speech-to-text/synthesis are wired and working; server-side engines are interface-ready stubs |
| Persistent memory | ✅ implemented | Notes stored in `data/memory.json`, searchable, plus `memory.save` / `memory.write` tools |
| Settings persistence | ✅ implemented | Atomic JSON writes, validated on load, secrets never sent to the browser |
| Activity log | ✅ implemented | Ring buffer + live server-sent-events stream, secret redaction, level filtering |
| Desktop shell (native window) | ❌ not implemented | Milestone 2 — see [Roadmap](#roadmap) |

### Interface

- **Chat** — central interaction surface with capability selector (or auto-routing), reasoning trace
  per reply, dictation, read-aloud, and explicit error banners.
- **Models** — provider cards with live reachability, a model catalogue that marks unreachable models
  as `unavailable`/`unconfigured`, plus a setup guide and runtime diagnostics.
- **Capabilities** — capability modules with availability status, the tool registry with permission
  badges, and a structured tool runner (JSON input → validated execution → structured output/error).
- **Activity** — live event stream with level counters, text filter, and follow mode.
- **Settings** — provider credentials, permission switches, voice engines, log level, persistence
  path; all changes are written to disk immediately.

All controls are functional or visibly disabled with an explanatory tooltip. Nothing is a decoration.

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
    types.ts             #   ModelProvider contract
    registry.ts          #   registration + live availability probing + preference order
    echoProvider.ts      #   always-available deterministic offline engine
    ollamaProvider.ts    #   local inference backend
    openaiProvider.ts    #   OpenAI-compatible HTTP API
  models/catalog.ts      # merges provider declarations with live availability
  orchestration/
    orchestrator.ts      # plan → execute tools → select provider → generate → trace
    conversations.ts     # bounded in-memory conversation history
  capabilities/registry.ts  # capability definitions + keyword router + status computation
  tools/
    types.ts             #   ToolDefinition contract + structured input validation
    registry.ts          #   registration, permission enforcement, structured outcomes
    paths.ts             #   path sandbox (blocks `..`, absolute paths, symlink escapes)
    builtin.ts           #   system.info, calc, workspace list/read, memory.write, http.fetch
  memory/                # persistent knowledge store + memory tools
  speech/engines.ts      # interchangeable STT/TTS engine descriptors
  routes/api.ts          # versioned REST API + SSE, zod-validated payloads, error mapping

src/                     # React desktop UI (no framework beyond React + Tailwind)
  state/SystemContext.tsx  # one live snapshot of system state, defensive against bad payloads
  lib/api.ts               # typed API client with explicit network/timeout errors
  hooks/useSpeech.ts       # Web Speech API wrapper with capability probing
  components/              # reusable design-system primitives + shell (sidebar, top bar)
  views/                   # Chat, Models, Capabilities, Activity, Settings
```

Deep dive: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Requirement-by-requirement verification: [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md).

### Request flow

1. `POST /api/chat` is validated (zod) and handed to the orchestrator.
2. The **router** picks a capability (user-pinned or keyword auto-routing).
3. The **planner** derives tool calls from the message; each call is validated, permission-checked
   and executed inside a sandbox. Failures become trace entries, never crashes.
4. Relevant **memory** records are recalled and injected as grounding context.
5. The **provider registry** selects the first available provider (external preferred, built-in
   fallback) and the response is generated with the capability's system directive.
6. The reply, its metadata (capability, provider, model, degraded flag, tool outcomes, duration) and
   the full trace are stored in the conversation and returned to the UI.

### Extension points

- **New model provider** — implement `ModelProvider` (`server/providers/types.ts`) and register it in
  `server/providers/registry.ts`. Nothing else changes.
- **New tool** — implement `ToolDefinition` (declare `requires`, `inputSchema`, `run`) and add it to
  `BUILTIN_TOOLS`. It appears in the UI with its permission badge automatically.
- **New capability** — add a `CapabilityDefinition` with system prompt, bound tools and routing
  keywords to `CAPABILITIES`. It is routed and displayed automatically.
- **New speech engine** — implement `SpeechProvider` and add it to `speechEngines`.

---

## Configuration

Copy [`.env.example`](.env.example) to `.env` (git-ignored) to override defaults:

| Variable | Default | Purpose |
| --- | --- | --- |
| `AGENTOS_PORT` | `4317` | API port |
| `AGENTOS_HOST` | `0.0.0.0` | API bind address |
| `AGENTOS_DATA_DIR` | `./data` | Settings, memory and notes location |
| `AGENTOS_WORKSPACE_DIR` | process cwd | Root that filesystem tools are confined to |
| `AGENTOS_ALLOWED_ORIGINS` | *(empty)* | Extra browser origins allowed to call the API |
| `AGENTOS_LOG_LEVEL` | `info` | Startup log level (then taken from settings) |

Persisted state (all under `AGENTOS_DATA_DIR`, `./data` by default):

- `settings.json` — validated settings, written atomically (temp file + rename).
- `memory.json` — knowledge-store records.
- `notes/*.md` — notes written through the `memory.write` tool.

### Enabling real models

**Local (Ollama).** Install Ollama, run `ollama pull llama3.2`, keep the default base URL
(`http://127.0.0.1:11434`) in *Settings → Model providers*. The catalogue picks up pulled models on
the next probe.

**API (OpenAI-compatible).** Paste a key and optional base URL in *Settings → Model providers*.
The key is stored server-side in `settings.json`, is never returned to the browser, and is redacted
in logs. Works with OpenAI, Azure-compatible gateways, LM Studio, vLLM, etc.

**Nothing configured.** Chat keeps working through the built-in deterministic engine. Its replies are
clearly labelled and the UI marks responses as degraded — it never pretends a model answered.

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
- Incoming payloads are validated with zod; unknown fields are rejected.
- The browser origin allow-list accepts localhost, `*.e2b.app` preview hosts and explicitly
  configured origins.

---

## Limitations (explicitly not done)

1. **No native desktop window.** AgentOS runs as a local web application; packaging with
   Tauri/Electron is a later milestone.
2. **Server-side speech engines are stubs.** Whisper/Piper adapters return `available: false`; the
   working speech path is the browser Web Speech API.
3. **Planner is deterministic, not model-driven.** Tool selection uses explicit intent rules; swapping
   in an LLM planner is a single function (`planTools`) and is the highest-value next step.
4. **Conversations are in memory only.** They reset on restart; only settings, memory and notes are
   persistent.
5. **No authentication.** The API is designed for a local, single-user machine. Do not expose the
   port to untrusted networks.
6. **Model catalogue is provider-driven.** Without a reachable provider only the built-in model is
   listed; there is no static model list to browse offline.

## Roadmap

1. Model-backed tool planning and streaming responses.
2. Conversation persistence + export.
3. Server-side speech engines (Whisper/Piper) behind the existing interface.
4. Desktop packaging (Tauri) with OS-level permission prompts.
5. Plugin/skill loading from a local directory with signed manifests.

## License

Unlicensed prototype — add a license before distributing.
