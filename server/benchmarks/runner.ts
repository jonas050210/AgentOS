import {
  BenchmarkRunRequest,
  BenchmarkScorecard,
  BenchmarkTaskResult,
} from '../../shared/types.js';
import { logger } from '../logger.js';
import { versionStore } from '../models/versions.js';
import { Bm25Index, evaluateRetrieval } from '../ml/retrieval.js';
import { evaluateArithmetic } from '../tools/builtin.js';
import { getSpecialist } from '../specialists/registry.js';
import { decide, extractExpression, extractUrlTarget, normalizeSpeechFor } from '../specialists/inference.js';
import { benchmarkStore } from './store.js';
import { BenchmarkTask, getSuite } from './suites.js';

export class BenchmarkError extends Error {
  constructor(message: string, readonly statusCode = 400, readonly code = 'BENCHMARK_ERROR') {
    super(message);
    this.name = 'BenchmarkError';
  }
}

interface TaskOutcome {
  actual: string;
  correct: boolean;
  error: string | null;
  /** Which mechanism produced the answer: trained head, rule policy or escalation. */
  source: 'head' | 'policy' | 'escalated' | 'error';
}

const RETRIEVAL_K = 5;

/**
 * Answers a single benchmark task using the specialist's active artifact.
 * Everything here is deterministic: the same version produces the same score.
 */
async function answerTask(
  specialistId: string,
  versionId: string | undefined,
  task: BenchmarkTask,
): Promise<TaskOutcome> {
  try {
    switch (task.kind) {
      case 'label': {
        const decision = decide(specialistId, task.input, { versionId });
        const source = classifyDecision(decision.engine, decision.escalated);
        if (decision.escalated) {
          return { actual: `escalated (${decision.escalationReason ?? 'no local decision'})`, correct: false, error: null, source };
        }
        return { actual: decision.label, correct: decision.label === task.expected, error: null, source };
      }
      case 'numeric': {
        const decision = decide(specialistId, task.input, { versionId });
        const source = classifyDecision(decision.engine, decision.escalated);
        if (decision.escalated || decision.label !== 'calc.evaluate') {
          return {
            actual: decision.escalated ? `escalated (${decision.escalationReason ?? 'no decision'})` : `routed to ${decision.label}`,
            correct: false,
            error: null,
            source,
          };
        }
        const expression = extractExpression(task.input);
        if (!expression) return { actual: 'no expression found', correct: false, error: null, source };
        const value = evaluateArithmetic(expression);
        const expected = Number(task.expected);
        return {
          actual: String(value),
          correct: Math.abs(value - expected) < 1e-6,
          error: null,
          source,
        };
      }
      case 'extraction': {
        const decision = decide(specialistId, task.input, { versionId });
        const source = classifyDecision(decision.engine, decision.escalated);
        if (decision.escalated || decision.label !== 'extract.url') {
          return {
            actual: decision.escalated ? `escalated (${decision.escalationReason ?? 'no decision'})` : `routed to ${decision.label}`,
            correct: false,
            error: null,
            source,
          };
        }
        const target = extractUrlTarget(task.input);
        return { actual: target ?? 'no url found', correct: target === task.expected, error: null, source };
      }
      case 'normalization': {
        const { text, lexiconEntries } = normalizeSpeechFor(specialistId, task.input, { versionId });
        const normalized = normalizeWhitespace(text);
        return {
          actual: normalized,
          correct: normalized === normalizeWhitespace(task.expected),
          error: null,
          source: lexiconEntries > 0 ? 'head' : 'policy',
        };
      }
      case 'retrieval': {
        const relevant = (task.meta?.relevantIds as string[]) ?? [];
        const artifact = resolveRetrievalArtifact(specialistId, versionId);
        if (!artifact) {
          return { actual: 'no index version — escalated', correct: false, error: null, source: 'escalated' };
        }
        const index = Bm25Index.fromArtifact(artifact);
        const hits = index.query(task.input, RETRIEVAL_K).map((h) => h.id);
        const found = hits.filter((id) => relevant.includes(id));
        const recall = relevant.length === 0 ? 0 : found.length / relevant.length;
        return {
          actual: hits.length > 0 ? hits.slice(0, 3).join(', ') : 'no hits',
          correct: recall >= 0.5,
          error: null,
          source: 'head',
        };
      }
      default:
        return { actual: 'unsupported task kind', correct: false, error: 'unsupported task kind', source: 'error' };
    }
  } catch (e) {
    return { actual: 'error', correct: false, error: e instanceof Error ? e.message : String(e), source: 'error' };
  }
}

function classifyDecision(engine: string, escalated: boolean): TaskOutcome['source'] {
  if (escalated) return 'escalated';
  if (engine.startsWith('rule policy')) return 'policy';
  if (engine.startsWith('Task head') || engine.startsWith('trained head')) return 'head';
  if (engine.startsWith('lexicon') || engine.startsWith('rules-only')) return 'head';
  if (engine.startsWith('BM25')) return 'head';
  return 'policy';
}

function resolveRetrievalArtifact(specialistId: string, versionId?: string) {
  const version = versionId ? versionStore.get(versionId) : versionStore.activeVersion(specialistId);
  if (!version || version.artifactKind !== 'retrieval_index') return null;
  const artifact = versionStore.artifact(version.id);
  return artifact as Parameters<typeof Bm25Index.fromArtifact>[0] | null;
}

