import { createRng, round, shuffle, stratifiedSplit } from './rng.js';
import { DEFAULT_VECTORIZER, HashingVectorizer, SparseVector, toDense } from './features.js';

export interface LabeledExample {
  text: string;
  label: string;
}

export interface SoftmaxHyperparameters {
  epochs: number;
  learningRate: number;
  l2: number;
  batchSize: number;
  momentum: number;
  featureDim: number;
  seed: number;
  patience: number;
  labelSmoothing: number;
  valFraction: number;
}

export const DEFAULT_SOFTMAX_HP: SoftmaxHyperparameters = {
  epochs: 60,
  learningRate: 0.8,
  // Small corpora overfit quickly; a firmer L2 keeps the held-out split honest.
  l2: 3e-4,
  batchSize: 16,
  momentum: 0.9,
  featureDim: DEFAULT_VECTORIZER.featureDim,
  seed: 42,
  patience: 10,
  labelSmoothing: 0.02,
  valFraction: 0.25,
};

export interface TaskHeadArtifact {
  kind: 'task_head_sgd';
  formatVersion: 1;
  featureDim: number;
  labels: string[];
  /** Row-major [class][feature] weights. */
  weights: number[][];
  bias: number[];
  trainedAt: string;
  trainingSize: number;
  bestEpoch: number;
  hyperparameters: SoftmaxHyperparameters;
  labelBalance: Record<string, number>;
  /**
   * Measured delegation threshold for this head (see ml/calibration.ts). When
   * present the decision chain trusts the head at or above it instead of using
   * the global default from Settings.
   */
  calibration?: {
    threshold: number;
    accuracy: number;
    examples: number;
    headAnswered: number;
    policyAnswered: number;
    escalated: number;
  };
}

export interface EpochReport {
  epoch: number;
  trainLoss: number;
  trainAccuracy: number;
  valLoss: number;
  valAccuracy: number;
  learningRate: number;
  durationMs: number;
}

export interface SoftmaxTrainingResult {
  artifact: TaskHeadArtifact;
  history: EpochReport[];
  trainedSize: number;
  validationSize: number;
  stoppedEarly: boolean;
  cancelled: boolean;
}

function softmax(scores: Float64Array): Float64Array {
  let max = -Infinity;
  for (const s of scores) if (s > max) max = s;
  const out = new Float64Array(scores.length);
  let sum = 0;
  for (let i = 0; i < scores.length; i++) {
    const e = Math.exp(scores[i] - max);
    out[i] = e;
    sum += e;
  }
  for (let i = 0; i < out.length; i++) out[i] /= sum || 1;
  return out;
}

/** Compact multiclass linear model trained with mini-batch SGD + momentum. */
export class TaskHead {
  constructor(readonly artifact: TaskHeadArtifact) {}

  get labels(): string[] {
    return this.artifact.labels;
  }

  get featureDim(): number {
    return this.artifact.featureDim;
  }

  /** Scores → probabilities for a dense or sparse feature vector. */
  probabilitiesFor(input: Float64Array | SparseVector): Float64Array {
    const denseVec = input instanceof Float64Array ? input : toDense(input, this.artifact.featureDim);
    const { weights, bias, labels } = this.artifact;
    const scores = new Float64Array(labels.length);
    for (let c = 0; c < labels.length; c++) {
      let sum = bias[c];
      const row = weights[c];
      for (let f = 0; f < row.length; f++) {
        const value = denseVec[f];
        if (value !== 0) sum += row[f] * value;
      }
      scores[c] = sum;
    }
    return softmax(scores);
  }

  predictFromDense(dense: Float64Array): { label: string; confidence: number; probabilities: Float64Array } {
    const probs = this.probabilitiesFor(dense);
    let best = 0;
    for (let i = 1; i < probs.length; i++) if (probs[i] > probs[best]) best = i;
    return { label: this.artifact.labels[best], confidence: probs[best], probabilities: probs };
  }

  predict(text: string, vectorizer: HashingVectorizer): { label: string; confidence: number; probabilities: Float64Array } {
    return this.predictFromDense(toDense(vectorizer.vectorize(text), this.artifact.featureDim));
  }
}

