#!/usr/bin/env node
/**
 * End-to-end smoke test: boots the real AgentOS backend against a temporary
 * data directory and exercises the documented API surface. Run with:
 *
 *   npm run smoke
 *
 * Exits non-zero on the first failing check, so it can be used in CI.
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'agentos-smoke-'));
const port = 4500 + Math.floor(Math.random() * 400);
const base = `http://127.0.0.1:${port}`;

let passed = 0;
const failures = [];

function check(name, condition, detail = '') {
  if (condition) {
    passed++;
    console.log(`  \u2713 ${name}`);
  } else {
    failures.push(`${name}${detail ? ` — ${detail}` : ''}`);
    console.log(`  \u2717 ${name}${detail ? ` — ${detail}` : ''}`);
  }
}

async function api(pathname, init) {
  const res = await fetch(`${base}${pathname}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON response */
  }
  return { status: res.status, body };
}

const server = spawn('node_modules/.bin/tsx', ['server/index.ts'], {
  cwd: root,
  env: {
    ...process.env,
    AGENTOS_PORT: String(port),
    AGENTOS_HOST: '127.0.0.1',
    AGENTOS_DATA_DIR: dataDir,
    AGENTOS_WORKSPACE_DIR: root,
  },
  stdio: ['ignore', 'pipe', 'pipe'],
});

server.stdout.on('data', (d) => process.env.SMOKE_VERBOSE && process.stdout.write(`[server] ${d}`));
server.stderr.on('data', (d) => process.env.SMOKE_VERBOSE && process.stderr.write(`[server] ${d}`));

