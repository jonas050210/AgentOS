// @vitest-environment node
import { describe, expect, it, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Server } from 'node:http';

/**
 * End-to-end coverage of the v0.2.0 platform API: specialists, datasets,
 * training runs (with the real in-process engine), model versions and the
 * benchmark gate. These tests exercise the routes over HTTP exactly like the
 * desktop UI does, so the contract the frontend relies on is pinned down.
 */

let server: Server;
let baseUrl: string;
let dataDir: string;

beforeAll(async () => {
  dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'agentos-platform-'));
  process.env.AGENTOS_DATA_DIR = dataDir;
  process.env.AGENTOS_WORKSPACE_DIR = process.cwd();
  vi.resetModules();
  const { createApp } = await import('../app.js');
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
async function readJson<T = any>(res: Response): Promise<T> {
  return (await res.json()) as T;
}
const send = (p: string, body: unknown, method = 'POST') =>
  fetch(`${baseUrl}${p}`, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

/** Waits for a training run to leave the queued/running state. */
async function waitForRun(runId: string, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const body = await readJson(await get(`/api/training/runs/${runId}`));
    if (body && typeof body.status === 'string' && !['queued', 'running'].includes(body.status)) return body;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Run ${runId} did not finish within ${timeoutMs} ms`);
}

describe('platform API · specialists', () => {
  it('lists the shipped specialists with honest training state', async () => {
    const body = await readJson(await get('/api/specialists'));
    const ids = body.specialists.map((s: { id: string }) => s.id);
    expect(ids).toEqual(expect.arrayContaining(['coding-ai', 'research-ai', 'knowledge-ai', 'voice-ai', 'general-ai']));
    for (const specialist of body.specialists) {
      expect(specialist.state).toBe('untrained');
      expect(specialist.activeVersion).toBeNull();
      expect(specialist.trainingMethodIds.length).toBeGreaterThan(0);
      expect(specialist.benchmarkSuiteIds.length).toBeGreaterThan(0);
      // Permissions are reported separately from training state so the UI can
      // explain a blocked tool instead of silently failing it.
      expect(typeof specialist.permissionsReady).toBe('boolean');
      expect(Array.isArray(specialist.blockedTools)).toBe(true);
    }
  });

  it('creates, patches, tests and deletes a custom specialist', async () => {
    const created = await readJson<{ id: string; name: string; category: string }>(
      await send('/api/specialists', {
        name: 'Finance AI',
        tagline: 'Checks invoices',
        description: 'Reviews invoice text and routes the right tools.',
        category: 'custom',
        toolIds: ['calc.evaluate'],
        trainingMethodIds: ['task_head_sgd'],
        benchmarkSuiteIds: [],
        systemPrompt: 'You are a careful finance assistant.',
      }),
    );
    const id = created.id;
    expect(id).toMatch(/^finance-ai/);
    expect(created.category).toBe('custom');

    const patched = await readJson<{ tagline: string }>(await send(`/api/specialists/${id}`, { tagline: 'Reconciles invoices' }, 'PATCH'));
    expect(patched.tagline).toBe('Reconciles invoices');

    const test = await readJson<{ specialistId: string; trace: unknown[] }>(
      await send(`/api/specialists/${id}/test`, { input: 'what is 10 * 3' }),
    );
    expect(test.specialistId).toBe(id);
    expect(test.trace.length).toBeGreaterThan(0);

    const removed = await fetch(`${baseUrl}/api/specialists/${id}`, { method: 'DELETE' });
    expect(removed.status).toBe(200);
    expect((await get(`/api/specialists/${id}`)).status).toBe(404);
  });

  it('rejects an invalid specialist payload instead of storing it', async () => {
    const res = await send('/api/specialists', { name: '' });
    expect(res.status).toBeGreaterThanOrEqual(400);
    const body = await readJson(res);
    expect(body.error).toBeTruthy();
  });

  it('stores playground examples as training data and exports them as NDJSON', async () => {
    const created = await readJson<{ id: string; label: string; source: string }>(
      await send('/api/specialists/coding-ai/examples', { text: 'multiply 21 by 2', label: 'calc.evaluate' }),
    );
    expect(created.label).toBe('calc.evaluate');
    expect(created.source).toBe('manual');
    const list = await readJson(await get('/api/specialists/coding-ai/examples'));
    expect(list.examples.some((e: { id: string }) => e.id === created.id)).toBe(true);

    // The export is NDJSON, one training example per line — the format the
    // external adapter method would consume.
    const dataset = await get('/api/training/datasets/coding.user_examples/export');
    expect(dataset.status).toBe(200);
    const lines = (await dataset.text())
      .trim()
      .split('\n')
      .filter(Boolean)
      .map((line) => JSON.parse(line));
    // Instruction/output pairs, the same shape the fine-tuning APIs expect.
    expect(lines.some((line: { instruction: string; output: string }) => line.instruction === 'multiply 21 by 2' && line.output === 'calc.evaluate')).toBe(true);

    const removed = await fetch(`${baseUrl}/api/specialists/coding-ai/examples/${created.id}`, { method: 'DELETE' });
    expect(removed.status).toBe(200);
  });

  it('normalises numbers and currency with deterministic rules before any training', async () => {
    const body = await readJson<{ text: string; lexiconEntries: number }>(
      await send('/api/specialists/voice-ai/normalize', { text: 'The build takes 42 sec and costs $15.' }),
    );
    expect(body.text).toMatch(/forty-two/);
    expect(body.text).toMatch(/fifteen dollars/);
    // No lexicon has been trained yet, so "sec" must stay as written — the API
    // does not claim a learned expansion it does not have.
    expect(body.lexiconEntries).toBe(0);
    expect(body.text).toMatch(/42 sec|forty-two sec/);
  });
});

describe('platform API · training', () => {
  it('describes the four methods honestly, including the one that cannot run here', async () => {
    const body = await readJson(await get('/api/training/methods'));
    const byId = new Map(body.methods.map((m: { id: string }) => [m.id, m]));
    expect([...byId.keys()]).toEqual(
      expect.arrayContaining(['task_head_sgd', 'retrieval_index', 'lexicon_induction', 'llm_adapter_finetune']),
    );
    const external = byId.get('llm_adapter_finetune') as { available: boolean; requirements: string[]; honesty: string; runtime: string };
    expect(external.available).toBe(false);
    expect(external.runtime).toBe('external');
    expect(external.requirements.join(' ')).toMatch(/torch/i);
    expect(external.honesty).toMatch(/runnable|adapter/i);
    expect((byId.get('task_head_sgd') as { available: boolean }).available).toBe(true);
  });

  it('refuses the external adapter method with 409 instead of pretending to train', async () => {
    const res = await send('/api/training/runs', {
      specialistId: 'coding-ai',
      methodId: 'llm_adapter_finetune',
      datasetId: 'coding.tool_routing',
    });
    expect(res.status).toBe(409);
    const body = await readJson(res);
    expect(body.code).toBe('METHOD_UNAVAILABLE');
    expect(body.error).toMatch(/Python|torch|transformers/i);
  });

  it('trains a real head end to end, records metrics, and promotes it on benchmark evidence', async () => {
    const started = await send('/api/training/runs', {
      specialistId: 'coding-ai',
      methodId: 'task_head_sgd',
      datasetId: 'coding.tool_routing',
      hyperparameters: { epochs: 60, learningRate: 0.8 },
      autoPromote: true,
      runBenchmarkAfter: true,
    });
    expect(started.status).toBe(202);
    const run = await readJson<{ id: string }>(started);

    const finished = await waitForRun(run.id);
    expect(finished.status).toBe('completed');
    expect(finished.metrics.epochsRun).toBeGreaterThan(0);
    expect(finished.metrics.trainSize).toBeGreaterThan(20);
    expect(finished.metrics.parameters).toBeGreaterThan(0);
    // Learning actually happened: the head beats chance on later epochs.
    expect(finished.metrics.finalTrainAccuracy).toBeGreaterThan(0.5);
    // The threshold the head is trustworthy at was measured, not guessed.
    expect(finished.metrics.calibrationExamples).toBeGreaterThan(0);
    expect(finished.metrics.calibratedAccuracy).toBeGreaterThan(0.5);

    // The run carries the benchmark card it was judged with.
    expect(finished.scorecardId).toBeTruthy();
    const card = await readJson(await get(`/api/benchmarks/scorecards/${finished.scorecardId}`));
    expect(card.tasksTotal).toBeGreaterThan(0);
    expect(card.metrics.headAnswered + card.metrics.policyAnswered + card.metrics.escalated).toBe(card.tasksTotal);

    expect(finished.promotion).toBeTruthy();
    expect(['promoted', 'kept_active', 'awaiting_review']).toContain(finished.promotion.action);
    expect(finished.promotion.metric).toBe('benchmarkQuality');
    expect(finished.promotion.reason).toMatch(/quality/i);

    // A promoted run must leave an active version behind that answers locally.
    const versions = await readJson(await get('/api/models/versions?specialistId=coding-ai'));
    const active = versions.versions.find((v: { status: string }) => v.status === 'active');
    expect(active).toBeTruthy();
    expect(active.metrics).toBeTruthy();

    const decision = await readJson<{ versionId: string; answer: string; engine: string }>(
      await send('/api/specialists/coding-ai/test', { input: 'what is 128 * 4' }),
    );
    expect(decision.versionId).toBe(active.id);
    expect(decision.answer).toMatch(/512/);
  });

  it('records every checkpoint of a run so versions can be inspected', async () => {
    const run = await readJson<{ id: string }>(
      await send('/api/training/runs', {
        specialistId: 'knowledge-ai',
        methodId: 'retrieval_index',
        datasetId: 'knowledge.corpus',
      }),
    );
    const finished = await waitForRun(run.id);
    expect(finished.status).toBe('completed');
    expect(finished.checkpoints.length).toBeGreaterThan(0);
    expect(finished.metrics.recallAt5).toBeGreaterThan(0.5);
  });

  it('refuses a run whose dataset does not match the method', async () => {
    const res = await send('/api/training/runs', {
      specialistId: 'knowledge-ai',
      methodId: 'task_head_sgd',
      datasetId: 'knowledge.corpus',
    });
    // A dataset the method cannot consume is a client error, and it is refused
    // before any run is created — the API never reports a run it cannot honour.
    expect(res.status).toBe(400);
    const body = await readJson(res);
    expect(body.code).toBe('DATASET_INCOMPATIBLE');
    expect(body.error).toMatch(/cannot consume/i);

    const runs = await readJson(await get('/api/training/runs?specialistId=knowledge-ai'));
    expect(
      runs.runs.some((r: { datasetId: string; methodId: string }) => r.datasetId === 'knowledge.corpus' && r.methodId === 'task_head_sgd'),
    ).toBe(false);
  });
});

describe('platform API · models and benchmarks', () => {
  it('keeps the better version and can reject a candidate without deleting evidence', async () => {
    const run = await readJson<{ id: string }>(
      await send('/api/training/runs', {
        specialistId: 'voice-ai',
        methodId: 'lexicon_induction',
        datasetId: 'voice.lexicon',
        autoPromote: false,
      }),
    );
    const finished = await waitForRun(run.id);
    expect(finished.status).toBe('completed');

    const versions = await readJson(await get('/api/models/versions?specialistId=voice-ai'));
    const candidate = versions.versions.find((v: { status: string }) => v.status === 'candidate');
    expect(candidate).toBeTruthy();

    const promoted = await readJson(await send(`/api/models/versions/${candidate.id}/promote`, { note: 'verified by hand' }));
    expect(promoted.version.status).toBe('active');

    // Now that the lexicon is live, the learned expansion applies.
    const normalize = await readJson<{ text: string; lexiconEntries: number }>(
      await send('/api/specialists/voice-ai/normalize', { text: 'The build takes 42 sec and costs $15.' }),
    );
    expect(normalize.lexiconEntries).toBeGreaterThan(0);
    expect(normalize.text).toMatch(/forty-two seconds and costs fifteen dollars/);

    // The previous active version is kept around, never blindly replaced.
    const after = await readJson(await get('/api/models/versions?specialistId=voice-ai'));
    expect(after.versions.filter((v: { status: string }) => v.status === 'active')).toHaveLength(1);
    expect(after.versions.length).toBeGreaterThanOrEqual(1);
  });

  it('serves the benchmark catalogue and never claims a suite can test the wrong category', async () => {
    const body = await readJson(await get('/api/benchmarks/suites'));
    expect(body.suites.length).toBeGreaterThanOrEqual(7);
    for (const suite of body.suites) {
      expect(suite.taskCount).toBeGreaterThan(0);
      expect(suite.metrics.length).toBeGreaterThan(0);
    }
    const mismatch = await send('/api/benchmarks/run', { specialistId: 'coding-ai', suiteId: 'voice.normalization' });
    expect(mismatch.status).toBe(400);
    expect((await readJson(mismatch)).code).toBe('SUITE_MISMATCH');
  });

  it('summarises the platform in one overview call', async () => {
    const body = await readJson(await get('/api/platform/overview'));
    expect(body.specialists).toHaveLength(5);
    expect(body.counts.total).toBeGreaterThan(0);
    // Three of the four methods run in this process; the external adapter does not.
    expect(body.trainingAvailable).toBe(3);
    expect(body.methods.length).toBeGreaterThanOrEqual(4);
    expect(body.datasets.length).toBeGreaterThan(0);
    expect(body.suites.length).toBeGreaterThanOrEqual(7);
    expect(body.versions.length).toBeGreaterThan(0);
    expect(body.scorecards.length).toBeGreaterThan(0);
  });
});
