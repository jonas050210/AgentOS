import { HyperparameterSpec, SpecialistCategory, TrainingMethodId, TrainingMethodInfo } from '../../shared/types.js';
import { autoFeatureDim, EpochReport, evaluateTaskHead, LabeledExample, TaskHead, trainTaskHead, TaskHeadArtifact } from '../ml/softmax.js';
import { Bm25Artifact, Bm25Index, evaluateRetrieval } from '../ml/retrieval.js';
import { induceLexicon, LexiconArtifact } from '../ml/lexicon.js';
import { calibrateDelegationThreshold } from '../ml/calibration.js';
import { planWithRules } from '../specialists/policy.js';
import { DatasetPayload, userExamplesAsLabeled } from './datasets.js';

export interface EpochLike {
  epoch: number;
  trainLoss: number;
  trainAccuracy: number;
  valLoss: number;
  valAccuracy: number;
  learningRate: number;
  durationMs: number;
  label?: string;
}

export interface MethodRunContext {
  runId: string;
  specialistId: string;
  specialistCategory: SpecialistCategory;
  dataset: DatasetPayload;
  hyperparameters: Record<string, number | boolean | string>;
  onEpoch: (epoch: EpochLike) => void;
  onCheckpoint: (epoch: number, artifact: unknown, valLoss: number, valAccuracy: number, note?: string) => void;
  shouldStop: () => boolean;
  log: (message: string, meta?: Record<string, unknown>) => void;
}

export interface MethodRunResult {
  artifact: unknown;
  artifactKind: 'task_head_sgd' | 'retrieval_index' | 'lexicon_induction';
  engineDescription: string;
  baseModel: string;
  primaryMetric: string;
  metrics: Record<string, number>;
  cancelled: boolean;
}

export interface TrainingMethodDefinition extends TrainingMethodInfo {
  /** Dataset kinds this method can consume. */
  datasetKinds: DatasetPayload['info']['kind'][];
  run: (ctx: MethodRunContext) => Promise<MethodRunResult>;
}

function hyper<T extends number | boolean | string>(ctx: MethodRunContext, key: string, fallback: T): T {
  const value = ctx.hyperparameters[key];
  if (value === undefined || value === null || value === '') return fallback;
  return value as T;
}

function num(ctx: MethodRunContext, key: string, fallback: number): number {
  const value = Number(hyper(ctx, key, fallback));
  return Number.isFinite(value) ? value : fallback;
}

function bool(ctx: MethodRunContext, key: string, fallback: boolean): boolean {
  return Boolean(hyper(ctx, key, fallback));
}

// ---------------------------------------------------------------------------
// 1. Task head — real gradient-based learning
// ---------------------------------------------------------------------------

const TASK_HEAD_HYPERPARAMETERS: HyperparameterSpec[] = [
  { key: 'epochs', label: 'Epochs', type: 'int', default: 120, min: 1, max: 400, description: 'Passes over the training split. Early stopping ends the run sooner when validation loss stops improving.' },
  { key: 'learningRate', label: 'Learning rate', type: 'float', default: 0.35, min: 0.001, max: 5, step: 0.01, description: 'Initial step size for mini-batch SGD; decays 15% per epoch.' },
  { key: 'l2', label: 'L2 regularisation', type: 'float', default: 0.0003, min: 0, max: 0.1, step: 0.0001, description: 'Weight decay that keeps the head from memorising the training split.', advanced: true },
  { key: 'batchSize', label: 'Batch size', type: 'int', default: 16, min: 1, max: 128, description: 'Examples per gradient update.', advanced: true },
  { key: 'momentum', label: 'Momentum', type: 'float', default: 0.9, min: 0, max: 0.99, step: 0.01, description: 'Smooths updates across batches.', advanced: true },
  { key: 'featureDim', label: 'Hashed feature space', type: 'int', default: 0, min: 0, max: 4096, description: 'Number of hashed features the head trains on. 0 chooses a size from the training set (about eight slots per example).', advanced: true },
  { key: 'patience', label: 'Early-stop patience', type: 'int', default: 25, min: 5, max: 200, description: 'Epochs without validation-loss improvement before training stops.', advanced: true },
  { key: 'valFraction', label: 'Validation split', type: 'float', default: 0.25, min: 0.1, max: 0.5, step: 0.05, description: 'Share of examples held back for early stopping.', advanced: true },
  { key: 'labelSmoothing', label: 'Label smoothing', type: 'float', default: 0.02, min: 0, max: 0.2, step: 0.01, description: 'Softens targets so the head stays calibrated on ambiguous instructions.', advanced: true },
  { key: 'includeUserExamples', label: 'Include my playground examples', type: 'boolean', default: true, description: 'Merges decisions you confirmed in the playground into the training split.' },
  { key: 'seed', label: 'Random seed', type: 'int', default: 42, min: 0, max: 99999, description: 'Seeds shuffling and initialisation so runs are reproducible.', advanced: true },
];