function normalizeWhitespace(text: string): string {
  return text.replace(/\s+/g, ' ').trim();
}

export async function runBenchmark(
  request: BenchmarkRunRequest,
  triggeredBy: 'user' | 'training' | 'system' = 'user',
): Promise<BenchmarkScorecard> {
  const suite = getSuite(request.suiteId);
  if (!suite) throw new BenchmarkError(`Unknown benchmark suite "${request.suiteId}"`, 404, 'NOT_FOUND');

  const specialist = getSpecialist(request.specialistId);
  if (!specialist) throw new BenchmarkError(`Unknown specialist "${request.specialistId}"`, 404, 'NOT_FOUND');

  if (suite.specialistCategory !== specialist.category && specialist.category !== 'custom') {
    throw new BenchmarkError(
      `Suite "${suite.name}" targets ${suite.specialistCategory} specialists, but ${specialist.name} is a ${specialist.category} specialist.`,
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
  const perRepeatQuality: number[] = [];

  for (let repeat = 0; repeat < repeats; repeat++) {
    let correctCount = 0;
    for (const task of suite.tasks) {
      const taskStart = performance.now();
      const outcome = await answerTask(specialist.id, version?.id, task);
      const latencyMs = Number((performance.now() - taskStart).toFixed(3));
      if (outcome.correct) correctCount++;
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
    perRepeatQuality.push(correctCount / Math.max(1, suite.tasks.length));
  }

  const cpuUsage = process.cpuUsage(cpuBefore);
  const heapAfter = process.memoryUsage().heapUsed;
  const latencies = taskResults.map((r) => r.latencyMs).sort((a, b) => a - b);
  const failures = taskResults.filter((r) => r.error).length;
  const passed = taskResults.filter((r) => r.correct).length;
  const tasksTotal = suite.tasks.length;

  const accuracy = passed / Math.max(1, tasksTotal);
  const metrics: Record<string, number> = {
    accuracy: round(accuracy),
    taskSuccessRate: round(accuracy),
    reliability: round(1 - failures / Math.max(1, tasksTotal)),
  };

  // Suite-specific metrics: retrieval suites report recall@k and MRR directly.
  let quality = accuracy;
  if (suite.tasks.every((t) => t.kind === 'retrieval')) {
    const artifact = resolveRetrievalArtifact(specialist.id, version?.id);
    if (artifact) {
      const index = Bm25Index.fromArtifact(artifact);
      const retrieval = evaluateRetrieval(
        index,
        suite.tasks.map((t) => ({ query: t.input, relevantIds: (t.meta?.relevantIds as string[]) ?? [] })),
        RETRIEVAL_K,
      );
      metrics.recallAt5 = retrieval.recallAtK;
      metrics.mrr = retrieval.mrr;
      metrics.hitRate = retrieval.hitRate;
      metrics.precisionAt5 = retrieval.precisionAtK;
      quality = retrieval.recallAtK;
    } else {
      metrics.recallAt5 = 0;
      metrics.mrr = 0;
      metrics.hitRate = 0;
      quality = 0;
    }
  } else if (suite.id === 'voice.normalization') {
    metrics.exactMatch = round(accuracy);
    quality = accuracy;
  } else {
    metrics.macroF1 = round(accuracy);
  }

  const consistency = repeats > 1 ? round(1 - Math.min(1, stdev(perRepeatQuality))) : 1;
  metrics.consistency = consistency;
  metrics.headAnswered = taskResults.filter((r) => r.source === 'head').length;
  metrics.policyAnswered = taskResults.filter((r) => r.source === 'policy').length;
  metrics.escalated = taskResults.filter((r) => r.source === 'escalated').length;

  const card: BenchmarkScorecard = {
    id: `bench_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
    suiteId: suite.id,
    suiteName: suite.name,
    specialistId: specialist.id,
    specialistName: specialist.name,
    versionId: version?.id ?? `${specialist.id}@untrained`,
    versionLabel: version ? version.label : 'untrained baseline',
    quality: round(quality),
    taskSuccessRate: round(accuracy),
    tasksPassed: passed,
    tasksTotal,
    latencyMsP50: percentile(latencies, 0.5),
    latencyMsP95: percentile(latencies, 0.95),
    latencyMsMean: round(latencies.reduce((a, b) => a + b, 0) / Math.max(1, latencies.length), 3),
    reliability: metrics.reliability,
    failures,
    cpuMs: Math.round((cpuUsage.user + cpuUsage.system) / 1000),
    heapDeltaMb: round((heapAfter - heapBefore) / 1024 / 1024, 2),
    metrics,
    taskResults,
    createdAt: new Date().toISOString(),
    durationMs: Date.now() - startedAt,
    engine: version
      ? `${version.methodName} · ${version.label} (${version.artifactKind})`
      : 'untrained baseline — no local artifact, requests escalate',
  };

  benchmarkStore.create(card);
  logger.info('benchmarks', `${suite.name} → quality ${(card.quality * 100).toFixed(1)}% on ${card.versionLabel}`, {
    specialist: specialist.name,
    passed,
    total: tasksTotal,
    triggeredBy,
  });

  return card;
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1));
  return round(sorted[index], 3);
}

function stdev(values: number[]): number {
  if (values.length < 2) return 0;
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / values.length;
  return Math.sqrt(variance);
}

function round(value: number, digits = 4): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}
