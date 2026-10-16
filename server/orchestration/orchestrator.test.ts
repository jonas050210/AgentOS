// @vitest-environment node
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { ModelProvider } from '../providers/types.js';

let dataDir: string;
let orchestrator: typeof import('./orchestrator.js');
let providerRegistry: typeof import('../providers/registry.js').providerRegistry;

/**
 * The suite must be hermetic: it may not depend on (or wait for) an Ollama
 * instance that happens to run on the machine executing the tests. Every test
 * starts from a provider registry where only the built-in offline engine is
 * available; tests that want an external provider register their own stub.
 */
function unavailableOllama(): ModelProvider {
  return {
    id: 'ollama',
    name: 'Ollama (local)',
    type: 'local',
    info: () => ({
      id: 'ollama',
      name: 'Ollama (local)',
      type: 'local',
      available: false,
      configured: true,
      detail: 'stubbed as unavailable for tests',
    }),
    isAvailable: async () => false,
    listModels: async () => [],
    generate: async () => {
      throw new Error('the unavailable Ollama stub must never be asked to generate');
    },
  };
}

beforeEach(async () => {
  dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'agentos-orch-'));
  process.env.AGENTOS_DATA_DIR = dataDir;
  process.env.AGENTOS_WORKSPACE_DIR = process.cwd();
  vi.resetModules();
  ({ providerRegistry } = await import('../providers/registry.js'));
  providerRegistry.register(unavailableOllama());
  orchestrator = await import('./orchestrator.js');
});

afterEach(() => {
  fs.rmSync(dataDir, { recursive: true, force: true });
  vi.restoreAllMocks();
});