const taskHeadMethod: TrainingMethodDefinition = {
  id: 'task_head_sgd',
  name: 'Task head · softmax + SGD',
  kind: 'gradient',
  runtime: 'in-process',
  available: true,
  description:
    'Trains a compact multiclass head on hashed n-gram features with real mini-batch SGD: cross-entropy loss, momentum, balanced class weights, L2 decay and early stopping. It learns which tool or strategy resolves an instruction, so the specialist can answer locally instead of escalating.',
  artifactKind: 'task_head_sgd · linear softmax over hashed n-gram features',
  requirements: ['None — runs in-process in milliseconds'],
  appliesTo: ['coding', 'research', 'general', 'custom', 'knowledge'],
  honesty:
    'This is genuine gradient descent, but it is not fine-tuning a language model: the head is a small linear classifier. It improves routing and tool selection; free-form prose still escalates to a model provider.',
  datasetKinds: ['routing'],
  hyperparameters: TASK_HEAD_HYPERPARAMETERS,
  async run(ctx): Promise<MethodRunResult> {
    const payload = ctx.dataset.routing;
    if (!payload || payload.train.length === 0) throw new Error('This dataset has no routing examples to train on.');

    const includeUser = bool(ctx, 'includeUserExamples', true);
    const userExamples = includeUser ? userExamplesAsLabeled(ctx.specialistId) : [];
    const expressionBonus: LabeledExample[] = includeUser
      ? (ctx.dataset.expressions?.train ?? []).map((e) => ({ text: e.text, label: 'calc.evaluate' }))
      : [];

    const examples: LabeledExample[] = [
      ...payload.train.map((item) => ({ text: item.text, label: item.label })),
      ...expressionBonus,
      ...userExamples,
    ];
    const classes = [...new Set(examples.map((e) => e.label))].sort();
    if (classes.length < 2) throw new Error('Routing training needs at least two distinct decision labels.');
    // 0 means "size it from the data"; an explicit value always wins.
    const requestedFeatureDim = Math.round(num(ctx, 'featureDim', 0));

    const featureDim = requestedFeatureDim > 0 ? requestedFeatureDim : autoFeatureDim(examples.length);
    ctx.log(`Training head on ${examples.length} examples across ${classes.length} labels`, {
      classes,
      featureDim,
      userExamples: userExamples.length,
    });

    const result = trainTaskHead({
      examples,
      hyperparameters: {
        epochs: num(ctx, 'epochs', 120),
        learningRate: num(ctx, 'learningRate', 0.35),
        l2: num(ctx, 'l2', 3e-4),
        batchSize: num(ctx, 'batchSize', 16),
        momentum: num(ctx, 'momentum', 0.9),
        featureDim,
        patience: num(ctx, 'patience', 25),
        seed: num(ctx, 'seed', 42),
        valFraction: num(ctx, 'valFraction', 0.25),
        labelSmoothing: num(ctx, 'labelSmoothing', 0.02),
      },
      onEpoch: (report: EpochReport) => ctx.onEpoch(report),
      // Real parameter snapshots: the head's best parameters at that point in training.
      checkpointEvery: Math.max(1, Math.round(num(ctx, 'epochs', 60) / 4)),
      onCheckpoint: (snapshot) =>
        ctx.onCheckpoint(
          snapshot.epoch,
          {
            kind: 'task_head_sgd',
            formatVersion: 1,
            featureDim: snapshot.featureDim,
            labels: snapshot.labels,
            weights: snapshot.weights.map((row) => row.map((v) => round(v, 6))),
            bias: snapshot.bias.map((v) => round(v, 6)),
            trainedAt: new Date().toISOString(),
            trainingSize: examples.length,
            bestEpoch: snapshot.epoch,
            labelBalance: {},
          },
          snapshot.valLoss,
          snapshot.valAccuracy,
          snapshot.isBest ? 'Best validation parameters so far' : 'Superseded by a later epoch',
        ),
      shouldStop: ctx.shouldStop,
    });

    const head = new TaskHead(result.artifact);
    const holdout = payload.evaluation.map((item) => ({ text: item.text, label: item.label }));
    const holdoutEval = holdout.length > 0 ? evaluateTaskHead(head, holdout) : null;
    const last = result.history[result.history.length - 1];
    const best = result.history.reduce((acc, r) => (r.valLoss < acc.valLoss ? r : acc), result.history[0] ?? { valLoss: 1, valAccuracy: 0, epoch: 0, trainLoss: 0, trainAccuracy: 0, learningRate: 0, durationMs: 0 });

    // Delegation calibration: how confident must this head be before its answer
    // is better than the deterministic rule policy? Measured on the validation
    // split, so it is a genuine generalisation measurement, not a training score.
    const allowedToolIds = ctx.dataset.info.labels;
    const calibration = calibrateDelegationThreshold(
      head,
      payload.validation.map((item) => ({
        text: item.text,
        expected: item.label,
        policyLabel: planWithRules(item.text, allowedToolIds, ctx.specialistId)?.toolId ?? null,
      })),
    );
    if (calibration) {
      result.artifact.calibration = {
        threshold: calibration.threshold,
        accuracy: calibration.accuracy,
        examples: calibration.examples,
        headAnswered: calibration.headAnswered,
        policyAnswered: calibration.policyAnswered,
        escalated: calibration.escalated,
      };
    }

    const metrics: Record<string, number> = {
      holdoutAccuracy: holdoutEval?.accuracy ?? 0,
      holdoutMacroF1: holdoutEval?.macroF1 ?? 0,
      valAccuracy: best.valAccuracy,
      valLoss: best.valLoss,
      finalTrainAccuracy: last?.trainAccuracy ?? 0,
      finalTrainLoss: last?.trainLoss ?? 0,
      epochsRun: result.history.length,
      trainSize: result.trainedSize,
      validationSize: result.validationSize,
      holdoutSize: holdout.length,
      labels: classes.length,
      parameters: classes.length * result.artifact.featureDim + classes.length,
      calibratedThreshold: calibration?.threshold ?? 0,
      calibratedAccuracy: calibration?.accuracy ?? 0,
      calibrationExamples: calibration?.examples ?? 0,
      calibratedEscalations: calibration?.escalated ?? 0,
    };

    ctx.log(
      `Head trained in ${result.history.length} epochs — held-out accuracy ${(metrics.holdoutAccuracy * 100).toFixed(1)}%`,
      { bestEpoch: result.artifact.bestEpoch, userExamples: userExamples.length },
    );

    return {
      artifact: result.artifact satisfies TaskHeadArtifact,
      artifactKind: 'task_head_sgd',
      engineDescription: `task head · softmax SGD · ${result.artifact.featureDim} hashed features · ${classes.length} labels · calibrated @ ${(calibration?.threshold ?? 0).toFixed(2)}`,
      baseModel: 'hashed-ngram-features',
      primaryMetric: 'holdoutAccuracy',
      metrics,
      cancelled: result.cancelled,
    };
  },
};

