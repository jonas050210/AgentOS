import { BenchmarkRunRequest, BenchmarkScorecard, BenchmarkTaskResult, BenchmarkSuiteId } from '../../shared/types.js';
import { logger } from '../logger.js';
import { Bm25Artifact, Bm25Index, evaluateRetrieval } from '../ml/retrieval.js';
import { versionStore } from '../models/versions.js';
import { decide, extractExpression, extractUrlTarget, normalizeSpeechFor } from '../specialists/inference.js';
import { getSpecialist } from '../specialists/registry.js';
import { evaluateArithmetic } from '../tools/builtin.js';
import { BENCHMARK_SUITES, BenchmarkSuite, BenchmarkTask, getSuite } from './suites.js';
import { BenchmarkStore, benchmarkStore } from './store.js';

/**
 * The benchmark runner measures a specialist on a fixed held-out suite and
 * records a scorecard. Two properties matter:
 *
 * 1. it answers through exactly the same decision path the app uses, so the
 *    score describes the product and not a private test harness;
 * 2. it never calls a model provider. What is measured is what runs locally —
 *    quality of the trained artefacts, and how often the pipeline has to
 *    escalate because nothing local can handle the request.
 */

export class BenchmarkError extends Error {
  constructor(
    message: string,
    readonly statusCode: number,
    readonly code: string,
  ) {
    super(message);
    this.name = 'BenchmarkError';
  }
}

interface TaskOutcome {
  actual: string;
  correct: boolean;
  error: string | null;
  source: BenchmarkTaskResult['source'];
}

function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return Number(sorted[index].toFixed(4));
}