export interface CheckpointSnapshot {
  epoch: number;
  featureDim: number;
  labels: string[];
  weights: number[][];
  bias: number[];
  valLoss: number;
  valAccuracy: number;
  /** True when this snapshot holds the best validation parameters so far. */
  isBest: boolean;
}

export interface TrainingOptions {
  examples: LabeledExample[];
  hyperparameters?: Partial<SoftmaxHyperparameters>;
  onEpoch?: (report: EpochReport) => void;
  /** Called every `checkpointEvery` epochs with the current best parameters. */
  onCheckpoint?: (snapshot: CheckpointSnapshot) => void;
  checkpointEvery?: number;
  shouldStop?: () => boolean;
  /** Optional explicit validation split; when omitted a stratified split is made. */
  validation?: LabeledExample[];
}

/**
 * Trains a softmax (multinomial logistic regression) head on labelled text.
 *
 * This is real gradient-based learning: cross-entropy loss, mini-batch SGD with
 * momentum, L2 regularisation, balanced class weights, learning-rate decay and
 * early stopping on a held-out split. It is intentionally compact so AgentOS can
 * train it locally in milliseconds without a Python/GPU dependency.
 */
export function trainTaskHead(options: TrainingOptions): SoftmaxTrainingResult {
  const hp: SoftmaxHyperparameters = { ...DEFAULT_SOFTMAX_HP, ...options.hyperparameters };
  const rng = createRng(hp.seed);
  const vectorizer = new HashingVectorizer({ featureDim: hp.featureDim });

  const all = options.examples.filter((e) => e.text.trim().length > 0);
  if (all.length < 4) throw new Error('At least 4 labelled examples are required to train a task head.');

  let trainSet = all;
  let valSet: LabeledExample[] = options.validation ?? [];
  if (valSet.length === 0) {
    const split = stratifiedSplit(all, (e) => e.label, hp.valFraction, rng);
    trainSet = split.train;
    valSet = split.val;
  }
  if (valSet.length === 0) throw new Error('Validation split is empty — provide more examples or a lower valFraction.');

  const labels = [...new Set(all.map((e) => e.label))].sort();
  if (labels.length < 2) throw new Error('Training requires at least two distinct labels.');
  const labelIndex = new Map(labels.map((l, i) => [l, i]));

  const dim = vectorizer.config.featureDim;
  const dense = (items: LabeledExample[]) => items.map((e) => toDense(vectorizer.vectorize(e.text), dim));
  const trainX = dense(trainSet);
  const trainY = trainSet.map((e) => labelIndex.get(e.label)!);
  const valX = dense(valSet);
  const valY = valSet.map((e) => labelIndex.get(e.label)!);

  // Balanced class weights: inverse label frequency, normalised to mean 1.
  const counts = new Array(labels.length).fill(0);
  for (const y of trainY) counts[y]++;
  const weightsPerClass = counts.map((c) => (c === 0 ? 0 : trainY.length / (counts.length * c)));
  const meanWeight = weightsPerClass.reduce((a, b) => a + b, 0) / Math.max(1, weightsPerClass.filter((w) => w > 0).length);
  for (let i = 0; i < weightsPerClass.length; i++) if (weightsPerClass[i] > 0) weightsPerClass[i] /= meanWeight || 1;

  let weights: Float64Array[] = labels.map(() => new Float64Array(dim));
  let bias = new Float64Array(labels.length);
  const velocityW = labels.map(() => new Float64Array(dim));
  const velocityB = new Float64Array(labels.length);

  let best = { loss: Infinity, weights: weights.map((w) => w.slice()), bias: bias.slice(), epoch: 0 };
  let patienceLeft = hp.patience;
  const history: EpochReport[] = [];
  let cancelled = false;

  const crossEntropy = (X: Float64Array[], Y: number[], W: Float64Array[], B: Float64Array) => {
    let loss = 0;
    let correct = 0;
    for (let i = 0; i < X.length; i++) {
      const probs = forward(X[i], W, B);
      const y = Y[i];
      const target = 1 - hp.labelSmoothing;
      const smoothed = hp.labelSmoothing / labels.length;
      loss -= weightsPerClass[y] * (target * Math.log(probs[y] + 1e-12) + smoothed * sumLogOthers(probs, y));
      if (argmax(probs) === y) correct++;
    }
    return { loss: loss / Math.max(1, X.length), accuracy: correct / Math.max(1, X.length) };
  };

  // Gradients are accumulated over a mini-batch and applied with momentum.
  const batchGradW = labels.map(() => new Float64Array(dim));
  const batchGradB = new Float64Array(labels.length);

  for (let epoch = 1; epoch <= hp.epochs; epoch++) {
    const epochStart = Date.now();
    if (options.shouldStop?.()) {
      cancelled = true;
      break;
    }
    const lr = hp.learningRate / (1 + 0.15 * (epoch - 1));
    const order = shuffle(
      Array.from({ length: trainX.length }, (_, i) => i),
      rng,
    );

    for (let start = 0; start < order.length; start += hp.batchSize) {
      const batch = order.slice(start, start + hp.batchSize);
      for (let c = 0; c < labels.length; c++) {
        batchGradW[c].fill(0);
      }
      batchGradB.fill(0);

      for (const i of batch) {
        const probs = forward(trainX[i], weights, bias);
        const y = trainY[i];
        const weight = weightsPerClass[y];
        for (let c = 0; c < labels.length; c++) {
          const target = c === y ? 1 - hp.labelSmoothing : hp.labelSmoothing / labels.length;
          const grad = weight * (probs[c] - target);
          if (grad === 0) continue;
          const row = batchGradW[c];
          const x = trainX[i];
          for (let f = 0; f < dim; f++) {
            const value = x[f];
            if (value !== 0) row[f] += grad * value;
          }
          batchGradB[c] += grad;
        }
      }

      const scale = lr / batch.length;
      for (let c = 0; c < labels.length; c++) {
        const row = weights[c];
        const gradRow = batchGradW[c];
        const velRow = velocityW[c];
        for (let f = 0; f < dim; f++) {
          // L2 decay applies to every weight, including those without a
          // gradient contribution in this batch.
          const grad = scale * gradRow[f] + hp.l2 * row[f];
          velRow[f] = hp.momentum * velRow[f] - grad;
          row[f] += velRow[f];
        }
        velocityB[c] = hp.momentum * velocityB[c] - scale * batchGradB[c];
        bias[c] += velocityB[c];
      }
    }

    const trainStats = crossEntropy(trainX, trainY, weights, bias);
    const valStats = crossEntropy(valX, valY, weights, bias);
    const report: EpochReport = {
      epoch,
      trainLoss: round(trainStats.loss, 5),
      trainAccuracy: round(trainStats.accuracy, 4),
      valLoss: round(valStats.loss, 5),
      valAccuracy: round(valStats.accuracy, 4),
      learningRate: round(lr, 5),
      durationMs: Date.now() - epochStart,
    };
    history.push(report);
    options.onEpoch?.(report);

    const checkpointEvery = Math.max(1, Math.round(options.checkpointEvery ?? 0));
    if (options.onCheckpoint && checkpointEvery > 0 && epoch % checkpointEvery === 0) {
      options.onCheckpoint({
        epoch,
        featureDim: dim,
        labels: labels.slice(),
        weights: best.weights.map((row) => Array.from(row)),
        bias: Array.from(best.bias),
        valLoss: report.valLoss,
        valAccuracy: report.valAccuracy,
        isBest: Math.abs(report.valLoss - round(best.loss, 5)) < 1e-9,
      });
    }

    if (valStats.loss < best.loss - 1e-6) {
      best = {
        loss: valStats.loss,
        weights: weights.map((w) => w.slice()),
        bias: bias.slice(),
        epoch,
      };
      patienceLeft = hp.patience;
    } else {
      patienceLeft--;
      if (patienceLeft <= 0 && epoch < hp.epochs) {
        // Early stop: restore the best parameters seen so far.
        weights = best.weights.map((w) => w.slice());
        bias = best.bias.slice();
        break;
      }
    }
  }

  // `best` is guaranteed to hold the first-epoch snapshot; if training was
  // cancelled before a single epoch completed we still persist the current
  // (untrained) parameters so the artifact stays structurally valid.
  const finalWeights = history.length === 0 ? weights : best.weights;
  const finalBias = history.length === 0 ? bias : best.bias;

  const artifact: TaskHeadArtifact = {
    kind: 'task_head_sgd',
    formatVersion: 1,
    featureDim: dim,
    labels,
    weights: finalWeights.map((row) => Array.from(row, (v) => round(v, 6))),
    bias: Array.from(finalBias, (v) => round(v, 6)),
    trainedAt: new Date().toISOString(),
    trainingSize: trainSet.length,
    bestEpoch: history.length === 0 ? 0 : best.epoch,
    hyperparameters: hp,
    labelBalance: Object.fromEntries(labels.map((l, i) => [l, counts[i]])),
  };

  return {
    artifact,
    history,
    trainedSize: trainSet.length,
    validationSize: valSet.length,
    stoppedEarly: history.length < hp.epochs,
    cancelled,
  };

  function forward(x: Float64Array, W: Float64Array[], B: Float64Array): Float64Array {
    const scores = new Float64Array(labels.length);
    for (let c = 0; c < labels.length; c++) {
      let sum = B[c];
      const row = W[c];
      for (let f = 0; f < dim; f++) {
        const value = x[f];
        if (value !== 0) sum += row[f] * value;
      }
      scores[c] = sum;
    }
    return softmax(scores);
  }
}