// ---------------------------------------------------------------------------
// 2. Retrieval index
// ---------------------------------------------------------------------------

const RETRIEVAL_HYPERPARAMETERS: HyperparameterSpec[] = [
  { key: 'k1', label: 'Term saturation (k1)', type: 'float', default: 1.2, min: 0.1, max: 3, step: 0.1, description: 'BM25 term-frequency saturation. Higher values reward repeated terms more.' },
  { key: 'b', label: 'Length normalisation (b)', type: 'float', default: 0.75, min: 0, max: 1, step: 0.05, description: 'How strongly document length is normalised.' },
  { key: 'minDf', label: 'Minimum document frequency', type: 'int', default: 1, min: 1, max: 5, description: 'Drops terms that appear in fewer documents — pruning reduces noise.', advanced: true },
  { key: 'titleBoost', label: 'Title weight', type: 'float', default: 2, min: 1, max: 5, step: 0.5, description: 'Extra weight for terms in a document title.', advanced: true },
  { key: 'charNgrams', label: 'Character n-grams', type: 'boolean', default: true, description: 'Adds character 4-grams so typos and inflections still match.' },
  { key: 'repeats', label: 'Optimisation passes', type: 'int', default: 3, min: 1, max: 10, description: 'Searches nearby k1/b values and keeps the best recall@k on the held-out queries.' },
];