async function waitForServer(timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${base}/api/health`);
      if (res.ok) return true;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

function cleanup() {
  server.kill('SIGTERM');
  fs.rmSync(dataDir, { recursive: true, force: true });
}

try {
  console.log(`AgentOS smoke test · port ${port} · data ${dataDir}\n`);
  const up = await waitForServer();
  if (!up) throw new Error('backend did not become healthy in time');

  console.log('health & status');
  const health = await api('/api/health');
  check('GET /api/health returns 200', health.status === 200);
  check('health reports components', Array.isArray(health.body?.components) && health.body.components.length > 0);
  check('health status is honest about missing models', ['healthy', 'degraded'].includes(health.body?.status));

  console.log('\nmodels & capabilities');
  const providers = await api('/api/providers');
  check('GET /api/providers lists the built-in adapter', providers.body?.some?.((p) => p.id === 'builtin' && p.available));
  const models = await api('/api/models');
  check('GET /api/models returns a catalogue', Array.isArray(models.body?.models));
  check('unavailable models are marked, not hidden', models.body.models.every((m) => typeof m.status === 'string'));
  const caps = await api('/api/capabilities');
  check('four product capabilities are defined', ['coding.assistant', 'research.assistant', 'knowledge.manager', 'general.assistant'].every((id) => caps.body.capabilities.some((c) => c.id === id)));
  const tools = await api('/api/tools');
  check('tools expose permission requirements', tools.body.tools.every((t) => typeof t.permission === 'string'));

  console.log('\nchat & orchestration');
  const chat = await api('/api/chat', { method: 'POST', body: JSON.stringify({ message: 'calculate 6 * (2 + 5)' }) });
  check('POST /api/chat answers', chat.status === 200 && typeof chat.body?.message?.content === 'string');
  check('chat response contains a routing trace', Array.isArray(chat.body?.trace) && chat.body.trace.length > 0);
  check('tool planning executed the calculator', chat.body.trace.some((t) => t.step === 'tool:calc.evaluate'));
  const routed = await api('/api/chat', { method: 'POST', body: JSON.stringify({ message: 'refactor this typescript function' }) });
  check('coding requests route to the coding capability', routed.body?.message?.meta?.capabilityId === 'coding.assistant');
  const bad = await api('/api/chat', { method: 'POST', body: JSON.stringify({ message: '' }) });
  check('empty chat message is rejected with 400', bad.status === 400 && bad.body.code === 'VALIDATION_ERROR');

  console.log('\nspecialists, training & benchmarks');
  const overview = await api('/api/platform/overview');
  const specialists = overview.body?.specialists ?? [];
  check('specialists are registered with a state', specialists.length >= 5 && specialists.every((s) => typeof s.state === 'string'));
  check('an untrained specialist is reported as untrained, not scored', specialists.every((s) => (s.activeVersion ? s.state !== 'untrained' : s.benchmarkScore === null)));
  const methods = await api('/api/training/methods');
  const adapter = methods.body?.methods?.find((m) => m.id === 'llm_adapter_finetune');
  check('the external adapter method is listed as unavailable, not hidden', adapter && adapter.available === false && adapter.runtime === 'external');
  const blocked = await api('/api/training/runs', {
    method: 'POST',
    body: JSON.stringify({ specialistId: 'coding-ai', methodId: 'llm_adapter_finetune', datasetId: 'coding.tool_routing' }),
  });
  check('POST /api/training/runs refuses an external method with 409', blocked.status === 409 && blocked.body.code === 'METHOD_UNAVAILABLE');
  const mismatched = await api('/api/training/runs', {
    method: 'POST',
    body: JSON.stringify({ specialistId: 'coding-ai', methodId: 'retrieval_index', datasetId: 'coding.tool_routing' }),
  });
  check('a method that does not fit the specialist is refused', mismatched.status === 400 && mismatched.body.code === 'METHOD_NOT_APPLICABLE');

  // Real training: a small head over real examples, benchmarked against the
  // untrained baseline and only promoted if the measurement supports it.
  const started = await api('/api/training/runs', {
    method: 'POST',
    body: JSON.stringify({
      specialistId: 'coding-ai',
      methodId: 'task_head_sgd',
      datasetId: 'coding.tool_routing',
      hyperparameters: { epochs: 12 },
      runBenchmarkAfter: true,
      autoPromote: true,
    }),
  });
  check('a training run starts and returns an id', started.status === 202 && typeof started.body?.id === 'string');
  let run = started.body;
  const deadline = Date.now() + 90000;
  while (run && ['queued', 'running'].includes(run.status) && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 400));
    run = (await api(`/api/training/runs/${started.body.id}`)).body;
  }
  check('the training run completes', run?.status === 'completed', run?.error ?? '');
  check('the run records real training metrics', typeof run?.metrics?.holdoutAccuracy === 'number' && run.metrics.epochsRun > 0);
  check('the run was benchmarked after training', typeof run?.metrics?.benchmarkQuality === 'number' && typeof run?.scorecardId === 'string');
  check('promotion carries a reason', typeof run?.promotion?.action === 'string' && typeof run?.promotion?.reason === 'string');
  const suffix = String(run?.versionId ?? '').split('@')[1];
  const artifactPath = path.join(dataDir, 'models', 'coding-ai', `${suffix}.json`);
  check('the trained artefact is written to disk', Boolean(suffix) && fs.existsSync(artifactPath));
  const stored = fs.existsSync(artifactPath) ? JSON.parse(fs.readFileSync(artifactPath, 'utf-8')) : null;
  check('the stored artefact contains learned weights, not seed values', Boolean(stored && Array.isArray(stored.weights) && stored.weights.length > 0 && stored.trainingSize > 0));

  const versions = await api('/api/models/versions?specialistId=coding-ai');
  const active = versions.body?.versions?.find((v) => v.status === 'active');
  check('exactly one version is active per specialist', versions.body.versions.filter((v) => v.status === 'active').length === 1);
  check('the active version carries training metrics', Boolean(active && Object.keys(active.metrics ?? {}).length > 0));

  const scorecards = await api('/api/benchmarks/scorecards?limit=20');
  const cards = scorecards.body?.scorecards ?? [];
  const baseline = cards.find((c) => c.versionId.endsWith('@untrained'));
  const measured = cards.find((c) => !c.versionId.endsWith('@untrained'));
  check('benchmarks keep an untrained baseline card per suite', Boolean(baseline && baseline.quality >= 0));
  check('a trained card is measured against that baseline', Boolean(measured && scorecards.body.comparisons[measured.id]?.baselineId));
  check('scorecards record quality, reliability and latency', cards.every((c) => typeof c.quality === 'number' && typeof c.reliability === 'number' && typeof c.latencyMsP50 === 'number'));

  const wrongSuite = await api('/api/benchmarks/run', { method: 'POST', body: JSON.stringify({ specialistId: 'coding-ai', suiteId: 'voice.normalization' }) });
  check('a suite cannot be run against the wrong specialist category', wrongSuite.status === 400 && wrongSuite.body.code === 'SUITE_MISMATCH');

  const normalize = await api('/api/specialists/voice-ai/normalize', { method: 'POST', body: JSON.stringify({ text: 'The build takes 42 sec and costs $15.' }) });
  check(
    'voice normalisation works before any training, and says so',
    /deterministic rules/.test(normalize.body?.engine ?? '') && /forty-two/.test(normalize.body?.text ?? '') && /fifteen dollars/.test(normalize.body?.text ?? ''),
  );

  const untrained = await api('/api/specialists/research-ai/test', { method: 'POST', body: JSON.stringify({ input: 'read https://example.com/doc' }) });
  check('an untrained specialist labels its engine instead of faking a model', /rule planner|no trained version/.test(untrained.body?.engine ?? ''));

  console.log('\npermissions & tool sandbox');
  const denied = await api('/api/tools/http.fetch/execute', { method: 'POST', body: JSON.stringify({ input: { url: 'https://example.com' } }) });
  check('network tool is denied while the permission is off', denied.body?.success === false && /Permission "network"/.test(denied.body.error));
  const traversal = await api('/api/tools/workspace.read/execute', { method: 'POST', body: JSON.stringify({ input: { path: '../../etc/passwd' } }) });
  check('path traversal outside the workspace is blocked', traversal.body?.success === false);
  const calc = await api('/api/tools/calc.evaluate/execute', { method: 'POST', body: JSON.stringify({ input: { expression: '40 + 2' } }) });
  check('allowed tool executes and returns structured output', calc.body?.success === true && calc.body.output.result === 42);
  const invalid = await api('/api/tools/calc.evaluate/execute', { method: 'POST', body: JSON.stringify({ input: {} }) });
  check('missing required input is reported as a validation error', invalid.body?.success === false);

  console.log('\nsettings persistence & secrets');
  const saved = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ theme: 'light', providers: { openai: { apiKey: 'sk-smoke-secret', baseUrl: 'https://api.example.test/v1', model: 'smoke-model' } } }) });
  check('settings update accepted', saved.status === 200 && saved.body.theme === 'light');
  check('API key is never echoed back', JSON.stringify(saved.body).includes('sk-smoke-secret') === false);
  check('API key presence is reported as a flag', saved.body.providers.openai.hasApiKey === true);
  const onDisk = JSON.parse(fs.readFileSync(path.join(dataDir, 'settings.json'), 'utf-8'));
  check('settings are written to disk', onDisk.theme === 'light' && onDisk.providers.openai.apiKey === 'sk-smoke-secret');
  const reloaded = await api('/api/settings');
  check('settings survive a re-read (same process)', reloaded.body.theme === 'light');
  const activity = await api('/api/activity?limit=200');
  check('logs never contain the raw secret', JSON.stringify(activity.body).includes('sk-smoke-secret') === false);

  console.log('\nmemory & activity');
  const mem = await api('/api/memory', { method: 'POST', body: JSON.stringify({ title: 'Smoke note', content: 'AgentOS smoke test entry', tags: ['smoke'] }) });
  check('memory entry created', mem.status === 201 && typeof mem.body.id === 'string');
  const search = await api('/api/memory?q=smoke');
  check('memory search finds the entry', search.body.records.some((r) => r.id === mem.body.id));
  const del = await api(`/api/memory/${mem.body.id}`, { method: 'DELETE' });
  check('memory entry deleted', del.status === 200);

  console.log('\nspeech & error handling');
  const speech = await api('/api/speech/engines');
  check('speech engines are reported with availability', speech.body.engines.some((e) => e.location === 'client'));
  check('server-side speech engines are marked unavailable', speech.body.engines.filter((e) => e.location === 'server').every((e) => e.available === false));
  const notFound = await api('/api/definitely-not-here');
  check('unknown API routes return a structured 404', notFound.status === 404 && notFound.body.code === 'NOT_FOUND');

  console.log('\nSSE activity stream');
  const streamOk = await new Promise((resolve) => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
      resolve(false);
    }, 4000);
    fetch(`${base}/api/activity/stream`, { signal: controller.signal })
      .then((res) => {
        const reader = res.body.getReader();
        reader.read().then(({ value }) => {
          clearTimeout(timer);
          controller.abort();
          resolve(Boolean(value && Buffer.from(value).toString().includes('event: ready')));
        });
      })
      .catch(() => {
        clearTimeout(timer);
        resolve(false);
      });
  });
  check('activity stream opens with a ready event', streamOk);

  console.log(`\n${passed} checks passed, ${failures.length} failed`);
  if (failures.length) {
    console.log('\nFailures:');
    for (const f of failures) console.log(` - ${f}`);
  }
  cleanup();
  process.exit(failures.length ? 1 : 0);
} catch (err) {
  console.error('Smoke test crashed:', err instanceof Error ? err.message : err);
  cleanup();
  process.exit(1);
}
