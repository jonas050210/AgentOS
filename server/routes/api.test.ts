// @vitest-environment node
import { describe, expect, it, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Server } from 'node:http';

let server: Server;
let baseUrl: string;
let dataDir: string;
let providerRegistry: typeof import('../providers/registry.js').providerRegistry;

beforeAll(async () => {
  dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'agentos-api-'));
  process.env.AGENTOS_DATA_DIR = dataDir;
  process.env.AGENTOS_WORKSPACE_DIR = process.cwd();
  vi.resetModules();
  const { createApp } = await import('../app.js');
  // Imported after the same module reset so the app and the test share instances.
  ({ providerRegistry } = await import('../providers/registry.js'));
  const app = createApp();
  await new Promise<void>((resolve) => {
    server = app.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  const port = typeof address === 'object' && address ? address.port : 0;
  baseUrl = `http://127.0.0.1:${port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
  fs.rmSync(dataDir, { recursive: true, force: true });
});

beforeEach(async () => {
  await fetch(`${baseUrl}/api/settings/reset`, { method: 'POST' });
});

const get = (p: string) => fetch(`${baseUrl}${p}`);
/** Typed JSON reader — Response.json() is `unknown` in the DOM typings. */
async function readJson<T = any>(res: Response): Promise<T> {
  return (await res.json()) as T;
}
const send = (p: string, body: unknown, method = 'POST') =>
  fetch(`${baseUrl}${p}`, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

describe('AgentOS HTTP API', () => {
  it('exposes health with honest component states', async () => {
    const res = await get('/api/health');
    expect(res.status).toBe(200);
    const body = await readJson(res);
    expect(['healthy', 'degraded', 'down']).toContain(body.status);
    expect(body.components.map((c: { id: string }) => c.id)).toEqual(
      expect.arrayContaining(['orchestrator', 'models', 'tools', 'permissions', 'memory', 'settings']),
    );
  });

  it('lists providers with availability flags', async () => {
    const body = await readJson(await get('/api/providers'));
    const builtin = body.find((p: { id: string }) => p.id === 'builtin');
    expect(builtin.available).toBe(true);
    expect(builtin.type).toBe('local');
  });

  it('returns a model catalogue that never claims unreachable models are usable', async () => {
    const body = await readJson(await get('/api/models'));
    expect(Array.isArray(body.models)).toBe(true);
    for (const model of body.models) {
      if (model.providerId === 'builtin') expect(model.status).toBe('available');
      else expect(['available', 'unavailable', 'unconfigured', 'error']).toContain(model.status);
    }
    expect(body.providers.some((p: { id: string }) => p.id === 'builtin')).toBe(true);
  });

  it('describes capabilities and tools with permission gates', async () => {
    const caps = await readJson(await get('/api/capabilities'));
    expect(caps.capabilities.map((c: { id: string }) => c.id)).toEqual(
      expect.arrayContaining(['coding.assistant', 'research.assistant', 'knowledge.manager', 'general.assistant']),
    );
    const tools = await readJson(await get('/api/tools'));
    const fetchTool = tools.tools.find((t: { id: string }) => t.id === 'http.fetch');
    expect(fetchTool.available).toBe(false); // network permission is off by default
  });

  it('runs a chat turn end to end', async () => {
    const res = await send('/api/chat', { message: 'calculate 2 * 21' });
    expect(res.status).toBe(200);
    const body = await readJson(res);
    expect(body.conversationId).toBeTruthy();
    expect(body.message.role).toBe('assistant');
    expect(body.trace.length).toBeGreaterThan(1);
  });

  it('rejects malformed chat payloads with a validation error', async () => {
    const res = await send('/api/chat', { message: '' });
    expect(res.status).toBe(400);
    const body = await readJson(res);
    expect(body.code).toBe('VALIDATION_ERROR');
  });

  it('returns 404 for unknown conversations and unknown routes', async () => {
    expect((await get('/api/conversations/nope')).status).toBe(404);
    expect((await get('/api/does-not-exist')).status).toBe(404);
  });

  it('executes allowed tools over HTTP and denies blocked ones', async () => {
    const ok = await readJson(await send('/api/tools/calc.evaluate/execute', { input: { expression: '6*7' } }));
    expect(ok.success).toBe(true);
    expect(ok.output).toEqual({ result: 42 });

    const denied = await readJson(await send('/api/tools/http.fetch/execute', { input: { url: 'https://example.com' } }));
    expect(denied.success).toBe(false);
    expect(denied.error).toMatch(/Permission "network"/);

    const unknown = await send('/api/tools/nope/execute', { input: {} });
    expect((await readJson(unknown)).success).toBe(false);
  });

  it('never returns stored API keys to the client', async () => {
    const saved = await readJson(await send('/api/settings', { providers: { openai: { apiKey: 'sk-super-secret', baseUrl: 'https://api.example.test/v1', model: 'm' } } }, 'PUT'));
    expect(saved.providers.openai.apiKey).toBe('');
    expect(saved.providers.openai.hasApiKey).toBe(true);

    const reread = await readJson(await get('/api/settings'));
    expect(JSON.stringify(reread)).not.toContain('sk-super-secret');
    expect(reread.providers.openai.hasApiKey).toBe(true);
  });

  it('keeps the stored key when an empty key is submitted', async () => {
    await send('/api/settings', { providers: { openai: { apiKey: 'sk-first', baseUrl: 'https://api.example.test/v1', model: 'm' } } }, 'PUT');
    await send('/api/settings', { providers: { openai: { apiKey: '' } } }, 'PUT');
    const body = await readJson(await get('/api/settings'));
    expect(body.providers.openai.hasApiKey).toBe(true);
  });

  it('rejects invalid settings payloads', async () => {
    const res = await send('/api/settings', { theme: 'neon' }, 'PUT');
    expect(res.status).toBe(400);
    expect((await readJson(res)).code).toBe('VALIDATION_ERROR');
  });

  it('persists settings to disk and reloads them (survives restart)', async () => {
    await send('/api/settings', { theme: 'light', logging: { level: 'debug' } }, 'PUT');
    const onDisk = JSON.parse(fs.readFileSync(path.join(dataDir, 'settings.json'), 'utf-8'));
    expect(onDisk.theme).toBe('light');
    expect(onDisk.logging.level).toBe('debug');

    vi.resetModules();
    const { SettingsStore } = await import('../settingsStore.js');
    const reopened = new SettingsStore(dataDir);
    expect(reopened.get().theme).toBe('light');
    expect(reopened.get().logging.level).toBe('debug');
  });

  it('exposes activity entries and redacts secret-looking metadata', async () => {
    const body = await readJson(await get('/api/activity?limit=50'));
    expect(Array.isArray(body.entries)).toBe(true);
    const leaked = body.entries.filter((e: { meta?: Record<string, unknown> }) =>
      JSON.stringify(e.meta ?? {}).includes('sk-super-secret'),
    );
    expect(leaked).toHaveLength(0);
  });

  it('stores and deletes memory records', async () => {
    const created = await readJson(await send('/api/memory', { title: 'Roadmap', content: 'Ship v0.1', tags: ['plan'] }));
    expect(created.id).toMatch(/^mem_/);
    const list = await readJson(await get('/api/memory?q=roadmap'));
    expect(list.records.some((r: { id: string }) => r.id === created.id)).toBe(true);
    const del = await fetch(`${baseUrl}/api/memory/${created.id}`, { method: 'DELETE' });
    expect(del.status).toBe(200);
    expect((await readJson(await fetch(`${baseUrl}/api/memory/${created.id}`, { method: 'DELETE' }))).error).toBeTruthy();
  });

  it('reports speech engine status', async () => {
    const body = await readJson(await get('/api/speech/engines'));
    const ids = body.engines.map((e: { id: string }) => e.id);
    expect(ids).toEqual(expect.arrayContaining(['browser-stt', 'browser-tts', 'whisper-server', 'piper-server']));
    expect(body.engines.find((e: { id: string }) => e.id === 'whisper-server').available).toBe(false);
  });

  it('handles an unreachable provider during a chat turn without a 500', async () => {
    const original = providerRegistry.get('ollama')!;
    providerRegistry.register({
      id: 'ollama',
      name: 'Broken',
      type: 'local',
      info: () => ({ id: 'ollama', name: 'Broken', type: 'local', available: false, configured: true, detail: 'stub' }),
      isAvailable: async () => true,
      listModels: async () => [],
      generate: async () => {
        throw new Error('socket hang up');
      },
    });
    const res = await send('/api/chat', { message: 'hi' });
    expect(res.status).toBe(200);
    const body = await readJson(res);
    expect(body.message.content).toMatch(/socket hang up/);
    expect(body.trace.find((t: { step: string }) => t.step === 'inference').status).toBe('error');
    providerRegistry.register(original);
  });
});
