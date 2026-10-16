import fs from 'node:fs';
import path from 'node:path';
import {
  BenchmarkComparison,
  BenchmarkComparisonRow,
  BenchmarkScorecard,
  BenchmarkSuiteId,
} from '../../shared/types.js';
import { logger } from '../logger.js';

/**
 * Scorecards are the evidence behind every promotion decision: quality, task
 * success, speed, reliability and resource use for one suite and one model
 * version. They are kept on disk so a regression can always be traced back to
 * the version that caused it.
 */

const MAX_CARDS = 240;
/**
 * Per-task results kept on a card. Larger than the biggest shipped suite (90
 * tasks) so the UI can always list every task; the cap only guards against a
 * future suite that would bloat the store.
 */
const MAX_TASK_RESULTS = 150;

/** Everything that can meaningfully get worse. */
export const REGRESSION_THRESHOLDS = {
  /** Absolute quality/task-success/reliability drop (0..1 scale). */
  quality: 0.02,
  /** Relative latency increase (p50/p95/mean). */
  latency: 0.25,
  /** Relative increase of CPU time or heap growth. */
  resource: 0.5,
};

export const COMPARABLE_METRICS: Array<{ metric: string; label: string; higherIsBetter: boolean }> = [
  { metric: 'quality', label: 'Quality score', higherIsBetter: true },
  { metric: 'taskSuccessRate', label: 'Task success rate', higherIsBetter: true },
  { metric: 'reliability', label: 'Reliability (no errors)', higherIsBetter: true },
  { metric: 'latencyMsP50', label: 'Latency p50 (ms)', higherIsBetter: false },
  { metric: 'latencyMsP95', label: 'Latency p95 (ms)', higherIsBetter: false },
  { metric: 'cpuMs', label: 'CPU time (ms)', higherIsBetter: false },
  { metric: 'heapDeltaMb', label: 'Heap growth (MB)', higherIsBetter: false },
];

function dataDir(): string {
  return process.env.AGENTOS_DATA_DIR || path.join(process.cwd(), 'data');
}