const retrievalMethod: TrainingMethodDefinition = {
  id: 'retrieval_index',
  name: 'Retrieval index · BM25',
  kind: 'index',
  runtime: 'in-process',
  available: true,
  description:
    'Builds a BM25 index over a document corpus and tunes its parameters against held-out queries. Index construction is a single pass; the optimisation loop searches k1/b combinations and keeps the configuration with the best recall@k.',
  artifactKind: 'retrieval_index · BM25 postings with tuned parameters',
  requirements: ['None — runs in-process'],
  appliesTo: ['knowledge', 'research', 'custom'],
  honesty:
    'No neural embeddings are involved: this is a sparse lexical index. It is genuinely built and tuned from your corpus, and every reported figure comes from held-out queries.',
  datasetKinds: ['retrieval'],
  hyperparameters: RETRIEVAL_HYPERPARAMETERS,
  async run(ctx): Promise<MethodRunResult> {
    const payload = ctx.dataset.retrieval;
    if (!payload || payload.docs.length === 0) throw new Error('This dataset has no documents to index.');

    const k1s = [num(ctx, 'k1', 1.2)];
    const bs = [num(ctx, 'b', 0.75)];
    const passes = Math.max(1, Math.round(num(ctx, 'repeats', 3)));
    for (let i = 1; i < passes; i++) {
      k1s.push(round2(Math.max(0.1, k1s[0] * (1 + 0.25 * i))));
      bs.push(round2(Math.min(1, bs[0] + 0.1 * i)));
    }

    let best: { index: Bm25Index; k1: number; b: number; recall: number } | null = null;
    let epoch = 0;
    for (const k1 of k1s) {
      for (const b of bs) {
        if (ctx.shouldStop()) {
          break;
        }
        epoch++;
        const started = Date.now();
        const index = Bm25Index.build(payload.docs, {
          k1,
          b,
          minDf: Math.round(num(ctx, 'minDf', 1)),
          titleBoost: num(ctx, 'titleBoost', 2),
          charNgrams: bool(ctx, 'charNgrams', true),
        });
        const metrics = evaluateRetrieval(index, payload.evaluation, 5);
        ctx.onEpoch({
          epoch,
          trainLoss: round2(1 - metrics.recallAtK),
          trainAccuracy: metrics.recallAtK,
          valLoss: round2(1 - metrics.mrr),
          valAccuracy: metrics.recallAtK,
          learningRate: k1,
          durationMs: Date.now() - started,
        });
        if (!best || metrics.recallAtK > best.recall) best = { index, k1, b, recall: metrics.recallAtK };
      }
    }
    if (!best) throw new Error('Index build was cancelled before any configuration completed.');

    const artifact = best.index.toArtifact();
    const finalMetrics = evaluateRetrieval(best.index, payload.evaluation, 5);
    const metrics: Record<string, number> = {
      recallAt5: finalMetrics.recallAtK,
      mrr: finalMetrics.mrr,
      precisionAt5: finalMetrics.precisionAtK,
      hitRate: finalMetrics.hitRate,
      documents: payload.docs.length,
      vocabulary: artifact.vocabularySize,
      queries: finalMetrics.queries,
      k1: best.k1,
      b: best.b,
    };
    ctx.onCheckpoint(epoch, artifact, round2(1 - finalMetrics.mrr), finalMetrics.recallAtK, 'Index frozen at the best configuration');

    ctx.log(`Index built over ${payload.docs.length} documents — recall@5 ${(finalMetrics.recallAtK * 100).toFixed(1)}%`, {
      vocabulary: artifact.vocabularySize,
      configurations: epoch,
    });

    return {
      artifact: artifact satisfies Bm25Artifact,
      artifactKind: 'retrieval_index',
      engineDescription: `BM25 index · ${payload.docs.length} docs · ${artifact.vocabularySize} terms · k1=${best.k1} b=${best.b}`,
      baseModel: 'bm25-sparse-index',
      primaryMetric: 'recallAt5',
      metrics,
      cancelled: false,
    };
  },
};