describe('orchestrator', () => {
  it('rejects empty messages', async () => {
    await expect(orchestrator.handleChat({ message: '   ' })).rejects.toThrow(/must not be empty/);
  });

  it('rejects oversized messages', async () => {
    await expect(orchestrator.handleChat({ message: 'x'.repeat(8001) })).rejects.toThrow(/8000 characters/);
  });

  it('answers via the offline engine and flags the degraded mode', async () => {
    const res = await orchestrator.handleChat({ message: 'hello there' });
    expect(res.conversationId).toMatch(/^conv_/);
    expect(res.message.role).toBe('assistant');
    expect((res.message.meta as { degraded: boolean }).degraded).toBe(true);
    expect(res.message.content).toMatch(/offline engine/i);

    const steps = res.trace.map((t) => t.step);
    expect(steps).toContain('route');
    expect(steps).toContain('decide');
    expect(steps).toContain('escalate');
    expect(res.trace.find((t) => t.step === 'model')?.status).toBe('warn');
    // Nothing was trained in this fresh data dir, so the specialist escalated
    // and the offline engine answered — the meta must say exactly that.
    const meta = res.message.meta as {
      answeredBy: string;
      escalated: boolean;
      specialistId: string;
      degraded: boolean;
      providerId: string;
      providerName: string;
      model: string;
      providerError: string | null;
    };
    expect(meta.answeredBy).toBe('offline-engine');
    expect(meta.escalated).toBe(true);
    expect(meta.specialistId).toBeTruthy();
    // …including which provider answered, with its own model name (never Ollama's).
    expect(meta.providerId).toBe('builtin');
    expect(meta.providerName).toBe('Built-in offline assistant');
    expect(meta.model).toBe('offline-reasoner');
    expect(meta.providerError).toBeNull();
  });

  it('reports a real provider as the author and never as the offline engine', async () => {
    providerRegistry.register({
      id: 'ollama',
      name: 'Ollama (test stub)',
      type: 'local',
      info: () => ({
        id: 'ollama',
        name: 'Ollama (test stub)',
        type: 'local',
        available: true,
        configured: true,
        detail: 'stub',
      }),
      isAvailable: async () => true,
      listModels: async () => [],
      generate: async (req) => ({ text: 'Hello! I am the stub provider.', model: req.model ?? 'stub-default', providerId: 'ollama' }),
    });

    const res = await orchestrator.handleChat({ message: 'hello there' });
    const meta = res.message.meta as {
      answeredBy: string;
      degraded: boolean;
      escalated: boolean;
      providerId: string;
      providerName: string;
      model: string;
      providerError: string | null;
    };
    expect(res.message.content).toBe('Hello! I am the stub provider.');
    expect(meta.escalated).toBe(true);
    expect(meta.answeredBy).toBe('provider');
    expect(meta.degraded).toBe(false);
    expect(meta.providerId).toBe('ollama');
    expect(meta.providerName).toBe('Ollama (test stub)');
    // The configured Ollama model is the one the provider was asked for.
    expect(meta.model).toBe('llama3.2');
    expect(meta.providerError).toBeNull();

    const modelStep = res.trace.find((t) => t.step === 'model');
    expect(modelStep?.status).toBe('ok');
    expect(modelStep?.detail).toMatch(/Ollama \(test stub\) supplied the language answer \(llama3\.2\)/);
    expect(modelStep?.detail).not.toMatch(/offline/i);
  });

  it('resolves provider availability once per turn instead of probing twice', async () => {
    let probes = 0;
    providerRegistry.register({
      id: 'ollama',
      name: 'Counting provider',
      type: 'local',
      info: () => ({
        id: 'ollama',
        name: 'Counting provider',
        type: 'local',
        available: true,
        configured: true,
        detail: 'stub',
      }),
      isAvailable: async () => {
        probes += 1;
        return true;
      },
      listModels: async () => [],
      generate: async () => ({ text: 'counted', model: 'counting-model', providerId: 'ollama' }),
    });

    const res = await orchestrator.handleChat({ message: 'hello there' });
    // Probing a provider is a network round trip; the reporting must reuse the
    // provider that actually answered instead of looking one up again.
    expect(probes).toBe(1);
    expect((res.message.meta as { providerId: string }).providerId).toBe('ollama');
  });

  it('feeds recalled memory to the provider as grounding', async () => {
    const { memoryStore } = await import('../memory/store.js');
    memoryStore.add({ title: 'Deploy key', content: 'The staging deploy key rotates on Fridays.', tags: ['ops'] });

    let systemPrompt = '';
    providerRegistry.register({
      id: 'ollama',
      name: 'Capturing provider',
      type: 'local',
      info: () => ({
        id: 'ollama',
        name: 'Capturing provider',
        type: 'local',
        available: true,
        configured: true,
        detail: 'stub',
      }),
      isAvailable: async () => true,
      listModels: async () => [],
      generate: async (req) => {
        systemPrompt = req.system ?? '';
        return { text: 'Fridays.', model: req.model ?? 'stub', providerId: 'ollama' };
      },
    });

    const res = await orchestrator.handleChat({ message: 'when does the staging deploy key rotate?' });
    // The trace claims memory was recalled — the provider must actually see it.
    expect(res.trace.some((t) => t.step === 'memory')).toBe(true);
    expect(systemPrompt).toContain('The staging deploy key rotates on Fridays.');
  });

  it('answers a memory lookup with the stored note instead of claiming no match', async () => {
    const { memoryStore } = await import('../memory/store.js');
    memoryStore.add({ title: 'Release train', content: 'The release train leaves on Thursday.', tags: ['release'] });

    const res = await orchestrator.handleChat({ message: 'what did we save about the release train?' });
    expect(res.trace.find((t) => t.step === 'tool:memory.search')?.status).toBe('ok');
    // A successful lookup must be reported as one: the note is in the reply.
    expect(res.message.content).toContain('Release train');
    expect(res.message.content).toContain('The release train leaves on Thursday.');
    expect(res.message.content).not.toMatch(/no stored note matched/i);
    expect((res.message.meta as { answeredBy: string }).answeredBy).toBe('local');
  });

  it('builds memory.write input the tool accepts instead of failing validation', async () => {
    const { toolInputFor } = await import('../specialists/inference.js');
    const { toolRegistry } = await import('../tools/registry.js');
    const outcome = await toolRegistry.execute('memory.write', toolInputFor('memory.write', 'keep the release plan'));
    // Writing is off by default, so the permission gate refuses it — but the
    // input shape is valid, which is what previously made the call unusable.
    expect(outcome.success).toBe(false);
    expect(outcome.error).toMatch(/Permission "write"/);
  });

  it('treats an empty provider response as a failure instead of a blank answer', async () => {
    providerRegistry.register({
      id: 'ollama',
      name: 'Empty provider',
      type: 'local',
      info: () => ({
        id: 'ollama',
        name: 'Empty provider',
        type: 'local',
        available: true,
        configured: true,
        detail: 'stub',
      }),
      isAvailable: async () => true,
      listModels: async () => [],
      generate: async () => ({ text: '   ', model: 'empty-model', providerId: 'ollama' }),
    });

    const res = await orchestrator.handleChat({ message: 'hello there' });
    expect(res.message.content).toMatch(/could not complete this request/i);
    expect(res.message.content).toMatch(/empty response/i);
    const meta = res.message.meta as { answeredBy: string; providerError: string | null; degraded: boolean };
    expect(meta.providerError).toMatch(/empty response/i);
    expect(meta.answeredBy).toBe('local');
    expect(meta.degraded).toBe(false);
    expect(res.trace.find((t) => t.step === 'escalate')?.status).toBe('error');
    expect(res.trace.find((t) => t.step === 'model')?.status).toBe('error');
  });

  it('auto-routes messages to the matching capability', async () => {
    const coding = await orchestrator.handleChat({ message: 'please refactor this function' });
    expect((coding.message.meta as { capabilityId: string }).capabilityId).toBe('coding.assistant');

    const knowledge = await orchestrator.handleChat({ message: 'remember my API token strategy' });
    expect((knowledge.message.meta as { capabilityId: string }).capabilityId).toBe('knowledge.manager');
  });

  it('honours a pinned capability and pins the legacy capability onto a specialist', async () => {
    const res = await orchestrator.handleChat({ message: 'hello', capabilityId: 'research.assistant' });
    expect((res.message.meta as { capabilityId: string }).capabilityId).toBe('research.assistant');
    // The legacy capability API still works, but it now resolves to a real specialist.
    expect((res.message.meta as { specialistId: string }).specialistId).toBe('research-ai');
    expect(res.trace[0].detail).toMatch(/pinned by the user|maps to research ai/i);
    expect(res.trace[0].status).toBe('ok');
  });

  it('executes planned tools and records them in the trace', async () => {
    const res = await orchestrator.handleChat({ message: 'calculate 12 * (4 + 3)' });
    const toolStep = res.trace.find((t) => t.step === 'tool:calc.evaluate');
    expect(toolStep).toBeDefined();
    expect(toolStep?.status).toBe('ok');
    expect((res.message.meta as { tools: Array<{ success: boolean }> }).tools).toHaveLength(1);
    expect((res.message.meta as { tools: Array<{ success: boolean }> }).tools[0].success).toBe(true);
  });

  it('keeps a conversation going across turns', async () => {
    const first = await orchestrator.handleChat({ message: 'hello' });
    const second = await orchestrator.handleChat({ message: 'and again', conversationId: first.conversationId });
    expect(second.conversationId).toBe(first.conversationId);
  });

  it('warns and starts a new conversation when the id is unknown', async () => {
    const res = await orchestrator.handleChat({ message: 'hi', conversationId: 'conv_missing' });
    expect(res.trace.find((t) => t.step === 'conversation')?.status).toBe('warn');
    expect(res.conversationId).not.toBe('conv_missing');
  });

  it('surfaces provider failures gracefully instead of crashing', async () => {
    providerRegistry.register({
      id: 'ollama',
      name: 'Failing provider',
      type: 'local',
      info: () => ({ id: 'ollama', name: 'Failing provider', type: 'local', available: false, configured: true, detail: 'test stub' }),
      isAvailable: async () => true,
      listModels: async () => [],
      generate: async () => {
        throw new Error('connection refused');
      },
    });

    const res = await orchestrator.handleChat({ message: 'hello' });
    expect(res.message.content).toMatch(/could not complete this request/i);
    expect(res.message.content).toMatch(/connection refused/);
    expect(res.trace.find((t) => t.step === 'escalate')?.status).toBe('error');
    // The provider never answered, so the trace must not claim it did.
    const modelStep = res.trace.find((t) => t.step === 'model');
    expect(modelStep?.status).toBe('error');
    expect(modelStep?.detail).toMatch(/unreachable/);
    expect((res.message.meta as { providerError: string }).providerError).toBe('connection refused');

    // restore the real adapter for other tests
    vi.resetModules();
    ({ providerRegistry } = await import('../providers/registry.js'));
    expect(providerRegistry.get('ollama')?.name).toBe('Ollama (local)');
  });

  it('plans no tools for plain conversation', () => {
    expect(orchestrator.planTools('hello world', 'general-ai', ['system.info'])).toEqual([]);
  });

  it('plans the same tools the specialist policy would run (one shared planner)', () => {
    const planned = orchestrator.planTools('what is 12 * 4', 'coding-ai', ['calc.evaluate']);
    expect(planned).toHaveLength(1);
    expect(planned[0].toolId).toBe('calc.evaluate');
    expect(planned[0].input).toEqual({ expression: '12*4' });
  });

  it('routes to a named specialist and reports it in the trace', async () => {
    const res = await orchestrator.handleChat({ message: 'summarise our notes', specialistId: 'knowledge-ai' });
    expect((res.message.meta as { specialistId: string }).specialistId).toBe('knowledge-ai');
    expect(res.trace[0].detail).toMatch(/pinned by the user/i);
  });

  it('rejects an unknown specialist instead of silently falling back', async () => {
    await expect(orchestrator.handleChat({ message: 'hi', specialistId: 'no-such-ai' })).rejects.toThrow(/Unknown specialist/);
  });
});

