import { DEFAULT_VECTORIZER, HashingVectorizer, SparseVector, toDense } from './features.js';
import { Rng } from './rng.js';

/**
 * Multiclass softmax head trained with mini-batch SGD.
 *
 * This is genuine gradient descent: cross-entropy loss, momentum, L2 weight
 * decay, class balancing for skewed labels, label smoothing (which keeps the
 * probabilities from becoming overconfident) and early stopping on a held-out
 * validation split. It is deliberately small and fully deterministic given a
 * seed, so a run can always be reproduced and compared.
 */

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
  valFraction: number;
  patience: number;
  labelSmoothing: number;
}

export const DEFAULT_SOFTMAX_HP: SoftmaxHyperparameters = {
  epochs: 30,
  learningRate: 0.35,
  l2: 1e-4,
  batchSize: 16,
  momentum: 0.9,
  featureDim: DEFAULT_VECTORIZER.featureDim,
  seed: 42,
  valFraction: 0.25,
  patience: 8,
  labelSmoothing: 0.02,
};

export interface TaskHeadArtifact {
  kind: 'task_head_sgd';
  featureDim: number;
  labels: string[];
  /** Row-major weights: [label][feature]. */
  weights: number[][];
  bias: number[];
  trainedAt: string;
  trainingSize: number;
  bestEpoch: number;
  /**
   * Measured delegation threshold (see ml/calibration.ts): the confidence at
   * which this head is genuinely more accurate than the rule baseline. Stored
   * with the artefact so the threshold travels with the model, calibrated on
   * data the head has never trained on.
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
  bestEpoch: number;
  trainedSize: number;
  validationSize: number;
  /** True when the run stopped early because the caller asked it to. */
  cancelled: boolean;
}

function softmax(scores: Float64Array): Float64Array {
  let max = -Infinity;
  for (const score of scores) if (score > max) max = score;
  const exps = new Float64Array(scores.length);
  let sum = 0;
  for (let i = 0; i < scores.length; i++) {
    const value = Math.exp(scores[i] - max);
    exps[i] = value;
    sum += value;
  }
  for (let i = 0; i < exps.length; i++) exps[i] /= sum || 1;
  return exps;
}

export class TaskHead {
  readonly labels: string[];
  readonly featureDim: number;
  private readonly weights: Float64Array;
  private readonly bias: Float64Array;
  private readonly vectorizer: HashingVectorizer;

  constructor(readonly artifact: TaskHeadArtifact, vectorizer?: HashingVectorizer) {
    this.labels = artifact.labels;
    this.featureDim = artifact.featureDim;
    this.vectorizer = vectorizer ?? new HashingVectorizer({ featureDim: artifact.featureDim });
    this.weights = new Float64Array(artifact.labels.length * artifact.featureDim);
    this.bias = Float64Array.from(artifact.bias);
    for (let c = 0; c < artifact.labels.length; c++) {
      for (let f = 0; f < artifact.featureDim; f++) {
        this.weights[c * artifact.featureDim + f] = artifact.weights[c]?.[f] ?? 0;
      }
    }
  }

  /** Class scores for an already-vectorised input. */
  scores(vector: Float64Array): Float64Array {
    const scores = new Float64Array(this.labels.length);
    for (let c = 0; c < this.labels.length; c++) {
      let sum = this.bias[c];
      const offset = c * this.featureDim;
      for (let f = 0; f < this.featureDim; f++) {
        const value = vector[f];
        if (value !== 0) sum += this.weights[offset + f] * value;
      }
      scores[c] = sum;
    }
    return scores;
  }

  probabilities(vector: Float64Array): Float64Array {
    return softmax(this.scores(vector));
  }

  predict(text: string): { label: string; confidence: number; probabilities: Float64Array } {
    const sparse: SparseVector = this.vectorizer.vectorize(text);
    const probs = this.probabilities(toDense(sparse));
    let best = 0;
    for (let i = 1; i < probs.length; i++) if (probs[i] > probs[best]) best = i;
    return { label: this.labels[best], confidence: probs[best], probabilities: probs };
  }
}