// ---------------------------------------------------------------------------
// 3. Lexicon induction
// ---------------------------------------------------------------------------

const LEXICON_HYPERPARAMETERS: HyperparameterSpec[] = [
  { key: 'minConfidence', label: 'Minimum confidence', type: 'float', default: 0.6, min: 0.1, max: 1, step: 0.05, description: 'Agreement threshold a term must reach before it is learned.' },
  { key: 'minCount', label: 'Minimum occurrences', type: 'int', default: 1, min: 1, max: 10, description: 'How often a term must appear in the corpus.', advanced: true },
  { key: 'pruneThreshold', label: 'Prune competing readings below', type: 'int', default: 0, min: 0, max: 5, description: 'Drops ambiguous readings with very little support.', advanced: true },
  { key: 'caseSensitive', label: 'Case-sensitive terms', type: 'boolean', default: false, description: 'When off, "API" and "api" share one lexicon entry.', advanced: true },
];

const lexiconMethod: TrainingMethodDefinition = {
  id: 'lexicon_induction',
  name: 'Speech lexicon · induction',
  kind: 'statistical',
  runtime: 'in-process',
  available: true,
  description:
    'Learns a pronunciation lexicon from aligned (abbreviation → spoken form) examples with their contexts. Candidates are counted, scored for agreement and pruned over multiple passes; the held-out share reports exact-match accuracy.',
  artifactKind: 'lexicon_induction · term → spoken form table',
  requirements: ['None — runs in-process'],
  appliesTo: ['voice', 'custom'],
  honesty:
    'Statistical induction, not gradient descent: counts and agreement plus context evidence decide which reading wins. Numeric and symbol expansion stays rule-based and is labelled as such in the UI.',
  datasetKinds: ['lexicon'],
  hyperparameters: LEXICON_HYPERPARAMETERS,
  async run(ctx): Promise<MethodRunResult> {
    const payload = ctx.dataset.lexicon;
    if (!payload || payload.train.length === 0) throw new Error('This dataset has no aligned lexicon examples.');

    const result = induceLexicon(payload.train, {
      minConfidence: num(ctx, 'minConfidence', 0.6),
      minCount: Math.round(num(ctx, 'minCount', 1)),
      pruneThreshold: Math.round(num(ctx, 'pruneThreshold', 0)),
      caseSensitive: bool(ctx, 'caseSensitive', false),
    }, {
      // Held-out sentences (same terms, unseen contexts) drive the reported score.
      validation: payload.evaluation,
      onEpoch: (report) =>
        ctx.onEpoch({
          epoch: report.epoch,
          trainLoss: round2(1 - report.accepted / Math.max(1, report.accepted + report.rejected)),
          trainAccuracy: report.valExactMatch,
          valLoss: round2(1 - report.valExactMatch),
          valAccuracy: report.valExactMatch,
          learningRate: 0,
          durationMs: 0,
          label: `${report.learned} learned · ${report.rejected} rejected`,
        }),
      shouldStop: ctx.shouldStop,
    });

    // Held-out score against the curated evaluation split (never trained on).
    const holdout = payload.evaluation;
    let correct = 0;
    for (const example of holdout) {
      const entry = result.artifact.entries[example.term.toLowerCase()];
      if (entry && entry.spoken.toLowerCase() === example.spoken.toLowerCase()) correct++;
    }
    const holdoutExact = holdout.length === 0 ? 0 : correct / holdout.length;

    const metrics: Record<string, number> = {
      holdoutExactMatch: round(holdoutExact, 4),
      validationExactMatch: result.validationExactMatch,
      coverage: result.coverage,
      learnedEntries: result.artifact.stats.learned,
      rejectedEntries: result.artifact.stats.rejected,
      meanConfidence: result.artifact.stats.meanConfidence,
      trainingPairs: payload.train.length,
      holdoutPairs: holdout.length,
    };
    ctx.onCheckpoint(result.history.length, result.artifact, round2(1 - result.validationExactMatch), result.validationExactMatch, 'Final induced lexicon');

    ctx.log(`Lexicon induced — ${result.artifact.stats.learned} entries, held-out exact match ${(holdoutExact * 100).toFixed(1)}%`);

    return {
      artifact: result.artifact satisfies LexiconArtifact,
      artifactKind: 'lexicon_induction',
      engineDescription: `pronunciation lexicon · ${result.artifact.stats.learned} entries · + deterministic number/symbol rules`,
      baseModel: 'lexicon-induction',
      primaryMetric: 'holdoutExactMatch',
      metrics,
      cancelled: false,
    };
  },
};

