import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';
import {
  BenchmarkScorecard,
  BenchmarkSuiteId,
  CheckpointInfo,
  DecidedPromotion,
  TrainingEpoch,
  TrainingEvent,
  TrainingRun,
  TrainingRunRequest,
  VersionStatus,
} from '../../shared/types.js';
import { logger } from '../logger.js';
import { settingsStore } from '../settingsStore.js';
import { versionStore } from '../models/versions.js';
import { getSpecialist } from '../specialists/registry.js';
import { benchmarkStore } from '../benchmarks/store.js';
import { getSuite, suiteForDataset } from '../benchmarks/suites.js';
import { getDataset } from './datasets.js';
import { getMethod, MethodRunResult } from './methods.js';
import { checkpointsDir, runStore } from './runs.js';

export const trainingBus = new EventEmitter();
trainingBus.setMaxListeners(50);

export function emitTrainingEvent(event: TrainingEvent): void {
  trainingBus.emit('event', event);
}

export class TrainingError extends Error {
  constructor(message: string, readonly statusCode = 400, readonly code = 'TRAINING_ERROR') {
    super(message);
    this.name = 'TrainingError';
  }
}

/**
 * Applies the promotion policy to a finished run.
 *
 * When a benchmark scorecard exists for the candidate, the decision is made on
 * the *end-to-end* benchmark quality against the best available baseline (the
 * active version, or the untrained rule-policy baseline). Only when no suite is
 * attached does it fall back to the training hold-out metric.
 */
export function decidePromotion(params: {
  specialistId: string;
  metrics: Record<string, number>;
  primaryMetric: string;
  autoPromote: boolean;
  minImprovement: number;
  scorecard?: { quality: number; suiteName: string; baselineQuality: number | null; baselineLabel: string | null; baselineId: string | null } | null;
}): DecidedPromotion {
  const { specialistId, metrics, primaryMetric, autoPromote, minImprovement } = params;
  const baselineId = params.scorecard?.baselineId ?? null;
  const candidate = metrics[primaryMetric];
  if (typeof candidate !== 'number' || Number.isNaN(candidate)) {
    return {
      action: 'awaiting_review',
      reason: `The run reported no value for the primary metric "${primaryMetric}".`,
      candidateMetric: null,
      activeMetric: null,
      delta: null,
      metric: primaryMetric,
      baselineId,
    };
  }

  const card = params.scorecard ?? null;
  if (card) {
    const { quality, baselineQuality, baselineLabel, suiteName } = card;
    if (baselineQuality === null) {
      return {
        action: autoPromote ? 'promoted' : 'awaiting_review',
        reason: `${suiteName}: quality ${(quality * 100).toFixed(1)}%. No baseline scorecard exists yet, so there was nothing to regress against.`,
        candidateMetric: quality,
        activeMetric: null,
        delta: null,
        metric: 'benchmarkQuality',
        baselineId: null,
      };
    }
    const delta = quality - baselineQuality;
    if (delta >= minImprovement) {
      return {
        action: autoPromote ? 'promoted' : 'awaiting_review',
        reason: `${suiteName}: quality ${(quality * 100).toFixed(1)}% vs ${baselineLabel ?? 'baseline'} ${(baselineQuality * 100).toFixed(1)}% (${delta >= 0 ? '+' : ''}${(delta * 100).toFixed(1)} points, gate ${(minImprovement * 100).toFixed(1)}).`,
        candidateMetric: quality,
        activeMetric: baselineQuality,
        delta,
        metric: 'benchmarkQuality',
        baselineId,
      };
    }
    if (delta < 0) {
      return {
        action: 'kept_active',
        reason: `${suiteName}: quality dropped ${(Math.abs(delta) * 100).toFixed(1)} points against ${baselineLabel ?? 'baseline'} — the better performer was kept and this version marked as a regression.`,
        candidateMetric: quality,
        activeMetric: baselineQuality,
        delta,
        metric: 'benchmarkQuality',
        baselineId,
      };
    }
    return {
      action: 'kept_active',
      reason: `${suiteName}: quality improved by only ${(delta * 100).toFixed(1)} points, below the ${(minImprovement * 100).toFixed(1)} point gate. The existing performer stays active.`,
      candidateMetric: quality,
      activeMetric: baselineQuality,
      delta,
      metric: 'benchmarkQuality',
      baselineId,
    };
  }

  const active = versionStore.activeVersion(specialistId);
  const activeMetric = active ? (active.metrics[primaryMetric] ?? null) : null;

  if (!active || activeMetric === null) {
    return {
      action: autoPromote ? 'promoted' : 'awaiting_review',
      reason: active
        ? `No comparable ${primaryMetric} on the active version, so no regression check was possible.`
        : 'First trained version for this specialist — no active version to compare against.',
      candidateMetric: candidate,
      activeMetric,
      delta: null,
      metric: primaryMetric,
      baselineId,
    };
  }

  const delta = candidate - activeMetric;
  if (delta >= minImprovement) {
    return {
      action: autoPromote ? 'promoted' : 'awaiting_review',
      reason: `${primaryMetric} improved by ${(delta * 100).toFixed(2)} points (gate ${(minImprovement * 100).toFixed(2)}).`,
      candidateMetric: candidate,
      activeMetric,
      delta,
      metric: primaryMetric,
      baselineId,
    };
  }
  if (delta < 0) {
    return {
      action: 'kept_active',
      reason: `${primaryMetric} regressed by ${(Math.abs(delta) * 100).toFixed(2)} points — the active version was kept.`,
      candidateMetric: candidate,
      activeMetric,
      delta,
      metric: primaryMetric,
      baselineId,
    };
  }
  return {
    action: 'kept_active',
    reason: `${primaryMetric} gained only ${(delta * 100).toFixed(2)} points, below the ${(minImprovement * 100).toFixed(2)} improvement gate.`,
    candidateMetric: candidate,
    activeMetric,
    delta,
    metric: primaryMetric,
    baselineId,
  };
}