function mean(values: number[]): number {
  if (values.length === 0) return 0;
  return Number((values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(4));
}

function normalizeWhitespace(text: string): string {
  return text.replace(/\s+/g, ' ').trim();
}

/** Answers one task through the specialist's real pipeline (no providers). */
async function answerTask(specialistId: string, task: BenchmarkTask, versionId?: string): Promise<TaskOutcome> {
  try {
    if (task.kind === 'label') {
      const decision = decide(specialistId, task.input, { versionId });
      if (decision.escalated || !decision.label) {
        return { actual: `escalated (${decision.reason ?? 'no local decision'})`, correct: false, error: null, source: 'escalated' };
      }
      return {
        actual: decision.label,
        correct: decision.label === task.expected,
        error: null,
        source: decision.source === 'model' ? 'head' : 'policy',
      };
    }

    if (task.kind === 'numeric') {
      const decision = decide(specialistId, task.input, { versionId });
      if (decision.escalated) {
        return { actual: `escalated (${decision.reason ?? 'no local decision'})`, correct: false, error: null, source: 'escalated' };
      }
      if (decision.label !== 'calc.evaluate') {
        return { actual: `routed to ${decision.label}`, correct: false, error: null, source: decision.source === 'model' ? 'head' : 'policy' };
      }
      const expression = extractExpression(task.input);
      if (!expression) return { actual: 'no expression found', correct: false, error: null, source: 'error' };
      const value = evaluateArithmetic(expression);
      const actual = String(value);
      const correct = Math.abs(value - Number(task.expected)) < 1e-6;
      return { actual, correct, error: null, source: decision.source === 'model' ? 'head' : 'policy' };
    }

    if (task.kind === 'extraction') {
      const decision = decide(specialistId, task.input, { versionId });
      if (decision.escalated) {
        return { actual: `escalated (${decision.reason ?? 'no local decision'})`, correct: false, error: null, source: 'escalated' };
      }
      // The learned part is the route; extraction itself is deterministic.
      const target = extractUrlTarget(task.input);
      const correct = target === task.expected && decision.label === 'extract.url';
      return {
        actual: `${target ?? 'no url found'}${decision.label === 'extract.url' ? '' : ` (routed to ${decision.label})`}`,
        correct,
        error: null,
        source: decision.source === 'model' ? 'head' : 'policy',
      };
    }

    if (task.kind === 'normalization') {
      const normalized = normalizeSpeechFor(specialistId, task.input, { versionId });
      const actual = normalizeWhitespace(normalized.text);
      return {
        actual,
        correct: actual === normalizeWhitespace(task.expected),
        error: null,
        source: normalized.lexiconEntries > 0 ? 'head' : 'policy',
      };
    }

    if (task.kind === 'retrieval') {
      const relevant = (task.meta?.relevant as string[] | undefined) ?? [];
      const artifact = resolveRetrievalArtifact(specialistId, versionId);
      if (!artifact) {
        return { actual: 'no index trained', correct: false, error: null, source: 'escalated' };
      }
      const index = Bm25Index.fromArtifact(artifact);
      const hits = index.query(task.input, 5);
      const hitIds = hits.map((hit) => hit.id);
      const recalled = relevant.filter((id) => hitIds.includes(id));
      return {
        actual: hitIds.join(', ') || 'no hits',
        correct: recalled.length === relevant.length && relevant.length > 0,
        error: null,
        source: 'head',
      };
    }

    return { actual: 'unsupported task kind', correct: false, error: 'unsupported task kind', source: 'error' };
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    logger.warn('benchmarks', `Task ${task.id} failed: ${message}`);
    return { actual: 'error', correct: false, error: message, source: 'error' };
  }
}

function resolveRetrievalArtifact(specialistId: string, versionId?: string): Bm25Artifact | null {
  const version = versionId ? versionStore.get(versionId) : versionStore.activeVersion(specialistId);
  if (!version || version.artifactKind !== 'retrieval_index') return null;
  const artifact = versionStore.artifact(version.id);
  if (!artifact || (artifact as { kind?: string }).kind !== 'retrieval_index') return null;
  return artifact as Bm25Artifact;
}

/** Suite-level score for suites whose metric is not per-task accuracy. */
async function suiteLevelScore(
  suite: BenchmarkSuite,
  specialistId: string,
  versionId?: string,
): Promise<{ quality: number; metrics: Record<string, number> } | null> {
  if (!suite.id.endsWith('retrieval')) return null;
  const artifact = resolveRetrievalArtifact(specialistId, versionId);
  if (!artifact) {
    return { quality: 0, metrics: { recallAt5: 0, mrr: 0, precisionAt5: 0, hitRate: 0 } };
  }
  const index = Bm25Index.fromArtifact(artifact);
  const queries = suite.tasks
    .filter((task) => task.kind === 'retrieval')
    .map((task) => ({ query: task.input, relevant: (task.meta?.relevant as string[] | undefined) ?? [] }));
  const result = evaluateRetrieval(index, queries, 5);
  return {
    quality: Number(result.recallAtK.toFixed(4)),
    metrics: {
      recallAt5: Number(result.recallAtK.toFixed(4)),
      mrr: Number(result.meanReciprocalRank.toFixed(4)),
      precisionAt5: Number(result.precisionAtK.toFixed(4)),
      hitRate: Number(result.hitRate.toFixed(4)),
    },
  };
}

/**
 * Runs one suite against one model version.
 *
 * `triggeredBy` only affects logging: the measurement itself is identical
 * whether a user or the training engine started it, which is what makes the
 * post-training number comparable with a manual one.
 */
export async function runBenchmark(
  request: BenchmarkRunRequest,
  triggeredBy: 'user' | 'training' | 'system' = 'user',
  store: BenchmarkStore = benchmarkStore,
): Promise<BenchmarkScorecard> {
  const suite = getSuite(request.suiteId);
  if (!suite) throw new BenchmarkError(`Unknown benchmark suite "${request.suiteId}"`, 404, 'NOT_FOUND');
  const specialist = getSpecialist(request.specialistId);
  if (!specialist) throw new BenchmarkError(`Unknown specialist "${request.specialistId}"`, 404, 'NOT_FOUND');
  if (!specialist.benchmarkSuiteIds.includes(suite.id)) {
    throw new BenchmarkError(
      `${specialist.name} does not declare the "${suite.name}" suite. Benchmark a ${suite.specialistCategory} specialist instead.`,
      400,
      'SUITE_MISMATCH',
    );
  }

  const version = request.versionId ? versionStore.get(request.versionId) : versionStore.activeVersion(specialist.id);
  if (request.versionId && !version) throw new BenchmarkError(`Unknown model version "${request.versionId}"`, 404, 'NOT_FOUND');
  const repeats = Math.max(1, Math.min(3, Math.round(request.repeats ?? 1)));
  const startedAt = Date.now();
  const cpuBefore = process.cpuUsage();
  const heapBefore = process.memoryUsage().heapUsed;

  const taskResults: BenchmarkTaskResult[] = [];
  const latencies: number[] = [];
  let failures = 0;
  let correctCount = 0;

  for (let repeat = 0; repeat < repeats; repeat++) {
    let repeatCorrect = 0;
    for (const task of suite.tasks) {
      const taskStart = performance.now();
      const outcome = await answerTask(specialist.id, task, version?.id);
      const latencyMs = Number((performance.now() - taskStart).toFixed(4));
      latencies.push(latencyMs);
      if (outcome.error) failures++;
      if (outcome.correct) {
        correctCount++;
        repeatCorrect++;
      }
      if (repeat === 0) {
        taskResults.push({
          taskId: task.id,
          inputPreview: task.input.slice(0, 120),
          expected: task.expected,
          actual: outcome.actual.slice(0, 200),
          correct: outcome.correct,
          latencyMs,
          error: outcome.error,
          source: outcome.source,
        });
      }
    }
    logger.debug('benchmarks', `Repeat ${repeat + 1}/${repeats}: ${repeatCorrect}/${suite.tasks.length} passed`);
  }

  const level = await suiteLevelScore(suite, specialist.id, version?.id);
  const taskAccuracy = suite.tasks.length > 0 ? repeatCorrectTotal(taskResults) / taskResults.length : 0;
  const quality = level ? level.quality : Number(taskAccuracy.toFixed(4));
  const cpuMs = Number(((process.cpuUsage(cpuBefore).user + process.cpuUsage(cpuBefore).system) / 1000).toFixed(3));
  const heapDeltaMb = Number(((process.memoryUsage().heapUsed - heapBefore) / 1024 / 1024).toFixed(3));
  const reliability = suite.tasks.length * repeats === 0 ? 1 : Number((1 - failures / (suite.tasks.length * repeats)).toFixed(4));

  const metrics: Record<string, number> = {
    ...(level?.metrics ?? {}),
    taskSuccessRate: Number((correctCount / (suite.tasks.length * repeats)).toFixed(4)),
    reliability,
    consistency: Number(taskAccuracy.toFixed(4)),
    headAnswered: taskResults.filter((r) => r.source === 'head').length,
    policyAnswered: taskResults.filter((r) => r.source === 'policy').length,
    escalated: taskResults.filter((r) => r.source === 'escalated').length,
  };

  const engine = describeEngine(specialist.id, version?.id, version?.label);
  const card: BenchmarkScorecard = {
    id: `card_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
    suiteId: suite.id,
    suiteName: suite.name,
    specialistId: specialist.id,
    specialistName: specialist.name,
    versionId: version?.id ?? 'untrained',
    versionLabel: version?.label ?? 'untrained baseline',
    engine,
    quality,
    taskSuccessRate: metrics.taskSuccessRate,
    tasksPassed: correctCount,
    tasksTotal: suite.tasks.length * repeats,
    latencyMsP50: percentile(latencies, 50),
    latencyMsP95: percentile(latencies, 95),
    latencyMsMean: mean(latencies),
    reliability,
    failures,
    cpuMs,
    heapDeltaMb,
    metrics,
    taskResults,
    createdAt: new Date().toISOString(),
    durationMs: Date.now() - startedAt,
  };

  const stored = store.save(card);
  logger.info(
    'benchmarks',
    `${suite.name} · ${card.versionLabel} → quality ${(quality * 100).toFixed(1)}%, ${card.tasksPassed}/${card.tasksTotal} tasks passed in ${card.durationMs} ms`,
    { triggeredBy },
  );
  return stored;
}

function repeatCorrectTotal(results: BenchmarkTaskResult[]): number {
  return results.filter((result) => result.correct).length;
}

/** Short, truthful description of what answered during a benchmark. */
export function describeEngine(specialistId: string, versionId?: string, versionLabel?: string): string {
  const version = versionId ? versionStore.get(versionId) : versionStore.activeVersion(specialistId);
  if (!version) return 'untrained baseline — no local artefact, requests escalate';
  const metrics = version.metrics ?? {};
  const evidence =
    metrics.calibratedThreshold !== undefined
      ? ` · calibrated threshold ${metrics.calibratedThreshold}`
      : metrics.recallAt5 !== undefined
        ? ` · recall@5 ${(metrics.recallAt5 * 100).toFixed(0)}%`
        : '';
  return `${version.methodName} · ${versionLabel ?? version.label} (${version.artifactKind.split(' ')[0]})${evidence}`;
}

export function suiteCatalogue(): BenchmarkSuite[] {
  return BENCHMARK_SUITES;
}

export function listSuiteIds(): BenchmarkSuiteId[] {
  return BENCHMARK_SUITES.map((suite) => suite.id);
}