function scorecardFile(): string {
  return path.join(dataDir(), 'benchmarks', 'scorecards.json');
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

export class BenchmarkStore {
  private cards: BenchmarkScorecard[] = [];

  constructor(private readonly file: string = scorecardFile()) {
    this.load();
  }

  private load(): void {
    try {
      if (!fs.existsSync(this.file)) return;
      const parsed = JSON.parse(fs.readFileSync(this.file, 'utf-8')) as BenchmarkScorecard[];
      if (Array.isArray(parsed)) {
        this.cards = parsed;
        logger.info('benchmarks', `Loaded ${parsed.length} benchmark scorecards`);
      }
    } catch (e) {
      logger.warn('benchmarks', `Could not read scorecards: ${String(e)}`);
    }
  }

  private persist(): void {
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true });
      const trimmed = this.cards.slice(0, MAX_CARDS);
      fs.writeFileSync(this.file, JSON.stringify(trimmed, null, 1), 'utf-8');
    } catch (e) {
      logger.error('benchmarks', `Could not persist scorecards: ${String(e)}`);
    }
  }

  /** Stores a card, keeping the on-disk payload bounded. */
  save(card: BenchmarkScorecard): BenchmarkScorecard {
    const stored: BenchmarkScorecard = {
      ...card,
      taskResults: card.taskResults.slice(0, MAX_TASK_RESULTS),
    };
    this.cards = [stored, ...this.cards.filter((c) => c.id !== stored.id)].slice(0, MAX_CARDS);
    this.persist();
    return stored;
  }

  get(id: string): BenchmarkScorecard | null {
    return this.cards.find((card) => card.id === id) ?? null;
  }

  list(filter: { specialistId?: string; suiteId?: BenchmarkSuiteId; versionId?: string; limit?: number } = {}): BenchmarkScorecard[] {
    const limit = filter.limit ?? 50;
    return this.cards
      .filter((card) => (filter.specialistId ? card.specialistId === filter.specialistId : true))
      .filter((card) => (filter.suiteId ? card.suiteId === filter.suiteId : true))
      .filter((card) => (filter.versionId ? card.versionId === filter.versionId : true))
      .slice(0, limit);
  }

  /** Newest card for one suite and version — the natural before/after baseline. */
  latestFor(suiteId: BenchmarkSuiteId, versionId: string): BenchmarkScorecard | null {
    return this.cards.find((card) => card.suiteId === suiteId && card.versionId === versionId) ?? null;
  }

  /** Alias kept for callers that name the operation from the UI's perspective. */
  buildComparison(candidate: BenchmarkScorecard, baseline: BenchmarkScorecard | null): BenchmarkComparison {
    return this.compare(candidate, baseline);
  }

  /** Best known quality for a specialist, used for the "trained on" badge. */
  bestFor(specialistId: string): BenchmarkScorecard | null {
    return this.cards
      .filter((card) => card.specialistId === specialistId)
      .reduce<BenchmarkScorecard | null>((best, card) => (!best || card.quality > best.quality ? card : best), null);
  }

  count(): number {
    return this.cards.length;
  }

  clear(): number {
    const removed = this.cards.length;
    this.cards = [];
    this.persist();
    return removed;
  }

  /**
   * Compares a candidate card against a baseline card. A regression needs to be
   * both *worse* and *large enough to matter*, so a few microseconds of noise
   * never blocks a genuinely better model.
   */
  compare(candidate: BenchmarkScorecard, baseline: BenchmarkScorecard | null): BenchmarkComparison {
    if (!baseline) {
      return {
        suiteId: candidate.suiteId,
        candidateId: candidate.id,
        baselineId: null,
        rows: [],
        verdict: 'no_baseline',
        tradeoffs: [],
        summary: 'No baseline scorecard for this suite yet — this result becomes the reference.',
      };
    }

    const rows: BenchmarkComparisonRow[] = COMPARABLE_METRICS.map(({ metric, label, higherIsBetter }) => {
      const candidateValue = readMetric(candidate, metric);
      const baselineValue = readMetric(baseline, metric);
      const delta = candidateValue - baselineValue;
      const deltaPct = baselineValue === 0 ? (candidateValue === 0 ? 0 : 1) : delta / Math.abs(baselineValue);
      const direction: BenchmarkComparisonRow['direction'] =
        Math.abs(delta) < 1e-9 ? 'same' : (higherIsBetter && delta > 0) || (!higherIsBetter && delta < 0) ? 'better' : 'worse';
      const isLatency = metric.startsWith('latency');
      const regression =
        direction === 'worse' &&
        (higherIsBetter
          ? Math.abs(delta) > REGRESSION_THRESHOLDS.quality
          : isLatency
            ? deltaPct > REGRESSION_THRESHOLDS.latency
            : deltaPct > REGRESSION_THRESHOLDS.resource);
      return {
        metric,
        label,
        candidate: round(candidateValue),
        baseline: round(baselineValue),
        delta: round(delta),
        deltaPct: round(deltaPct * 100, 2),
        direction,
        regression,
      };
    });

    const regressions = rows.filter((row) => row.regression);
    const qualityRow = rows.find((row) => row.metric === 'quality')!;
    // Two runs of the same version are a repeat measurement, not an improvement:
    // calling noise progress would make the promotion trail untrustworthy.
    const sameVersion = candidate.versionId === baseline.versionId;
    // The verdict answers one question — "is this version better than the one it
    // replaced?" — and the answer follows the quality score, because that is what
    // a promotion is decided on. A slower or heavier candidate is not called a
    // regression for that alone: the cost is reported as a trade-off instead, so
    // a 30-point quality gain is never hidden behind a latency warning.
    const qualityRegression = qualityRow.regression;
    const qualityImprovement = qualityRow.delta > REGRESSION_THRESHOLDS.quality;
    const tradeoffs = regressions.filter((row) => row.metric !== 'quality').map((row) => row.label);
    const verdict: BenchmarkComparison['verdict'] = sameVersion
      ? regressions.length > 0
        ? 'regressed'
        : 'unchanged'
      : qualityRegression
        ? 'regressed'
        : qualityImprovement
          ? 'improved'
          : 'unchanged';
    const qualityDelta = `${qualityRow.delta * 100 >= 0 ? '+' : ''}${(qualityRow.delta * 100).toFixed(1)}`;
    const faster = rows.filter((row) => row.metric.startsWith('latency') && row.direction === 'better').length;
    const summary =
      verdict === 'regressed' && sameVersion
        ? `Repeat run of ${candidate.versionLabel} came out worse than the previous run: ${regressions.map((row) => row.label).join(', ')} — treat this result as unstable.`
        : verdict === 'regressed'
          ? `${candidate.versionLabel} is worse than ${baseline.versionLabel}: quality ${qualityDelta} points, ${regressions.map((row) => row.label).join(', ')} worse.`
          : verdict === 'improved'
            ? `${candidate.versionLabel} improves on ${baseline.versionLabel}: quality ${qualityDelta} points${tradeoffs.length > 0 ? `, at the cost of ${tradeoffs.join(', ')}` : ''}.`
            : sameVersion
              ? `Repeat run of ${candidate.versionLabel}: quality unchanged (${qualityDelta} points), ${faster} latency metric(s) better.`
              : `${candidate.versionLabel} is statistically indistinguishable from ${baseline.versionLabel}.`;

    return { suiteId: candidate.suiteId, candidateId: candidate.id, baselineId: baseline.id, rows, verdict, tradeoffs, summary };
  }

  /** Baseline lookup order: the version's own history, then the untrained baseline, then anything. */
  findBaseline(candidate: BenchmarkScorecard, versionId: string | null): BenchmarkScorecard | null {
    const sameSuite = this.cards.filter((card) => card.suiteId === candidate.suiteId && card.id !== candidate.id);
    if (versionId) {
      const own = sameSuite.find((card) => card.versionId === versionId);
      if (own) return own;
    }
    const untrained = sameSuite.find((card) => card.versionId.endsWith('@untrained'));
    if (untrained) return untrained;
    return sameSuite[0] ?? null;
  }
}

export const benchmarkStore = new BenchmarkStore();