export interface StartRunOptions {
  triggeredBy?: TrainingRun['triggeredBy'];
}

export function startTraining(request: TrainingRunRequest, options: StartRunOptions = {}): TrainingRun {
  const specialist = getSpecialist(request.specialistId);
  if (!specialist) throw new TrainingError(`Unknown specialist "${request.specialistId}"`, 404, 'NOT_FOUND');

  const method = getMethod(request.methodId);
  if (!method) throw new TrainingError(`Unknown training method "${request.methodId}"`, 400, 'UNKNOWN_METHOD');

  const dataset = getDataset(request.datasetId);
  if (!dataset) throw new TrainingError(`Unknown dataset "${request.datasetId}"`, 404, 'NOT_FOUND');

  if (!method.available) {
    throw new TrainingError(
      `"${method.name}" cannot run in this environment. Requirements: ${method.requirements.join('; ')}.`,
      409,
      'METHOD_UNAVAILABLE',
    );
  }
  if (!method.appliesTo.includes(specialist.category)) {
    throw new TrainingError(
      `"${method.name}" does not apply to ${specialist.category} specialists. Supported: ${method.appliesTo.join(', ')}.`,
      400,
      'METHOD_NOT_APPLICABLE',
    );
  }
  if (!method.datasetKinds.includes(dataset.info.kind)) {
    throw new TrainingError(
      `Dataset "${dataset.info.name}" has kind "${dataset.info.kind}", which ${method.name} cannot consume (${method.datasetKinds.join(', ')}).`,
      400,
      'DATASET_INCOMPATIBLE',
    );
  }
  if (dataset.info.size === 0) {
    throw new TrainingError(`Dataset "${dataset.info.name}" is empty — add examples or choose another dataset.`, 400, 'EMPTY_DATASET');
  }
  if (runStore.list({ specialistId: specialist.id, status: 'running' }).length > 0) {
    throw new TrainingError('A training run is already in progress for this specialist.', 409, 'RUN_IN_PROGRESS');
  }

  const hyperparameters: Record<string, number | boolean | string> = {};
  for (const spec of method.hyperparameters) hyperparameters[spec.key] = spec.default;
  Object.assign(hyperparameters, request.hyperparameters ?? {});

  const run = runStore.create({
    specialistId: specialist.id,
    specialistName: specialist.name,
    methodId: method.id,
    methodName: method.name,
    datasetId: dataset.info.id,
    datasetName: dataset.info.name,
    datasetSize: dataset.info.size,
    hyperparameters,
    status: 'queued',
    startedAt: null,
    notes: request.notes ?? '',
    triggeredBy: options.triggeredBy ?? 'user',
    primaryMetric: null,
  });

  emitTrainingEvent({ type: 'run.created', run });
  logger.info('training', `Queued ${run.id} (${method.name} on ${dataset.info.name})`);

  // Runs execute asynchronously so the API can answer immediately; progress is
  // streamed through the training event bus.
  setImmediate(() => {
    void executeRun(run.id, { autoPromote: request.autoPromote, runBenchmarkAfter: request.runBenchmarkAfter });
  });

  return run;
}