// ---------------------------------------------------------------------------
// 4. External adapter fine-tuning (declared, not runnable here)
// ---------------------------------------------------------------------------

const EXTERNAL_HYPERPARAMETERS: HyperparameterSpec[] = [
  { key: 'baseModel', label: 'Base model', type: 'enum', default: 'llama3.2', options: ['llama3.2', 'qwen2.5:7b', 'mistral:7b'], description: 'Checkpoint to adapt. Must be available locally.' },
  { key: 'rank', label: 'LoRA rank', type: 'int', default: 16, min: 4, max: 128, description: 'Rank of the low-rank adapters.' },
  { key: 'learningRate', label: 'Learning rate', type: 'float', default: 0.0002, min: 0.000001, max: 0.01, step: 0.00001, description: 'Optimiser step size.' },
  { key: 'epochs', label: 'Epochs', type: 'int', default: 3, min: 1, max: 20, description: 'Passes over the exported dataset.' },
];

const adapterMethod: TrainingMethodDefinition = {
  id: 'llm_adapter_finetune',
  name: 'Adapter fine-tuning (LoRA/PEFT)',
  kind: 'external',
  runtime: 'external',
  available: false,
  description:
    'Fine-tunes a base language model with low-rank adapters using an external Python toolchain. AgentOS can export a dataset in the required JSONL format and track the resulting adapter as a version, but the training itself runs outside this process.',
  artifactKind: 'lora_adapter (produced by the external trainer)',
  requirements: [
    'Python 3.11 with torch, transformers and peft installed',
    'A local base checkpoint that fits in available VRAM (GPU strongly recommended)',
    'The external trainer (e.g. an Unsloth/LLaMA-Factory script) pointed at the exported dataset',
  ],
  appliesTo: ['coding', 'research', 'knowledge', 'general', 'custom', 'voice'],
  honesty:
    'NOT RUNNABLE in this build. It is declared so the interface and dataset export exist, and it is reported as unavailable everywhere it appears. Nothing about it is simulated.',
  datasetKinds: ['routing', 'retrieval', 'normalization', 'lexicon'],
  hyperparameters: EXTERNAL_HYPERPARAMETERS,
  async run(): Promise<MethodRunResult> {
    throw Object.assign(
      new Error(
        'Adapter fine-tuning requires an external Python toolchain (torch + peft) and a base checkpoint, which is not installed in this environment. AgentOS refuses to simulate it.',
      ),
      { code: 'METHOD_UNAVAILABLE' },
    );
  },
};

function round(value: number, digits = 4): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}
function round2(value: number, digits = 4): number {
  return round(value, digits);
}

export const TRAINING_METHODS: TrainingMethodDefinition[] = [taskHeadMethod, retrievalMethod, lexiconMethod, adapterMethod];

export function getMethod(id: TrainingMethodId): TrainingMethodDefinition | undefined {
  return TRAINING_METHODS.find((m) => m.id === id);
}

export function methodInfos(): TrainingMethodInfo[] {
  return TRAINING_METHODS.map(({ run: _run, datasetKinds: _kinds, ...info }) => info);
}