describe('specialist escalation reporting', () => {
  it('names the built-in offline engine, including its own model', async () => {
    const { runSpecialist } = await import('../specialists/inference.js');
    const result = await runSpecialist('general-ai', 'hello there');

    expect(result.escalated).toBe(true);
    expect(result.answeredBy).toBe('offline-engine');
    expect(result.providerId).toBe('builtin');
    expect(result.providerName).toBe('Built-in offline assistant');
    expect(result.model).toBe('offline-reasoner');
    expect(result.providerError).toBeNull();
    expect(result.answer).toMatch(/offline engine/i);
    expect(result.escalationReason).toBeTruthy();
  });

  it('names the external provider that answered', async () => {
    providerRegistry.register({
      id: 'ollama',
      name: 'Answering stub',
      type: 'local',
      info: () => ({
        id: 'ollama',
        name: 'Answering stub',
        type: 'local',
        available: true,
        configured: true,
        detail: 'stub',
      }),
      isAvailable: async () => true,
      listModels: async () => [],
      generate: async (req) => ({ text: 'Answered externally.', model: req.model ?? 'stub', providerId: 'ollama' }),
    });
    const { runSpecialist } = await import('../specialists/inference.js');
    const result = await runSpecialist('general-ai', 'hello there');

    expect(result.answeredBy).toBe('provider');
    expect(result.providerId).toBe('ollama');
    expect(result.providerName).toBe('Answering stub');
    expect(result.model).toBe('llama3.2');
    expect(result.providerError).toBeNull();
  });

  it('reports a provider failure without naming it as the author', async () => {
    providerRegistry.register({
      id: 'ollama',
      name: 'Broken stub',
      type: 'local',
      info: () => ({
        id: 'ollama',
        name: 'Broken stub',
        type: 'local',
        available: true,
        configured: true,
        detail: 'stub',
      }),
      isAvailable: async () => true,
      listModels: async () => [],
      generate: async () => {
        throw new Error('connection reset');
      },
    });
    const { runSpecialist } = await import('../specialists/inference.js');
    const result = await runSpecialist('general-ai', 'hello there');

    expect(result.providerError).toBe('connection reset');
    expect(result.answeredBy).toBe('local');
    expect(result.providerId).toBe('ollama');
    // No model produced this text — the answer is the failure notice itself.
    expect(result.model).toBeNull();
    expect(result.answer).toMatch(/connection reset/);
    expect(result.trace.find((t) => t.step === 'escalate')?.status).toBe('error');
  });

  it('keeps provider metadata empty when the local path answers', async () => {
    const { runSpecialist } = await import('../specialists/inference.js');
    const result = await runSpecialist('coding-ai', 'what is 12 * 4');

    expect(result.escalated).toBe(false);
    expect(result.answeredBy).toBe('local');
    expect(result.providerId).toBeNull();
    expect(result.providerName).toBeNull();
    expect(result.model).toBeNull();
    expect(result.escalationReason).toBeNull();
    expect(result.answer).toMatch(/48/);
  });

  it('reports a failed tool as the escalation reason instead of guessing one', async () => {
    // Swap the calculator the rule planner picks for one that throws: the tool
    // call fails and the request escalates naming the tool failure as its reason.
    const { toolRegistry } = await import('../tools/registry.js');
    const { runSpecialist } = await import('../specialists/inference.js');
    toolRegistry.register({
      id: 'calc.evaluate',
      name: 'Calculator (broken stub)',
      description: 'Always fails',
      category: 'compute',
      requires: [],
      inputSchema: { type: 'object', properties: { expression: { type: 'string', required: true } } },
      run: async () => {
        throw new Error('calculator offline');
      },
    });

    const result = await runSpecialist('coding-ai', 'what is 12 * 4');
    expect(result.escalated).toBe(true);
    expect(result.escalationReason).toMatch(/calc\.evaluate failed: calculator offline/);
    expect(result.providerError).toBeNull();
    expect(result.answeredBy).toBe('offline-engine');
  });
});