/**
 * Runs a pre-training measurement when the specialist has no active version (or
 * none for this suite yet). The suite is the specialist's primary suite — the
 * same one `executeRun` benchmarks the candidate on — so the before/after pair
 * is comparable by construction instead of being measured on two different task
 * sets. Failures are logged, never fatal: a missing baseline degrades the
 * comparison, it must not break the training run.
 */
async function captureBaseline(run: TrainingRun, specialistId: string): Promise<void> {
  const specialist = getSpecialist(specialistId);
  const primary = specialist?.benchmarkSuiteIds[0] as BenchmarkSuiteId | undefined;
  const suite = primary && getSuite(primary) ? primary : suiteForDataset(run.datasetId);
  if (!suite) return;
  const active = versionStore.activeVersion(specialistId);
  if (active && benchmarkStore.latestFor(suite, active.id)) return;
  try {
    const { runBenchmark } = await import('../benchmarks/runner.js');
    emitTrainingEvent({
      type: 'run.status',
      runId: run.id,
      status: 'running',
      progress: 0.01,
      message: `Measuring the current state on ${suite} before training`,
    });
    const card = await runBenchmark({ suiteId: suite, specialistId, repeats: 1 }, 'training');
    logger.info('training', `Baseline ${card.id} for ${specialistId}: quality ${(card.quality * 100).toFixed(1)}%`);
  } catch (e) {
    logger.warn('training', `Could not measure a pre-training baseline for ${specialistId}`, { error: String(e) });
  }
}

/**
 * Baseline for a candidate scorecard: the active version's latest card for the
 * same suite, or the untrained rule-policy baseline card when nothing is active.
 */
function findBaseline(candidate: BenchmarkScorecard): BenchmarkScorecard | null {
  const active = versionStore.activeVersion(candidate.specialistId);
  if (active && active.id !== candidate.versionId) {
    const activeCard = benchmarkStore.latestFor(candidate.suiteId, active.id);
    if (activeCard) return activeCard;
  }
  const history = benchmarkStore.list({ specialistId: candidate.specialistId, suiteId: candidate.suiteId, limit: 30 });
  return (
    history.find((card) => card.id !== candidate.id && card.versionId.endsWith('@untrained')) ??
    history.find((card) => card.id !== candidate.id) ??
    null
  );
}