/** Deterministic stratified split: every k-th example of each label is held out. */
export function splitExamples(
  examples: LabeledExample[],
  valFraction: number,
  seed: number,
): { train: LabeledExample[]; validation: LabeledExample[] } {
  const rng = new Rng(seed);
  const train: LabeledExample[] = [];
  const validation: LabeledExample[] = [];
  const byLabel = new Map<string, LabeledExample[]>();
  for (const example of examples) {
    const bucket = byLabel.get(example.label) ?? [];
    bucket.push(example);
    byLabel.set(example.label, bucket);
  }
  for (const bucket of byLabel.values()) {
    const shuffled = rng.shuffle(bucket);
    const valCount = Math.max(1, Math.round(shuffled.length * valFraction));
    // Fewer than 4 examples cannot spare a validation slice: keep them in train.
    if (shuffled.length < 4) {
      train.push(...shuffled);
      continue;
    }
    validation.push(...shuffled.slice(0, valCount));
    train.push(...shuffled.slice(valCount));
  }
  return { train, validation };
}

export interface CheckpointSnapshot {
  epoch: number;
  labels: string[];
  featureDim: number;
  weights: number[][];
  bias: number[];
  valLoss: number;
  valAccuracy: number;
  isBest: boolean;
}

export interface TrainOptions {
  examples: LabeledExample[];
  hyperparameters?: Partial<SoftmaxHyperparameters>;
  onEpoch?: (report: EpochReport) => void;
  shouldStop?: () => boolean;
  /** Overrides the internal split (used by the benchmark-informed training path). */
  validation?: LabeledExample[];
  /** Snapshot the parameters every N epochs, so intermediate models are real. */
  checkpointEvery?: number;
  onCheckpoint?: (snapshot: CheckpointSnapshot) => void;
}

