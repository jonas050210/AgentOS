import { Router, Request, Response } from 'express';
import { z } from 'zod';
import {
  BenchmarkRunRequest,
  BenchmarkSuiteId,
  CreateSpecialistRequest,
  SpecialistPatch,
  TrainingRunRequest,
} from '../../shared/types.js';
import { logger } from '../logger.js';
import { ApiError, asyncHandler } from './helpers.js';
import { specialistRegistry } from '../specialists/registry.js';
import { runSpecialist } from '../specialists/inference.js';
import { normalizeSpeechFor } from '../specialists/inference.js';
import { addUserExample, getDataset, listDatasets, listUserExamples, removeUserExample, datasetsForSpecialist } from '../training/datasets.js';
import { methodInfos, TRAINING_METHODS, getMethod } from '../training/methods.js';
import { cancelTraining, startTraining, trainingBus } from '../training/engine.js';
import { runStore } from '../training/runs.js';
import { versionStore } from '../models/versions.js';
import { benchmarkStore } from '../benchmarks/store.js';
import { runBenchmark } from '../benchmarks/runner.js';
import { suiteInfos, getSuite } from '../benchmarks/suites.js';

const hyperparametersSchema = z.record(z.union([z.number(), z.boolean(), z.string()]));

const runRequestSchema = z.object({
  specialistId: z.string().min(1).max(80),
  methodId: z.enum(['task_head_sgd', 'retrieval_index', 'lexicon_induction', 'llm_adapter_finetune']),
  datasetId: z.string().min(1).max(120),
  hyperparameters: hyperparametersSchema.optional(),
  autoPromote: z.boolean().optional(),
  runBenchmarkAfter: z.boolean().optional(),
  notes: z.string().max(500).optional(),
});

const specialistPatchSchema = z
  .object({
    name: z.string().min(1).max(60).optional(),
    shortName: z.string().max(30).optional(),
    tagline: z.string().max(160).optional(),
    description: z.string().max(600).optional(),
    accent: z.string().regex(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i).optional(),
    glyph: z.string().max(24).optional(),
    systemPrompt: z.string().max(4000).optional(),
    params: z
      .object({
        temperature: z.number().min(0).max(2).optional(),
        maxTokens: z.number().min(64).max(8000).optional(),
        topP: z.number().min(0.05).max(1).optional(),
      })
      .optional(),
    toolIds: z.array(z.string().max(60)).max(30).optional(),
    capabilityIds: z.array(z.string().max(60)).max(20).optional(),
    enabled: z.boolean().optional(),
  })
  .strict();

const createSpecialistSchema = z.object({
  name: z.string().min(1).max(60),
  shortName: z.string().max(30).optional(),
  tagline: z.string().max(160).optional(),
  description: z.string().max(600).optional(),
  category: z.enum(['coding', 'research', 'knowledge', 'voice', 'general', 'custom']).optional(),
  accent: z.string().regex(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i).optional(),
  glyph: z.string().max(24).optional(),
  templateId: z.string().max(80).optional(),
  toolIds: z.array(z.string().max(60)).max(30).optional(),
  systemPrompt: z.string().max(4000).optional(),
  params: z
    .object({
      temperature: z.number().min(0).max(2).optional(),
      maxTokens: z.number().min(64).max(8000).optional(),
      topP: z.number().min(0.05).max(1).optional(),
    })
    .optional(),
});

const testSchema = z.object({
  input: z.string().min(1).max(4000),
  saveAsExample: z.boolean().optional(),
  versionId: z.string().max(120).optional(),
  skipEscalation: z.boolean().optional(),
});

const benchmarkRunSchema = z.object({
  suiteId: z.string().max(60),
  specialistId: z.string().max(80),
  versionId: z.string().max(120).optional(),
  repeats: z.number().int().min(1).max(3).optional(),
});