async function executeRun(runId: string, flags: { autoPromote?: boolean; runBenchmarkAfter?: boolean }): Promise<void> {
  const run = runStore.get(runId);
  if (!run) return;
  const method = getMethod(run.methodId);
  const dataset = getDataset(run.datasetId);
  const specialist = getSpecialist(run.specialistId);
  if (!method || !dataset || !specialist) {
    finish(runId, 'failed', { error: 'Run configuration is no longer resolvable (method, dataset or specialist missing).' });
    return;
  }

  const settings = settingsStore.get();
  const autoPromote = flags.autoPromote ?? settings.training.autoPromote;
  const minImprovement = settings.training.minImprovement;
  const startedMs = Date.now();

  runStore.update(runId, { status: 'running', startedAt: new Date().toISOString(), progress: 0.02 });
  emitTrainingEvent({ type: 'run.status', runId, status: 'running', progress: 0.02, message: `${method.name} started` });

  // Objective before/after pair: measure the current (possibly untrained) state
  // on the same suite the candidate will be measured on. Without this a first
  // run has nothing to compare against, which is exactly the case where a
  // promotion decision is easiest to get wrong.
  if (flags.runBenchmarkAfter) await captureBaseline(run, specialist.id);

  let epochCounter = 0;
  // Methods that are not epoch-driven (index builds, induction passes) report a
  // small number of phases, so the expected count is clamped to a sane minimum.
  const expectedEpochs = Math.max(4, Number(run.hyperparameters.epochs ?? 0) || 0);
  const progressFor = (epoch: number) => Math.min(0.9, 0.05 + 0.8 * (epoch / expectedEpochs));

  try {
    const result: MethodRunResult = await method.run({
      runId,
      specialistId: specialist.id,
      specialistCategory: specialist.category,
      dataset,
      hyperparameters: run.hyperparameters,
      onEpoch: (epoch) => {
        epochCounter = Math.max(epochCounter, epoch.epoch);
        const entry: TrainingEpoch = {
          epoch: epoch.epoch,
          trainLoss: epoch.trainLoss,
          trainAccuracy: epoch.trainAccuracy,
          valLoss: epoch.valLoss,
          valAccuracy: epoch.valAccuracy,
          learningRate: epoch.learningRate,
          durationMs: epoch.durationMs,
        };
        runStore.addEpoch(runId, entry);
        const progress = progressFor(epoch.epoch);
        runStore.update(runId, { progress });
        emitTrainingEvent({ type: 'run.epoch', runId, epoch: entry, progress });
      },
      onCheckpoint: (epoch, artifact, valLoss, valAccuracy, note) => {
        const checkpoint = writeCheckpoint(runId, epoch, artifact, valLoss, valAccuracy, note);
        runStore.addCheckpoint(runId, checkpoint);
        runStore.update(runId, { progress: Math.min(0.95, progressFor(epoch) + 0.02) });
        emitTrainingEvent({ type: 'run.checkpoint', runId, checkpoint });
      },
      shouldStop: () => runStore.isCancelRequested(runId),
      log: (message, meta) => logger.info('training', `[${runId}] ${message}`, meta),
    });

    if (runStore.isCancelRequested(runId)) {
      finish(runId, 'cancelled', { error: 'Cancelled by the user before completion.', durationMs: Date.now() - startedMs });
      return;
    }

    const version = versionStore.create({
      specialistId: specialist.id,
      methodId: method.id,
      methodName: method.name,
      artifactKind: result.artifactKind,
      artifact: result.artifact,
      metrics: result.metrics,
      primaryMetric: result.primaryMetric,
      runId,
      datasetId: dataset.info.id,
      datasetSize: dataset.info.size,
      notes: run.notes || `${method.name} · ${dataset.info.name}`,
      baseModel: result.baseModel,
    });

    // Benchmark the candidate before deciding: promotion is based on measured
    // end-to-end behaviour, not on the training loss alone.
    let scorecard: BenchmarkScorecard | null = null;
    const suiteId = specialist.benchmarkSuiteIds[0] as BenchmarkSuiteId | undefined;
    if (suiteId) {
      try {
        const { runBenchmark } = await import('../benchmarks/runner.js');
        scorecard = await runBenchmark({ suiteId, specialistId: specialist.id, versionId: version.id, repeats: 1 }, 'training');
        runStore.update(runId, { progress: 0.97 });
      } catch (e) {
        logger.warn('training', `Post-run benchmark failed for ${runId}`, { error: String(e) });
      }
    }

    const baseline = scorecard ? findBaseline(scorecard) : null;
    const promotion = decidePromotion({
      specialistId: specialist.id,
      metrics: result.metrics,
      primaryMetric: result.primaryMetric,
      autoPromote,
      minImprovement,
      scorecard: scorecard
        ? {
            quality: scorecard.quality,
            suiteName: scorecard.suiteName,
            baselineQuality: baseline?.quality ?? null,
            baselineLabel: baseline?.versionLabel ?? null,
            baselineId: baseline?.id ?? null,
          }
        : null,
    });

    let versionStatus: VersionStatus = 'candidate';
    if (promotion.action === 'promoted') {
      versionStore.promote(version.id, promotion.reason);
      versionStatus = 'active';
    } else if (promotion.action === 'kept_active') {
      versionStatus = (promotion.delta ?? 0) < 0 ? 'rejected' : 'candidate';
      versionStore.setStatus(version.id, versionStatus, promotion.reason);
    } else {
      versionStore.setStatus(version.id, 'candidate', promotion.reason);
    }

    finish(runId, 'completed', {
      metrics: {
        ...result.metrics,
        ...(scorecard ? { benchmarkQuality: scorecard.quality, benchmarkSuccess: scorecard.taskSuccessRate } : {}),
      },
      primaryMetric: scorecard ? 'benchmarkQuality' : result.primaryMetric,
      promotion,
      versionId: version.id,
      scorecardId: scorecard?.id ?? null,
      durationMs: Date.now() - startedMs,
      artifactPath: versionStore.get(version.id) ? path.join(process.env.AGENTOS_DATA_DIR || path.join(process.cwd(), 'data'), 'models', specialist.id, `${version.id}.json`) : null,
    });

    logger.info('training', `Completed ${runId} → ${version.id} (${versionStatus})`, { promotion: promotion.action });

  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    logger.error('training', `Run ${runId} failed: ${message}`);
    finish(runId, 'failed', { error: message, durationMs: Date.now() - startedMs });
  } finally {
    runStore.clearCancel(runId);
  }
}

