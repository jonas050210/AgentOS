// @vitest-environment node
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

let dataDir: string;
let orchestrator: typeof import('./orchestrator.js');
let providerRegistry: typeof import('../providers/registry.js').providerRegistry;

beforeEach(async () => {
  dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'agentos-orch-'));
  process.env.AGENTOS_DATA_DIR = dataDir;
  process.env.AGENTOS_WORKSPACE_DIR = process.cwd();
  vi.resetModules();
  ({ providerRegistry } = await import('../providers/registry.js'));
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
    expect(steps).toContain('model');
    expect(steps).toContain('inference');
    expect(res.trace.find((t) => t.step === 'model')?.status).toBe('warn');
  });

  it('auto-routes messages to the matching capability', async () => {
    const coding = await orchestrator.handleChat({ message: 'please refactor this function' });
    expect((coding.message.meta as { capabilityId: string }).capabilityId).toBe('coding.assistant');

    const knowledge = await orchestrator.handleChat({ message: 'remember my API token strategy' });
    expect((knowledge.message.meta as { capabilityId: string }).capabilityId).toBe('knowledge.manager');
  });

  it('honours a pinned capability', async () => {
    const res = await orchestrator.handleChat({ message: 'hello', capabilityId: 'research.assistant' });
    expect((res.message.meta as { capabilityId: string }).capabilityId).toBe('research.assistant');
    expect(res.trace[0].detail).toMatch(/pinned by user/);
  });

  it('executes planned tools and records them in the trace', async () => {
    const res = await orchestrator.handleChat({ message: 'calculate 12 * (4 + 3)' });
    const toolStep = res.trace.find((t) => t.step === 'tool:calc.evaluate');
    expect(toolStep).toBeDefined();
    expect(toolStep?.status).toBe('ok');
    expect((res.message.meta as { tools: unknown[] }).tools).toHaveLength(1);
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
    expect(res.message.content).toMatch(/could not be reached/);
    expect(res.message.content).toMatch(/connection refused/);
    expect(res.trace.find((t) => t.step === 'inference')?.status).toBe('error');

    // restore the real adapter for other tests
    vi.resetModules();
    ({ providerRegistry } = await import('../providers/registry.js'));
    expect(providerRegistry.get('ollama')?.name).toBe('Ollama (local)');
  });

  it('plans no tools for plain conversation', () => {
    expect(orchestrator.planTools('hello world', 'general.assistant', ['system.info'])).toEqual([]);
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