/** Verifies a promotion by benchmarking the candidate against the active version. */
async function verifyPromotion(versionId: string): Promise<{ ok: boolean; comparison: unknown; message: string }> {
  const version = versionStore.get(versionId);
  if (!version) throw new ApiError('Unknown version', 404, 'NOT_FOUND');
  const specialist = specialistRegistry.get(version.specialistId);
  if (!specialist) throw new ApiError('Specialist no longer exists', 404, 'NOT_FOUND');

  const suiteId = specialist.benchmarkSuiteIds[0] as BenchmarkSuiteId | undefined;
  if (!suiteId) {
    return { ok: true, comparison: null, message: 'No benchmark suite is attached to this specialist, so promotion could not be verified automatically.' };
  }

  const candidateCard = await runBenchmark({ suiteId, specialistId: specialist.id, versionId, repeats: 1 }, 'system');
  const activeVersion = versionStore.activeVersion(specialist.id);
  if (!activeVersion || activeVersion.id === versionId) {
    return {
      ok: true,
      comparison: null,
      message: `Benchmarked ${version.label}: quality ${(candidateCard.quality * 100).toFixed(1)}%. No competing active version, so nothing to regress against.`,
    };
  }
  const baselineCard = await runBenchmark({ suiteId, specialistId: specialist.id, versionId: activeVersion.id, repeats: 1 }, 'system');
  const comparison = benchmarkStore.buildComparison(candidateCard, baselineCard);
  return {
    ok: comparison.verdict !== 'regressed',
    comparison,
    message: comparison.summary,
  };
}