export function trainTaskHead(options: TrainOptions): SoftmaxTrainingResult {
  const hp: SoftmaxHyperparameters = { ...DEFAULT_SOFTMAX_HP, ...options.hyperparameters };
  const labels = [...new Set(options.examples.map((example) => example.label))].sort();
  if (labels.length < 2) throw new Error('Training a task head needs at least two distinct labels.');
  if (options.examples.length < labels.length * 2) {
    throw new Error(`Need at least ${labels.length * 2} examples for ${labels.length} labels, received ${options.examples.length}.`);
  }

  const split = options.validation
    ? { train: options.examples, validation: options.validation }
    : splitExamples(options.examples, hp.valFraction, hp.seed);
  const vectorizer = new HashingVectorizer({ featureDim: hp.featureDim });
  const labelIndex = new Map(labels.map((label, index) => [label, index]));

  const trainX = split.train.map((example) => vectorizer.vectorize(example.text));
  const trainY = Int32Array.from(split.train.map((example) => labelIndex.get(example.label)!));
  const valX = split.validation.map((example) => vectorizer.vectorize(example.text));
  const valY = Int32Array.from(split.validation.map((example) => labelIndex.get(example.label) ?? -1));

  // Balanced class weights: a label with 3 examples must not be drowned out by one with 30.
  const counts = new Array(labels.length).fill(0);
  for (const label of trainY) counts[label]++;
  const total = trainY.length;
  const classWeights = counts.map((count) => (count === 0 ? 0 : total / (labels.length * count)));

  const weights = new Float64Array(labels.length * hp.featureDim);
  const bias = new Float64Array(labels.length);
  const weightVelocity = new Float64Array(weights.length);
  const biasVelocity = new Float64Array(bias.length);
  const rng = new Rng(hp.seed);
  for (let i = 0; i < weights.length; i++) weights[i] = (rng.next() - 0.5) * 0.02;

  const history: EpochReport[] = [];
  let learningRate = hp.learningRate;
  let bestValLoss = Infinity;
  let bestEpoch = 0;
  let bestWeights = weights.slice();
  let bestBias = bias.slice();
  let epochsWithoutImprovement = 0;
  const order = new Int32Array(trainY.length);
  for (let i = 0; i < order.length; i++) order[i] = i;

  let cancelled = false;
  for (let epoch = 1; epoch <= hp.epochs; epoch++) {
    const started = Date.now();
    rng.shuffleInPlace(order);
    let epochLoss = 0;
    let epochCorrect = 0;
    let batches = 0;

    for (let batchStart = 0; batchStart < order.length; batchStart += hp.batchSize) {
      const batch = Array.from(order.subarray(batchStart, batchStart + hp.batchSize));
      const grad = new Float64Array(weights.length);
      const gradBias = new Float64Array(bias.length);

      for (const index of batch) {
        const dense = toDense(trainX[index]);
        const scores = new Float64Array(labels.length);
        for (let c = 0; c < labels.length; c++) {
          let sum = bias[c];
          const offset = c * hp.featureDim;
          for (let f = 0; f < hp.featureDim; f++) {
            const value = dense[f];
            if (value !== 0) sum += weights[offset + f] * value;
          }
          scores[c] = sum;
        }
        const probs = softmax(scores);
        const target = trainY[index];
        const weight = classWeights[target];
        let maxProb = 0;
        let argmax = 0;
        for (let c = 0; c < probs.length; c++) {
          const smoothed = (1 - hp.labelSmoothing) * (c === target ? 1 : 0) + hp.labelSmoothing / labels.length;
          const error = (probs[c] - smoothed) * weight;
          epochLoss += -Math.log(Math.max(probs[target], 1e-9)) * weight;
          if (probs[c] > maxProb) {
            maxProb = probs[c];
            argmax = c;
          }
          if (error === 0) continue;
          gradBias[c] += error;
          const offset = c * hp.featureDim;
          for (let f = 0; f < hp.featureDim; f++) {
            const value = dense[f];
            if (value !== 0) grad[offset + f] += error * value;
          }
        }
        if (argmax === target) epochCorrect++;
      }

      const scale = 1 / batch.length;
      for (let i = 0; i < weights.length; i++) {
        const gradient = grad[i] * scale + hp.l2 * weights[i];
        weightVelocity[i] = hp.momentum * weightVelocity[i] - learningRate * gradient;
        weights[i] += weightVelocity[i];
      }
      for (let i = 0; i < bias.length; i++) {
        biasVelocity[i] = hp.momentum * biasVelocity[i] - learningRate * (gradBias[i] * scale);
        bias[i] += biasVelocity[i];
      }
      batches++;
    }

    // Validation pass with the parameters as they are right now.
    let valLoss = 0;
    let valCorrect = 0;
    for (let i = 0; i < valX.length; i++) {
      const dense = toDense(valX[i]);
      const scores = new Float64Array(labels.length);
      for (let c = 0; c < labels.length; c++) {
        let sum = bias[c];
        const offset = c * hp.featureDim;
        for (let f = 0; f < hp.featureDim; f++) {
          const value = dense[f];
          if (value !== 0) sum += weights[offset + f] * value;
        }
        scores[c] = sum;
      }
      const probs = softmax(scores);
      const target = valY[i];
      if (target < 0) continue;
      valLoss += -Math.log(Math.max(probs[target], 1e-9));
      let maxProb = 0;
      let argmax = 0;
      for (let c = 0; c < probs.length; c++) {
        if (probs[c] > maxProb) {
          maxProb = probs[c];
          argmax = c;
        }
      }
      if (argmax === target) valCorrect++;
    }
    valLoss = valX.length > 0 ? valLoss / valX.length : 0;

    const report: EpochReport = {
      epoch,
      trainLoss: Number((epochLoss / Math.max(1, trainY.length)).toFixed(5)),
      trainAccuracy: Number((epochCorrect / Math.max(1, trainY.length)).toFixed(4)),
      valLoss: Number(valLoss.toFixed(5)),
      valAccuracy: Number((valCorrect / Math.max(1, valX.length)).toFixed(4)),
      learningRate: Number(learningRate.toFixed(4)),
      durationMs: Date.now() - started,
    };
    history.push(report);
    options.onEpoch?.(report);

    const improved = valLoss < bestValLoss - 1e-6;
    if (options.checkpointEvery && options.onCheckpoint && epoch % options.checkpointEvery === 0) {
      options.onCheckpoint({
        epoch,
        labels,
        featureDim: hp.featureDim,
        weights: Array.from({ length: labels.length }, (_, c) =>
          Array.from(weights.subarray(c * hp.featureDim, (c + 1) * hp.featureDim)).map((value) => Number(value.toFixed(6))),
        ),
        bias: Array.from(bias).map((value) => Number(value.toFixed(6))),
        valLoss: Number(valLoss.toFixed(5)),
        valAccuracy: report.valAccuracy,
        isBest: improved,
      });
    }

    if (improved) {
      bestValLoss = valLoss;
      bestEpoch = epoch;
      bestWeights = weights.slice();
      bestBias = bias.slice();
      epochsWithoutImprovement = 0;
    } else {
      epochsWithoutImprovement++;
    }
    if (options.shouldStop?.()) {
      cancelled = true;
      break;
    }
    if (epochsWithoutImprovement >= hp.patience) break;
    learningRate *= 0.985;
  }

  if (bestWeights.every((value) => value === 0)) {
    bestWeights = weights.slice();
    bestBias = bias.slice();
    bestEpoch = history.length;
  }

  // Store rounded weights: keeps artefacts small without changing predictions
  // beyond the sixth decimal.
  const artifact: TaskHeadArtifact = {
    kind: 'task_head_sgd',
    featureDim: hp.featureDim,
    labels,
    weights: labels.map((_, c) =>
      Array.from(bestWeights.subarray(c * hp.featureDim, (c + 1) * hp.featureDim)).map((value) => Number(value.toFixed(6))),
    ),
    bias: Array.from(bestBias).map((value) => Number(value.toFixed(6))),
    trainedAt: new Date().toISOString(),
    trainingSize: trainY.length,
    bestEpoch,
  };

  return { artifact, history, bestEpoch, trainedSize: trainY.length, validationSize: valX.length, cancelled };
}

