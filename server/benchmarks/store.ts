import fs from 'node:fs';
import path from 'node:path';
import {
  BenchmarkComparison,
  BenchmarkComparisonRow,
  BenchmarkScorecard,
  BenchmarkSuiteId,
} from '../../shared/types.js';
import { logger } from '../logger.js';

const MAX_CARDS = 200;
const MAX_TASK_RESULTS = 80;

/** Tolerances that decide whether a change counts as a regression. */
export const REGRESSION_THRESHOLDS = {
  /** Absolute drop in a 0..1 quality metric that counts as a regression. */
  quality: 0.02,
  /** Relative increase in latency that counts as a regression. */
  latency: 0.25,
  /** Relative increase in CPU/heap cost that counts as a regression. */
  resource: 0.5,
};

const HIGHER_IS_BETTER = new Set(['quality', 'taskSuccessRate', 'reliability', 'exactMatch', 'recallAt5', 'mrr', 'accuracy', 'macroF1', 'hitRate']);
const LOWER_IS_BETTER = new Set(['latencyMsP50', 'latencyMsP95', 'latencyMsMean', 'cpuMs', 'heapDeltaMb', 'failures']);

const METRIC_LABELS: Record<string, string> = {
  quality: 'Quality score',
  taskSuccessRate: 'Task success rate',
  reliability: 'Reliability (no errors)',
  latencyMsP50: 'Latency p50 (ms)',
  latencyMsP95: 'Latency p95 (ms)',
  latencyMsMean: 'Latency mean (ms)',
  cpuMs: 'CPU time (ms)',
  heapDeltaMb: 'Heap growth (MB)',
  failures: 'Failures',
  exactMatch: 'Exact match',
  recallAt5: 'Recall@5',
  mrr: 'MRR',
  accuracy: 'Accuracy',
  macroF1: 'Macro F1',
  hitRate: 'Hit rate',
};

export const COMPARABLE_METRICS = [
  'quality',
  'taskSuccessRate',
  'reliability',
  'latencyMsP50',
  'latencyMsP95',
  'cpuMs',
  'heapDeltaMb',
];

function dir(): string {
  return path.join(process.env.AGENTOS_DATA_DIR || path.join(process.cwd(), 'data'), 'benchmarks');
}

function file(): string {
  return path.join(dir(), 'scorecards.json');
}

class BenchmarkStore {
  private cards: BenchmarkScorecard[] = [];

  constructor() {
    this.load();
  }

  private load(): void {
    try {
      if (!fs.existsSync(file())) return;
      const parsed = JSON.parse(fs.readFileSync(file(), 'utf-8')) as BenchmarkScorecard[];
      if (Array.isArray(parsed)) this.cards = parsed;
      logger.info('benchmarks', `Loaded ${this.cards.length} scorecards`);
    } catch (e) {
      logger.warn('benchmarks', 'Could not read benchmark history', { error: String(e) });
    }
  }

