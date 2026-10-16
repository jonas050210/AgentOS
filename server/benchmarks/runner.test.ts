// @vitest-environment node
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/**
 * The benchmark runner turns a deterministic suite plus a model version into a
 * scorecard. These tests use the real suites and a real (tiny) trained head, so
 * they assert measured behaviour rather than mock call patterns: an untrained
 * specialist escalates, a trained one answers locally, and the scorecard says
 * which of the two happened for every task.
 */

let dataDir: string;
let runner: typeof import('./runner.js');
let store: typeof import('./store.js');
let suites: typeof import('./suites.js');
let versions: typeof import('../models/versions.js');
let inference: typeof import('../specialists/inference.js');
let methods: typeof import('../training/methods.js');
let datasets: typeof import('../training/datasets.js');

beforeEach(async () => {
  dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'agentos-bench-'));
  process.env.AGENTOS_DATA_DIR = dataDir;
  process.env.AGENTOS_WORKSPACE_DIR = process.cwd();
  vi.resetModules();
  runner = await import('./runner.js');
  store = await import('./store.js');
  suites = await import('./suites.js');
  versions = await import('../models/versions.js');
  inference = await import('../specialists/inference.js');
  methods = await import('../training/methods.js');
  datasets = await import('../training/datasets.js');
});

afterEach(() => {
  fs.rmSync(dataDir, { recursive: true, force: true });
});

describe('benchmark suites', () => {
  it('covers every specialist category with a scored, deterministic task list', () => {
    const ids = suites.BENCHMARK_SUITES.map((s) => s.id);
    expect(ids).toEqual(
      expect.arrayContaining([
        'coding.routing',
        'coding.expression',
        'research.extraction',
        'research.retrieval',
        'knowledge.retrieval',
        'voice.normalization',
        'general.tool_selection',
      ]),
    );
    for (const suite of suites.BENCHMARK_SUITES) {
      expect(suite.tasks.length).toBe(suite.taskCount);
      expect(suite.tasks.length).toBeGreaterThan(0);
      expect(suite.scorer.length).toBeGreaterThan(0);
      expect(suite.provenance.length).toBeGreaterThan(0);
      for (const task of suite.tasks) {
        expect(task.expected.length).toBeGreaterThan(0);
        expect(['label', 'numeric', 'extraction', 'normalization', 'retrieval']).toContain(task.kind);
      }
    }
  });

  it('keeps the benchmark tasks disjoint from the training split', async () => {
    const coding = datasets.getDataset('coding.tool_routing')!;
    const suite = suites.getSuite('coding.routing')!;
    const trained = new Set(coding.routing!.train.map((i) => i.text));
    for (const task of suite.tasks) expect(trained.has(task.input)).toBe(false);
    // ...and from the calibration slice the head tunes its threshold on.
    const calibration = new Set(coding.routing!.validation.map((i) => i.text));
    for (const task of suite.tasks) expect(calibration.has(task.input)).toBe(false);
  });

  it('refuses to benchmark a specialist outside the suite category', async () => {
    await expect(runner.runBenchmark({ specialistId: 'coding-ai', suiteId: 'voice.normalization' })).rejects.toThrow(
      /does not declare the "Voice/i,
    );
  });
});