function argmax(values: Float64Array): number {
  let best = 0;
  for (let i = 1; i < values.length; i++) if (values[i] > values[best]) best = i;
  return best;
}

function sumLogOthers(probs: Float64Array, exclude: number): number {
  let sum = 0;
  for (let i = 0; i < probs.length; i++) {
    if (i === exclude) continue;
    sum += Math.log(probs[i] + 1e-12);
  }
  return sum;
}

export interface ClassMetrics {
  label: string;
  precision: number;
  recall: number;
  f1: number;
  support: number;
  predicted: number;
}

export interface EvaluationResult {
  accuracy: number;
  macroF1: number;
  loss: number;
  perClass: ClassMetrics[];
  confusion: { labels: string[]; matrix: number[][] };
  size: number;
}

/**
 * Evaluates a head on labelled examples: accuracy, macro-F1, per-class
 * precision/recall and the confusion matrix (used by benchmark scorecards).
 */
export function evaluateTaskHead(
  head: TaskHead,
  examples: LabeledExample[],
  vectorizer = new HashingVectorizer({ featureDim: head.featureDim }),
): EvaluationResult {
  const labels = head.labels;
  const index = new Map(labels.map((l, i) => [l, i]));
  const confusion = labels.map(() => new Array(labels.length).fill(0));
  const predictedCounts = new Array(labels.length).fill(0);
  let correct = 0;
  let loss = 0;

  for (const example of examples) {
    const { label, probabilities } = head.predict(example.text, vectorizer);
    const actualIndex = index.get(example.label);
    if (actualIndex === undefined) {
      // Unknown label at evaluation time counts as a miss, never as a pass.
      continue;
    }
    const predictedIndex = index.get(label)!;
    confusion[actualIndex][predictedIndex]++;
    predictedCounts[predictedIndex]++;
    if (predictedIndex === actualIndex) correct++;
    loss -= Math.log((probabilities[actualIndex] ?? 0) + 1e-12);
  }

  const perClass: ClassMetrics[] = labels.map((label, i) => {
    const truePositive = confusion[i][i];
    const support = confusion[i].reduce((a, b) => a + b, 0);
    const predicted = predictedCounts[i];
    const precision = predicted === 0 ? 0 : truePositive / predicted;
    const recall = support === 0 ? 0 : truePositive / support;
    const f1 = precision + recall === 0 ? 0 : (2 * precision * recall) / (precision + recall);
    return {
      label,
      precision: round(precision, 4),
      recall: round(recall, 4),
      f1: round(f1, 4),
      support,
      predicted,
    };
  });

  return {
    accuracy: round(examples.length === 0 ? 0 : correct / examples.length, 4),
    macroF1: round(perClass.reduce((a, c) => a + c.f1, 0) / Math.max(1, perClass.length), 4),
    loss: round(loss / Math.max(1, examples.length), 5),
    perClass,
    confusion: { labels, matrix: confusion },
    size: examples.length,
  };
}