  private persist(): void {
    try {
      fs.mkdirSync(dir(), { recursive: true });
      const trimmed = this.cards.slice(0, MAX_CARDS);
      const tmp = `${file()}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify(trimmed, null, 2), 'utf-8');
      fs.renameSync(tmp, file());
    } catch (e) {
      logger.error('benchmarks', 'Failed to persist scorecards', { error: String(e) });
    }
  }

  create(card: BenchmarkScorecard): BenchmarkScorecard {
    const bounded: BenchmarkScorecard = {
      ...card,
      taskResults: card.taskResults.slice(0, MAX_TASK_RESULTS),
    };
    this.cards.unshift(bounded);
    this.cards = this.cards.slice(0, MAX_CARDS);
    this.persist();
    return bounded;
  }

  get(id: string): BenchmarkScorecard | null {
    return this.cards.find((c) => c.id === id) ?? null;
  }

  list(filter: { specialistId?: string; suiteId?: BenchmarkSuiteId; versionId?: string; limit?: number } = {}): BenchmarkScorecard[] {
    return this.cards
      .filter((c) => (filter.specialistId ? c.specialistId === filter.specialistId : true))
      .filter((c) => (filter.suiteId ? c.suiteId === filter.suiteId : true))
      .filter((c) => (filter.versionId ? c.versionId === filter.versionId : true))
      .slice(0, filter.limit ?? 50);
  }

  latestFor(suiteId: BenchmarkSuiteId, versionId: string): BenchmarkScorecard | null {
    return this.cards.find((c) => c.suiteId === suiteId && c.versionId === versionId) ?? null;
  }

  /** Best (highest quality) scorecard for a version across all suites. */
  bestQualityFor(specialistId: string, versionId: string): BenchmarkScorecard | null {
    const cards = this.cards.filter((c) => c.specialistId === specialistId && c.versionId === versionId);
    if (cards.length === 0) return null;
    return cards.reduce((best, card) => (card.quality > best.quality ? card : best), cards[0]);
  }

  clear(): number {
    const removed = this.cards.length;
    this.cards = [];
    this.persist();
    return removed;
  }

  buildComparison(candidate: BenchmarkScorecard, baseline: BenchmarkScorecard | null): BenchmarkComparison {
    if (!baseline) {
      return {
        suiteId: candidate.suiteId,
        candidateId: candidate.id,
        baselineId: null,
        rows: [],
        verdict: 'no_baseline',
        summary: 'No baseline scorecard exists for this suite yet — run the same suite against the active version to compare.',
      };
    }

    const rows: BenchmarkComparisonRow[] = COMPARABLE_METRICS.map((metric) => {
      const candidateValue = readMetric(candidate, metric);
      const baselineValue = readMetric(baseline, metric);
      const delta = candidateValue - baselineValue;
      const deltaPct = baselineValue === 0 ? (candidateValue === 0 ? 0 : 1) : delta / Math.abs(baselineValue);
      const higherBetter = HIGHER_IS_BETTER.has(metric);
      const lowerBetter = LOWER_IS_BETTER.has(metric);
      let direction: BenchmarkComparisonRow['direction'] = 'same';
      if (Math.abs(delta) > 1e-9) {
        direction = (higherBetter && delta > 0) || (lowerBetter && delta < 0) ? 'better' : 'worse';
      }
      const isLatency = metric.startsWith('latency');
      // A regression must be both *worse* and *large enough to matter*:
      // quality needs a visible drop in points, latency and resource cost a
      // relative increase beyond their tolerances.
      const regression =
        direction === 'worse' &&
        (higherBetter
          ? delta <= -REGRESSION_THRESHOLDS.quality
          : isLatency
            ? deltaPct >= REGRESSION_THRESHOLDS.latency
            : deltaPct >= REGRESSION_THRESHOLDS.resource);
      return {
        metric,
        label: METRIC_LABELS[metric] ?? metric,
        candidate: round(candidateValue),
        baseline: round(baselineValue),
        delta: round(delta),
        deltaPct: round(deltaPct, 4),
        direction,
        regression,
      };
    });

    const regressions = rows.filter((r) => r.regression);
    const improvements = rows.filter((r) => r.direction === 'better' && !r.regression);
    const qualityRow = rows.find((r) => r.metric === 'quality')!;
    const verdict: BenchmarkComparison['verdict'] = regressions.length > 0 ? 'regressed' : improvements.length > 0 ? 'improved' : 'unchanged';
    const summary =
      verdict === 'regressed'
        ? `Regression detected in ${regressions.map((r) => r.label).join(', ')} compared with ${baseline.versionLabel}.`
        : verdict === 'improved'
          ? `Improvement of ${(qualityRow.delta * 100).toFixed(1)} quality points over ${baseline.versionLabel} with no regressions.`
          : `No measurable change versus ${baseline.versionLabel}.`;

    return {
      suiteId: candidate.suiteId,
      candidateId: candidate.id,
      baselineId: baseline.id,
      rows,
      verdict,
      summary,
    };
  }
}

function readMetric(card: BenchmarkScorecard, metric: string): number {
  if (metric === 'quality') return card.quality;
  if (metric === 'taskSuccessRate') return card.taskSuccessRate;
  if (metric === 'reliability') return card.reliability;
  if (metric === 'latencyMsP50') return card.latencyMsP50;
  if (metric === 'latencyMsP95') return card.latencyMsP95;
  if (metric === 'cpuMs') return card.cpuMs;
  if (metric === 'heapDeltaMb') return card.heapDeltaMb;
  return card.metrics[metric] ?? 0;
}

function round(value: number, digits = 4): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

export const benchmarkStore = new BenchmarkStore();
