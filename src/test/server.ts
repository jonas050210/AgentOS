import { vi } from 'vitest';
import {
  activityFixture,
  baselineScorecardFixture,
  capabilitiesFixture,
  comparisonFixture,
  datasetFixture,
  externalMethodFixture,
  healthFixture,
  modelsFixture,
  overviewFixture,
  providersFixture,
  runFixture,
  scorecardFixture,
  settingsFixture,
  specialistFixture,
  suiteFixture,
  testResultFixture,
  toolsFixture,
  trainingMethodFixture,
  untrainedSpecialistFixture,
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
    // ---------- platform layer ----------
    if (path.startsWith('/api/platform/overview')) return json(overviewFixture);
    if (path.startsWith('/api/specialists/') && path.includes('/test') && method === 'POST') return json(testResultFixture);
    if (path.startsWith('/api/specialists/') && path.includes('/normalize') && method === 'POST') {
      return json({
        text: 'The A P I responds in two hundred fifty milliseconds.',
        engine: 'deterministic rules + learned lexicon (v1) · 28 entries',
        versionId: 'voice-ai@v1',
        lexiconEntries: 28,
        applied: [{ from: 'API', to: 'A P I' }],
      });
    }
    if (path.startsWith('/api/specialists/') && path.includes('/examples')) {
      if (method === 'POST' || method === 'DELETE') return json({ id: 'ex_1', text: 'example', label: 'calc.evaluate', removed: true });
      return json({
        examples: [{ id: 'ex_1', specialistId: 'coding-ai', text: 'what is 2 + 2', label: 'calc.evaluate', createdAt: '2026-01-01T00:00:00.000Z', source: 'playground' }],
      });
    }
    if (path.startsWith('/api/specialists/') && method === 'PATCH') return json(specialistFixture);
    if (path.startsWith('/api/specialists/')) return json(specialistFixture);
    if (path.startsWith('/api/specialists')) {
      if (method === 'POST') return json({ ...specialistFixture, id: 'custom-ai', name: 'Custom AI', state: 'untrained', activeVersion: null, builtIn: false });
      return json({ specialists: [specialistFixture, untrainedSpecialistFixture] });
    }
    if (path.startsWith('/api/training/methods')) return json({ methods: [trainingMethodFixture, externalMethodFixture] });
    if (path.startsWith('/api/training/datasets')) return json({ datasets: [datasetFixture] });
    if (path.startsWith('/api/training/runs') && method === 'POST') return json({ ...runFixture, id: 'run_new', status: 'queued', progress: 0, metrics: null, promotion: null });
    if (path.startsWith('/api/training/runs/') && method === 'DELETE') return json({ removed: true });
    if (path.startsWith('/api/training/runs/') && path.endsWith('/cancel')) return json({ cancelled: true });
    if (path.startsWith('/api/training/runs/')) return json(runFixture);
    if (path.startsWith('/api/training/runs')) return json({ runs: [runFixture] });
    if (path.startsWith('/api/models/versions/') && path.endsWith('/promote')) return json({ version: specialistFixture.activeVersion, verification: { ok: true, message: 'No regression found.' } });
    if (path.startsWith('/api/models/versions/') && path.endsWith('/reject')) return json({ version: { ...specialistFixture.activeVersion, status: 'rejected' } });
    if (path.startsWith('/api/models/versions/') && method === 'DELETE') return json({ removed: true });
    if (path.startsWith('/api/models/versions/')) {
      return json({ summary: specialistFixture.activeVersion, artifact: { kind: 'task_head_sgd', labels: ['calc.evaluate'] }, scorecards: [scorecardFixture] });
    }
    if (path.startsWith('/api/models/versions')) return json({ versions: [specialistFixture.activeVersion], activeVersionIds: { 'coding-ai': 'coding-ai@v1' } });
    if (path.startsWith('/api/benchmarks/suites')) return json({ suites: [suiteFixture] });
    if (path.startsWith('/api/benchmarks/scorecards/')) return json({ ...scorecardFixture, taskResults: scorecardFixture.taskResults });
    if (path.startsWith('/api/benchmarks/scorecards'))
      return json({
        scorecards: [scorecardFixture, baselineScorecardFixture],
        comparisons: {
          [scorecardFixture.id]: comparisonFixture,
          [baselineScorecardFixture.id]: { ...comparisonFixture, candidateId: baselineScorecardFixture.id, baselineId: null, rows: [], verdict: 'no_baseline' as const, summary: 'No earlier measurement in this suite — this is the first card.' },
        },
      });
    if (path.startsWith('/api/benchmarks/compare')) return json(comparisonFixture);
    if (path.startsWith('/api/benchmarks/run') && method === 'POST') return json(scorecardFixture);
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