describe('benchmark runner', () => {
  it('scores the untrained baseline and labels every task with its answer source', async () => {
    const card = await runner.runBenchmark({ specialistId: 'coding-ai', suiteId: 'coding.routing' });
    expect(card.tasksTotal).toBe(suites.getSuite('coding.routing')!.tasks.length);
    expect(card.tasksPassed + (card.tasksTotal - card.tasksPassed)).toBe(card.tasksTotal);
    expect(card.quality).toBeGreaterThanOrEqual(0);
    expect(card.quality).toBeLessThanOrEqual(1);
    expect(card.reliability).toBe(1);
    expect(card.taskResults).toHaveLength(card.tasksTotal);
    const sources = new Set(card.taskResults.map((r) => r.source));
    // Nothing is trained yet, so no task may claim a trained head answered it.
    expect(sources.has('head')).toBe(false);
    expect(card.taskResults.every((r) => ['policy', 'escalated', 'error'].includes(r.source))).toBe(true);
    expect(card.metrics.headAnswered).toBe(0);
    expect(card.metrics.policyAnswered).toBeGreaterThan(0);
    expect(card.metrics.escalated).toBeGreaterThan(0);
    expect(card.metrics.policyAnswered + card.metrics.escalated).toBe(card.tasksTotal);
  });

  it('measures a real improvement once a head is trained, and beats the rule baseline', async () => {
    const baseline = await runner.runBenchmark({ specialistId: 'coding-ai', suiteId: 'coding.routing' });
    const run = await methods.getMethod('task_head_sgd')!.run({
      runId: 'run_test',
      specialistId: 'coding-ai',
      specialistCategory: 'coding',
      dataset: datasets.getDataset('coding.tool_routing')!,
      hyperparameters: { epochs: 60, learningRate: 0.8 },
      onEpoch: () => {},
      onCheckpoint: () => {},
      log: () => {},
      shouldStop: () => false,
    });
    const version = versions.versionStore.create({
      specialistId: 'coding-ai',
      label: 'test-v1',
      methodId: 'task_head_sgd',
      methodName: 'Task head · softmax + SGD',
      artifactKind: 'task_head_sgd',
      artifact: run.artifact,
      metrics: run.metrics,
      primaryMetric: run.primaryMetric,
      runId: 'run_test',
      datasetId: 'coding.tool_routing',
      datasetSize: 80,
      notes: 'unit test',
      baseModel: run.baseModel,
    });
    versions.versionStore.promote(version.id, 'unit test');

    const card = await runner.runBenchmark({ specialistId: 'coding-ai', suiteId: 'coding.routing' });
    expect(card.metrics.headAnswered).toBeGreaterThan(0);
    expect(card.quality).toBeGreaterThan(baseline.quality);
    expect(card.latencyMsP50).toBeGreaterThan(0);

    // The trained head really is the thing deciding, not a coincidence.
    const decision = inference.decide('coding-ai', 'calculate 12 * (4 + 3)');
    expect(decision.versionId).toBe(version.id);
    expect(decision.label).toBe('calc.evaluate');
  });

  it('flags a version that is worse than the active one instead of praising it', async () => {
    const trained = await methods.getMethod('task_head_sgd')!.run({
      runId: 'run_good',
      specialistId: 'coding-ai',
      specialistCategory: 'coding',
      dataset: datasets.getDataset('coding.tool_routing')!,
      hyperparameters: { epochs: 60, learningRate: 0.8 },
      onEpoch: () => {},
      onCheckpoint: () => {},
      log: () => {},
      shouldStop: () => false,
    });
    const good = versions.versionStore.create({
      specialistId: 'coding-ai',
      label: 'good',
      methodId: 'task_head_sgd',
      methodName: 'Task head · softmax + SGD',
      artifactKind: 'task_head_sgd',
      artifact: trained.artifact,
      metrics: trained.metrics,
      primaryMetric: trained.primaryMetric,
      runId: 'run_good',
      datasetId: 'coding.tool_routing',
      datasetSize: 80,
      notes: 'unit test',
      baseModel: trained.baseModel,
    });
    versions.versionStore.promote(good.id, 'unit test');
    const goodCard = await runner.runBenchmark({ specialistId: 'coding-ai', suiteId: 'coding.routing' });

    // A deliberately crippled "improvement": one label, only the bias term, so it
    // can never beat the trained head. The gate must notice.
    const broken = versions.versionStore.create({
      specialistId: 'coding-ai',
      label: 'broken',
      methodId: 'task_head_sgd',
      methodName: 'Task head · softmax + SGD',
      artifactKind: 'task_head_sgd',
      artifact: {
        kind: 'task_head_sgd',
        formatVersion: 1,
        featureDim: 128,
        labels: ['memory.save'],
        weights: [new Array(128).fill(0)],
        bias: [0],
        trainedAt: new Date().toISOString(),
        trainingSize: 0,
        bestEpoch: 0,
        hyperparameters: { epochs: 1, learningRate: 0.1, l2: 0, batchSize: 1, momentum: 0, featureDim: 128, seed: 1, valFraction: 0.25, labelSmoothing: 0 },
        labelBalance: {},
      },
      metrics: {},
      primaryMetric: 'holdoutAccuracy',
      runId: 'run_broken',
      datasetId: 'coding.tool_routing',
      datasetSize: 0,
      notes: 'unit test',
      baseModel: 'test',
    });
    versions.versionStore.promote(broken.id, 'unit test');
    const candidate = await runner.runBenchmark({ specialistId: 'coding-ai', suiteId: 'coding.routing' });
    const comparison = store.benchmarkStore.buildComparison(candidate, goodCard);
    expect(candidate.quality).toBeLessThan(goodCard.quality);
    expect(comparison.verdict).toBe('regressed');
    expect(comparison.summary).toMatch(/worse than|regression/i);
    expect(comparison.rows.some((row) => row.metric === 'quality' && row.regression)).toBe(true);
  });

  it('calls a slower but clearly better candidate an improvement and names the cost', () => {
    const base = {
      suiteId: 'coding.routing' as const,
      suiteName: 'Coding · decision routing',
      specialistId: 'coding-ai',
      specialistName: 'Coding AI',
      quality: 0.5,
      taskSuccessRate: 0.5,
      tasksPassed: 45,
      tasksTotal: 90,
      latencyMsP50: 0.02,
      latencyMsP95: 0.05,
      latencyMsMean: 0.03,
      reliability: 1,
      failures: 0,
      cpuMs: 5,
      heapDeltaMb: 0,
      metrics: { accuracy: 0.5 },
      taskResults: [],
      createdAt: new Date().toISOString(),
      durationMs: 10,
      engine: 'unit test',
    };
    const baseline = store.benchmarkStore.save({ ...base, id: 'card_unit_base', versionId: 'coding-ai@untrained', versionLabel: 'untrained baseline' });
    const candidate = store.benchmarkStore.save({
      ...base,
      id: 'card_unit_head',
      versionId: 'coding-ai@v1',
      versionLabel: 'v1',
      quality: 0.8,
      taskSuccessRate: 0.8,
      tasksPassed: 72,
      latencyMsP50: 1.2,
      latencyMsP95: 2.4,
      latencyMsMean: 1.4,
      cpuMs: 60,
      engine: 'trained task head (v1)',
    });

    const comparison = store.benchmarkStore.compare(candidate, baseline);
    // 30 points of quality must not be reported as a regression because a learned
    // head is slower than a dictionary lookup — the cost is stated instead.
    expect(comparison.verdict).toBe('improved');
    expect(comparison.tradeoffs.join(' ')).toMatch(/latency/i);
    expect(comparison.summary).toMatch(/improves on untrained baseline/);
    expect(comparison.summary).toMatch(/cost of Latency/);

    // The candidate's own earlier card is the baseline when one exists, so a
    // repeat run is compared against itself rather than against the baseline again.
    const repeat = store.benchmarkStore.save({ ...candidate, id: 'card_unit_repeat' });
    expect(store.benchmarkStore.findBaseline(repeat, repeat.versionId)?.id).toBe(candidate.id);
  });

  it('keeps scorecards queryable per specialist and per suite', async () => {
    await runner.runBenchmark({ specialistId: 'voice-ai', suiteId: 'voice.normalization' });
    const cards = store.benchmarkStore.list({ specialistId: 'voice-ai' });
    expect(cards.length).toBeGreaterThan(0);
    expect(store.benchmarkStore.list({ suiteId: 'voice.normalization' }).length).toBe(cards.length);
    expect(store.benchmarkStore.get(cards[0].id)?.suiteId).toBe('voice.normalization');
  });

  it('never calls a repeat run of the same version an improvement', async () => {
    const first = await runner.runBenchmark({ specialistId: 'coding-ai', suiteId: 'coding.routing' });
    const second = await runner.runBenchmark({ specialistId: 'coding-ai', suiteId: 'coding.routing' });
    const comparison = store.benchmarkStore.compare(second, first);
    // Same model, same suite: the verdict must not read as progress.
    expect(comparison.verdict).not.toBe('improved');
    expect(comparison.summary).toMatch(/repeat run/i);
  });

  it('returns a full per-task breakdown for a suite larger than the old cap', async () => {
    const card = await runner.runBenchmark({ specialistId: 'coding-ai', suiteId: 'coding.routing' });
    expect(card.tasksTotal).toBeGreaterThan(60);
    expect(card.taskResults).toHaveLength(card.tasksTotal);
    expect(card.taskResults.every((result) => result.source !== undefined)).toBe(true);
    expect(card.taskResults.filter((result) => result.correct).length).toBe(card.tasksPassed);
  });
});
