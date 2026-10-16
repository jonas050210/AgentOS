import { vi } from 'vitest';
import {
  activityFixture,
  capabilitiesFixture,
  healthFixture,
  modelsFixture,
  providersFixture,
  settingsFixture,
  toolsFixture,
} from './fixtures';

export interface MockOptions {
  /** Every request rejects with a network error. */
  failEverything?: boolean;
  chatResponse?: unknown;
  /** Chat endpoint behaves as if the backend refused or the socket died. */
  chatError?: { network: true } | { status: number; message: string; code?: string };
}

/** Installs a fetch mock that mirrors the AgentOS HTTP API shape. */
export function mockApi(overrides: Partial<Record<string, unknown>> = {}, options: MockOptions = {}) {
  const handler = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
    const path = url.replace(/^https?:\/\/[^/]+/, '');
    const method = init?.method ?? 'GET';

    if (options.failEverything) {
      throw new TypeError('Failed to fetch');
    }
    for (const [key, value] of Object.entries(overrides)) {
      if (path.startsWith(key)) {
        return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
    }
    if (path.startsWith('/api/health')) return json(healthFixture);
    if (path.startsWith('/api/providers')) return json(providersFixture);
    if (path.startsWith('/api/models')) return json(modelsFixture);
    if (path.startsWith('/api/capabilities')) return json(capabilitiesFixture);
    if (path.startsWith('/api/tools/') && method === 'POST') {
      return json({ toolId: 'calc.evaluate', success: true, output: { result: 42 }, durationMs: 3, timestamp: new Date().toISOString() });
    }
    if (path.startsWith('/api/tools')) return json(toolsFixture);
    if (path.startsWith('/api/chat')) {
      if (options.chatError) {
        if ('network' in options.chatError) throw new TypeError('Failed to fetch');
        return new Response(
          JSON.stringify({ error: options.chatError.message, code: options.chatError.code ?? 'HTTP_ERROR' }),
          { status: options.chatError.status, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return json(
        options.chatResponse ?? {
          conversationId: 'conv_test',
          message: {
            id: 'msg_a',
            role: 'assistant',
            content: 'Offline answer from the built-in engine.',
            timestamp: new Date('2026-01-01T00:00:03.000Z').toISOString(),
            meta: { capabilityId: 'general.assistant', providerId: 'builtin', model: 'offline-reasoner', degraded: true },
          },
          trace: [
            { step: 'route', detail: 'Auto-routed to General assistant', status: 'ok' },
            { step: 'model', detail: 'Using built-in offline engine', status: 'warn' },
          ],
        },
      );
    }
    if (path.startsWith('/api/settings/reset')) return json(settingsFixture);
    if (path.startsWith('/api/settings')) return json(settingsFixture);
    if (path.startsWith('/api/activity')) return json(activityFixture);
    if (path.startsWith('/api/memory')) return json({ records: [], count: 0 });
    if (path.startsWith('/api/speech/engines')) return json({ engines: [], settings: settingsFixture.voice });
    return new Response(JSON.stringify({ error: `No mock for ${path}`, code: 'NOT_FOUND' }), { status: 404 });
  });

  vi.stubGlobal('fetch', handler);
  return handler;
}

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}