function writeCheckpoint(runId: string, epoch: number, artifact: unknown, valLoss: number, valAccuracy: number, note?: string): CheckpointInfo {
  const dir = checkpointsDir(runId);
  fs.mkdirSync(dir, { recursive: true });
  const id = `ckpt_${runId}_e${epoch}`;
  const file = path.join(dir, `${id}.json`);
  const payload = JSON.stringify({ id, epoch, note: note ?? '', artifact }, null, 0);
  fs.writeFileSync(file, payload, 'utf-8');
  return {
    id,
    epoch,
    path: file,
    valLoss,
    valAccuracy,
    sizeBytes: Buffer.byteLength(payload, 'utf-8'),
    createdAt: new Date().toISOString(),
    best: true,
  };
}

function finish(
  runId: string,
  status: TrainingRun['status'],
  patch: Partial<TrainingRun>,
): void {
  const run = runStore.update(runId, {
    status,
    progress: status === 'completed' ? 1 : runStore.get(runId)?.progress ?? 0,
    finishedAt: new Date().toISOString(),
    ...patch,
  });
  if (run) emitTrainingEvent({ type: 'run.finished', run });
}

export function cancelTraining(runId: string): boolean {
  const ok = runStore.requestCancel(runId);
  if (ok) {
    emitTrainingEvent({ type: 'run.status', runId, status: 'running', progress: runStore.get(runId)?.progress ?? 0, message: 'Cancellation requested — stopping after the current epoch' });
  }
  return ok;
}