describe('capability routing', () => {
  it('routes by keyword and falls back to the general assistant', async () => {
    const { routeCapability } = await import('../capabilities/registry.js');
    expect(routeCapability('write a typescript class').id).toBe('coding.assistant');
    expect(routeCapability('what is the weather').id).toBe('general.assistant');
    expect(routeCapability('').id).toBe('general.assistant');
    expect(routeCapability('save this note').category).toBe('knowledge');
  });
});

describe('conversation store', () => {
  it('evicts the oldest conversation beyond the cap', async () => {
    const { ConversationStore } = await import('./conversations.js');
    const store = new ConversationStore();
    const first = store.create('general.assistant');
    for (let i = 0; i < 60; i++) store.create('general.assistant');
    expect(store.get(first.id)).toBeUndefined();
    expect(store.list().length).toBeLessThanOrEqual(50);
  });

  it('bounds stored messages per conversation', async () => {
    const { ConversationStore } = await import('./conversations.js');
    const store = new ConversationStore();
    const conv = store.create('general.assistant');
    for (let i = 0; i < 60; i++) {
      store.append(conv.id, { id: `m${i}`, role: 'user', content: `t${i}`, timestamp: new Date().toISOString() });
    }
    expect(store.get(conv.id)!.messages.length).toBe(40);
  });
});