export interface EvaluationResult {
  accuracy: number;
  macroF1: number;
  confusion: number[][];
  labels: string[];
  perLabel: Record<string, { precision: number; recall: number; f1: number; support: number }>;
}

export function evaluateTaskHead(head: TaskHead, examples: LabeledExample[]): EvaluationResult {
  const labels = head.labels;
  const index = new Map(labels.map((label, i) => [label, i]));
  const confusion = labels.map(() => new Array(labels.length).fill(0));
  const predictedCounts = new Array(labels.length).fill(0);
  let correct = 0;

  for (const example of examples) {
    const { label: predicted } = head.predict(example.text);
    const predictedIndex = index.get(predicted)!;
    const actualIndex = index.get(example.label);
    predictedCounts[predictedIndex]++;
    if (actualIndex === undefined) continue; // unknown label at eval time is never a pass
    confusion[actualIndex][predictedIndex]++;
    if (predictedIndex === actualIndex) correct++;
  }

  const perLabel: EvaluationResult['perLabel'] = {};
  let macroF1 = 0;
  for (const [i, label] of labels.entries()) {
    const truePositive = confusion[i][i];
    const falseNegative = confusion[i].reduce((sum, value) => sum + value, 0) - truePositive;
    const falsePositive = predictedCounts[i] - truePositive;
    const precision = truePositive + falsePositive === 0 ? 0 : truePositive / (truePositive + falsePositive);
    const recall = truePositive + falseNegative === 0 ? 0 : truePositive / (truePositive + falseNegative);
    const f1 = precision + recall === 0 ? 0 : (2 * precision * recall) / (precision + recall);
    macroF1 += f1;
    perLabel[label] = {
      precision: Number(precision.toFixed(4)),
      recall: Number(recall.toFixed(4)),
      f1: Number(f1.toFixed(4)),
      support: truePositive + falseNegative,
    };
  }
  macroF1 = labels.length === 0 ? 0 : macroF1 / labels.length;

  return {
    accuracy: examples.length === 0 ? 0 : Number((correct / examples.length).toFixed(4)),
    macroF1: Number(macroF1.toFixed(4)),
    confusion,
    labels,
    perLabel,
  };
}