export function createPlatformRouter(): Router {
  const router = Router();

  // -------------------------------------------------------------------------
  // Specialists
  // -------------------------------------------------------------------------

  router.get(
    '/specialists',
    asyncHandler(async (_req, res) => {
      res.json({ specialists: specialistRegistry.list() });
    }),
  );

  router.post(
    '/specialists',
    asyncHandler(async (req, res) => {
      const parsed = createSpecialistSchema.safeParse(req.body);
      if (!parsed.success) {
        throw new ApiError(`Invalid specialist: ${parsed.error.issues.map((i) => `${i.path.join('.')} ${i.message}`).join('; ')}`, 400, 'VALIDATION_ERROR');
      }
      const created = specialistRegistry.create(parsed.data as CreateSpecialistRequest);
      res.status(201).json(created);
    }),
  );

  router.get(
    '/specialists/:id',
    asyncHandler(async (req, res) => {
      const specialist = specialistRegistry.get(req.params.id);
      if (!specialist) throw new ApiError(`Unknown specialist "${req.params.id}"`, 404, 'NOT_FOUND');
      res.json(specialist);
    }),
  );

  router.patch(
    '/specialists/:id',
    asyncHandler(async (req, res) => {
      const parsed = specialistPatchSchema.safeParse(req.body);
      if (!parsed.success) {
        throw new ApiError(`Invalid specialist patch: ${parsed.error.issues.map((i) => `${i.path.join('.')} ${i.message}`).join('; ')}`, 400, 'VALIDATION_ERROR');
      }
      const updated = specialistRegistry.update(req.params.id, parsed.data as SpecialistPatch);
      if (!updated) throw new ApiError(`Unknown specialist "${req.params.id}"`, 404, 'NOT_FOUND');
      res.json(updated);
    }),
  );

  router.delete(
    '/specialists/:id',
    asyncHandler(async (req, res) => {
      const specialist = specialistRegistry.get(req.params.id);
      if (!specialist) throw new ApiError(`Unknown specialist "${req.params.id}"`, 404, 'NOT_FOUND');
      if (specialist.builtIn) throw new ApiError('Built-in specialists cannot be deleted — disable them instead.', 409, 'BUILT_IN');
      if (specialist.versions.length > 0 && req.query.force !== 'true') {
        throw new ApiError(
          `${specialist.name} still has ${specialist.versions.length} trained version(s). Pass ?force=true to delete the specialist and its versions.`,
          409,
          'HAS_VERSIONS',
        );
      }
      for (const version of specialist.versions) versionStore.remove(version.id);
      const removed = specialistRegistry.remove(req.params.id);
      res.json({ removed });
    }),
  );

  router.post(
    '/specialists/:id/test',
    asyncHandler(async (req, res) => {
      const parsed = testSchema.safeParse(req.body ?? {});
      if (!parsed.success) throw new ApiError('A non-empty `input` string is required.', 400, 'VALIDATION_ERROR');
      const specialist = specialistRegistry.get(req.params.id);
      if (!specialist) throw new ApiError(`Unknown specialist "${req.params.id}"`, 404, 'NOT_FOUND');
      const result = await runSpecialist(req.params.id, parsed.data.input, {
        versionId: parsed.data.versionId,
        saveAsExample: parsed.data.saveAsExample,
        skipEscalation: parsed.data.skipEscalation,
        mode: 'playground',
      });
      res.json(result);
    }),
  );

  router.get(
    '/specialists/:id/examples',
    asyncHandler(async (req, res) => {
      res.json({ examples: listUserExamples(req.params.id) });
    }),
  );

  router.post(
    '/specialists/:id/examples',
    asyncHandler(async (req, res) => {
      const schema = z.object({ text: z.string().min(1).max(500), label: z.string().min(1).max(80) });
      const parsed = schema.safeParse(req.body ?? {});
      if (!parsed.success) throw new ApiError('Both `text` and `label` are required.', 400, 'VALIDATION_ERROR');
      res.status(201).json(addUserExample(req.params.id, { ...parsed.data, source: 'manual' }));
    }),
  );

  router.delete(
    '/specialists/:id/examples/:exampleId',
    asyncHandler(async (req, res) => {
      const removed = removeUserExample(req.params.id, req.params.exampleId);
      if (!removed) throw new ApiError('Example not found', 404, 'NOT_FOUND');
      res.json({ removed });
    }),
  );

  router.post(
    '/specialists/:id/normalize',
    asyncHandler(async (req, res) => {
      const schema = z.object({ text: z.string().min(1).max(4000) });
      const parsed = schema.safeParse(req.body ?? {});
      if (!parsed.success) throw new ApiError('A non-empty `text` string is required.', 400, 'VALIDATION_ERROR');
      res.json(normalizeSpeechFor(req.params.id, parsed.data.text));
    }),
  );

  // -------------------------------------------------------------------------
  // Training
  // -------------------------------------------------------------------------

  router.get(
    '/training/methods',
    asyncHandler(async (_req, res) => {
      res.json({ methods: methodInfos() });
    }),
  );

  router.get(
    '/training/datasets',
    asyncHandler(async (req, res) => {
      const specialistId = typeof req.query.specialistId === 'string' ? req.query.specialistId : undefined;
      if (specialistId) {
        const specialist = specialistRegistry.get(specialistId);
        if (!specialist) throw new ApiError(`Unknown specialist "${specialistId}"`, 404, 'NOT_FOUND');
        res.json({ datasets: datasetsForSpecialist(specialist.id, specialist.category) });
        return;
      }
      res.json({ datasets: listDatasets() });
    }),
  );

  router.get(
    '/training/datasets/:id/export',
    asyncHandler(async (req, res) => {
      const dataset = getDataset(req.params.id);
      if (!dataset) throw new ApiError(`Unknown dataset "${req.params.id}"`, 404, 'NOT_FOUND');
      const lines: string[] = [];
      const push = (instruction: string, output: unknown) => lines.push(JSON.stringify({ instruction, output }));
      for (const item of dataset.routing?.train ?? []) push(item.text, item.label);
      for (const doc of dataset.retrieval?.docs ?? []) push(doc.title, doc.text);
      for (const item of dataset.lexicon?.train ?? []) push(item.term, item.spoken);
      for (const item of dataset.normalization?.train ?? []) push(item.text, item.expected);
      res.setHeader('Content-Type', 'application/x-ndjson');
      res.setHeader('Content-Disposition', `attachment; filename="${dataset.info.id}.jsonl"`);
      res.send(lines.join('\n'));
    }),
  );

  router.get(
    '/training/runs',
    asyncHandler(async (req, res) => {
      const specialistId = typeof req.query.specialistId === 'string' ? req.query.specialistId : undefined;
      const status = typeof req.query.status === 'string' ? (req.query.status as never) : undefined;
      const limit = Math.min(Number(req.query.limit ?? 50) || 50, 200);
      res.json({ runs: runStore.list({ specialistId, status, limit }), counts: runStore.counts() });
    }),
  );

  router.get(
    '/training/runs/:id',
    asyncHandler(async (req, res) => {
      const run = runStore.get(req.params.id);
      if (!run) throw new ApiError(`Unknown run "${req.params.id}"`, 404, 'NOT_FOUND');
      res.json(run);
    }),
  );

  router.post(
    '/training/runs',
    asyncHandler(async (req, res) => {
      const parsed = runRequestSchema.safeParse(req.body);
      if (!parsed.success) {
        throw new ApiError(`Invalid training request: ${parsed.error.issues.map((i) => `${i.path.join('.')} ${i.message}`).join('; ')}`, 400, 'VALIDATION_ERROR');
      }
      const request = parsed.data as TrainingRunRequest;
      const method = getMethod(request.methodId);
      if (!method) throw new ApiError(`Unknown method "${request.methodId}"`, 400, 'UNKNOWN_METHOD');
      if (!method.available) {
        throw new ApiError(
          `${method.name} cannot run in this environment. Requirements: ${method.requirements.join('; ')}.`,
          409,
          'METHOD_UNAVAILABLE',
        );
      }
      const run = startTraining(request);
      res.status(202).json(run);
    }),
  );

  router.post(
    '/training/runs/:id/cancel',
    asyncHandler(async (req, res) => {
      const ok = cancelTraining(req.params.id);
      if (!ok) throw new ApiError('Run is not active', 409, 'NOT_ACTIVE');
      res.json({ cancelled: true });
    }),
  );

  router.delete(
    '/training/runs/:id',
    asyncHandler(async (req, res) => {
      const run = runStore.get(req.params.id);
      if (!run) throw new ApiError('Unknown run', 404, 'NOT_FOUND');
      if (run.status === 'running' || run.status === 'queued') throw new ApiError('Cancel the run before deleting it.', 409, 'RUN_ACTIVE');
      runStore.update(req.params.id, { notes: `${run.notes} [deleted]` });
      const removed = runStore.clearFinished();
      res.json({ removed });
    }),
  );

  router.get('/training/stream', (req: Request, res: Response) => {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    });
    res.write(`event: ready\ndata: ${JSON.stringify({ running: runStore.counts().running })}\n\n`);
    const onEvent = (event: unknown) => res.write(`event: training\ndata: ${JSON.stringify(event)}\n\n`);
    trainingBus.on('event', onEvent);
    const heartbeat = setInterval(() => res.write(': ping\n\n'), 20_000);
    req.on('close', () => {
      clearInterval(heartbeat);
      trainingBus.off('event', onEvent);
      res.end();
    });
  });

  // -------------------------------------------------------------------------
  // Model versions
  // -------------------------------------------------------------------------

  router.get(
    '/models/versions',
    asyncHandler(async (req, res) => {
      const specialistId = typeof req.query.specialistId === 'string' ? req.query.specialistId : undefined;
      const versions = specialistId ? versionStore.list(specialistId) : versionStore.listAll();
      res.json({
        versions,
        active: Object.fromEntries(
          specialistRegistry.list().map((s) => [s.id, versionStore.activeVersionId(s.id)]),
        ),
      });
    }),
  );

  router.get(
    '/models/versions/:id',
    asyncHandler(async (req, res) => {
      const version = versionStore.get(req.params.id);
      if (!version) throw new ApiError('Unknown version', 404, 'NOT_FOUND');
      const artifact = versionStore.artifact(version.id) as Record<string, unknown> | null;
      // Artifact inspection stays bounded: labels/statistics, never raw weights.
      const summary = artifact
        ? {
            kind: artifact.kind,
            formatVersion: artifact.formatVersion,
            labels: (artifact.labels as string[] | undefined)?.slice(0, 40) ?? undefined,
            featureDim: artifact.featureDim,
            trainedAt: artifact.trainedAt,
            bestEpoch: artifact.bestEpoch,
            trainingSize: artifact.trainingSize,
            hyperparameters: artifact.hyperparameters,
            stats: artifact.stats,
            docCount: artifact.docCount,
            vocabularySize: artifact.vocabularySize,
            hyperparametersBm25: artifact.hyperparametersBm25,
          }
        : null;
      res.json({ summary: version, artifact: summary, scorecards: benchmarkStore.list({ versionId: version.id, limit: 10 }) });
    }),
  );

  router.post(
    '/models/versions/:id/promote',
    asyncHandler(async (req, res) => {
      const version = versionStore.get(req.params.id);
      if (!version) throw new ApiError('Unknown version', 404, 'NOT_FOUND');
      const schema = z.object({ verify: z.boolean().optional(), force: z.boolean().optional() });
      const parsed = schema.safeParse(req.body ?? {});
      if (!parsed.success) throw new ApiError('Invalid payload', 400, 'VALIDATION_ERROR');

      let verification: Awaited<ReturnType<typeof verifyPromotion>> | null = null;
      if (parsed.data.verify !== false) {
        verification = await verifyPromotion(version.id);
        if (!verification.ok && !parsed.data.force) {
          throw new ApiError(
            `Promotion blocked: ${verification.message} Run the benchmark again after retraining, or force the promotion if you accept the regression.`,
            409,
            'REGRESSION',
          );
        }
      }
      const promoted = versionStore.promote(version.id, verification?.message ?? 'Promoted from the Training Lab');
      logger.info('versions', `Promoted ${version.id}`);
      res.json({ version: promoted, verification });
    }),
  );

  router.post(
    '/models/versions/:id/reject',
    asyncHandler(async (req, res) => {
      const version = versionStore.get(req.params.id);
      if (!version) throw new ApiError('Unknown version', 404, 'NOT_FOUND');
      if (version.status === 'active') throw new ApiError('The active version cannot be rejected — promote another version first.', 409, 'ACTIVE_VERSION');
      const updated = versionStore.setStatus(version.id, 'rejected', 'Rejected in the Training Lab');
      res.json(updated);
    }),
  );

  router.delete(
    '/models/versions/:id',
    asyncHandler(async (req, res) => {
      const version = versionStore.get(req.params.id);
      if (!version) throw new ApiError('Unknown version', 404, 'NOT_FOUND');
      if (version.status === 'active') throw new ApiError('Promote another version before deleting the active one.', 409, 'ACTIVE_VERSION');
      res.json({ removed: versionStore.remove(version.id) });
    }),
  );

  // -------------------------------------------------------------------------
  // Benchmarks
  // -------------------------------------------------------------------------

  router.get(
    '/benchmarks/suites',
    asyncHandler(async (_req, res) => {
      res.json({ suites: suiteInfos() });
    }),
  );

  router.get(
    '/benchmarks/scorecards',
    asyncHandler(async (req, res) => {
      const specialistId = typeof req.query.specialistId === 'string' ? req.query.specialistId : undefined;
      const suiteId = typeof req.query.suiteId === 'string' ? (req.query.suiteId as BenchmarkSuiteId) : undefined;
      const versionId = typeof req.query.versionId === 'string' ? req.query.versionId : undefined;
      const limit = Math.min(Number(req.query.limit ?? 30) || 30, 100);
      res.json({ scorecards: benchmarkStore.list({ specialistId, suiteId, versionId, limit }) });
    }),
  );

  router.get(
    '/benchmarks/scorecards/:id',
    asyncHandler(async (req, res) => {
      const card = benchmarkStore.get(req.params.id);
      if (!card) throw new ApiError('Unknown scorecard', 404, 'NOT_FOUND');
      res.json(card);
    }),
  );

  router.post(
    '/benchmarks/run',
    asyncHandler(async (req, res) => {
      const parsed = benchmarkRunSchema.safeParse(req.body);
      if (!parsed.success) throw new ApiError('Invalid benchmark request', 400, 'VALIDATION_ERROR');
      const request = parsed.data as BenchmarkRunRequest;
      if (!getSuite(request.suiteId as BenchmarkSuiteId)) throw new ApiError(`Unknown suite "${request.suiteId}"`, 404, 'NOT_FOUND');
      const card = await runBenchmark(request);
      const active = versionStore.activeVersion(card.specialistId);
      const baseline =
        active && active.id !== card.versionId ? benchmarkStore.latestFor(card.suiteId, active.id) : benchmarkStore.list({ suiteId: card.suiteId, specialistId: card.specialistId, limit: 20 }).find((c) => c.id !== card.id) ?? null;
      const comparison = baseline && baseline.id !== card.id ? benchmarkStore.buildComparison(card, baseline) : null;
      res.json({ scorecard: card, comparison });
    }),
  );

  router.get(
    '/benchmarks/compare',
    asyncHandler(async (req, res) => {
      const candidateId = String(req.query.candidateId ?? '');
      const baselineId = String(req.query.baselineId ?? '');
      const candidate = benchmarkStore.get(candidateId);
      if (!candidate) throw new ApiError('Unknown candidate scorecard', 404, 'NOT_FOUND');
      const baseline = baselineId ? benchmarkStore.get(baselineId) : null;
      res.json(benchmarkStore.buildComparison(candidate, baseline));
    }),
  );

  // -------------------------------------------------------------------------
  // Platform overview
  // -------------------------------------------------------------------------

  router.get(
    '/platform/overview',
    asyncHandler(async (_req, res) => {
      const specialists = specialistRegistry.list();
      const runs = runStore.list({ limit: 8 });
      const counts = runStore.counts();
      const scorecards = benchmarkStore.list({ limit: 12 });
      const versions = versionStore.listAll();
      const recentComparisons = scorecards
        .filter((card) => card.suiteId && versions.some((v) => v.id === card.versionId))
        .slice(0, 6)
        .map((card) => {
          const active = versionStore.activeVersion(card.specialistId);
          const baseline = active ? benchmarkStore.latestFor(card.suiteId, active.id) : null;
          return {
            scorecardId: card.id,
            specialistId: card.specialistId,
            suiteName: card.suiteName,
            verdict: baseline && baseline.id !== card.id ? benchmarkStore.buildComparison(card, baseline).verdict : 'no_baseline',
            quality: card.quality,
            createdAt: card.createdAt,
          };
        });

      res.json({
        specialists,
        runs,
        counts,
        scorecards,
        versions,
        recentComparisons,
        methods: methodInfos(),
        datasets: listDatasets(),
        suites: suiteInfos(),
        trainingAvailable: TRAINING_METHODS.filter((m) => m.available).length,
      });
    }),
  );

  return router;
}

export { verifyPromotion };
