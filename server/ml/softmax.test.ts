// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { HashingVectorizer } from './features.js';
import { DEFAULT_SOFTMAX_HP, autoFeatureDim, TaskHead, trainTaskHead, evaluateTaskHead } from './softmax.js';

/**
 * The training defaults decide how good a freshly trained specialist actually
 * is, so they are treated as a claim to be tested rather than a preference. The
 * dataset used here is synthetic but structured the way the shipped pools are:
 * several labels, paraphrases within a label, unseen wording in the holdout.
 */

function buildDataset() {
  // Twenty varied instructions per label — the same order of magnitude as the
  // shipped routing corpora, because a head trained on five examples per label
  // would not be a meaningful test of the defaults either way.
  const topics: Record<string, string[]> = {
    'calc.evaluate': [
      'what is 12 * 4', 'compute 88 / 4', 'add 21 and 34', 'work out 7 * 8', 'what does 200 / 8 equal',
      'calculate 144 / 12', 'what is 19 % 4', 'evaluate 25 + 17', 'what is 45 - 19', 'compute 7 ^ 2',
      'what is 0.5 * 8', 'multiply 9 by 9', 'what is 81 / 9', 'sum 55 and 67', 'what is 2 ^ 8',
      'compute 1.5 * 6', 'what is 62 + 38', 'work out 55 - 18', 'add up 12 and 15', 'calculate 17 * 6',
    ],
    'workspace.read': [
      'open server/app.ts', 'read the file docs/ARCHITECTURE.md', 'show me the readme', 'print the config file',
      'display the licence file', 'read the environment example', 'cat vite.config.ts', 'show me src/App.tsx',
      'read tsconfig.json', 'open the notes file', 'print the changelog', 'display the settings source',
      'read the benchmark store file', 'open the index html', 'show me the smoke script', 'read the shared types file',
      'cat the eslint config', 'print package.json', 'open the logger source', 'display README.md',
    ],
    'memory.search': [
      'find the note about migrations', 'what do we know about caching', 'look up the saved note on ports',
      'recall anything about budgets', 'search my notes for the deadline', 'find our note about thresholds',
      'what notes exist about tests', 'look up what we stored about hiring', 'find the note about the vendor',
      'recall the earlier decision about polling', 'search memory for the retrieval index',
      'do we have notes about promotion gates', 'what do you remember about the roadmap', 'find any note about recall at k',
      'look up the note on dataset provenance', 'search my notes for the word version', 'what did we note about holdout splits',
      'find the note mentioning rollout', 'recall what we wrote about licences', 'look up anything about confirmation thresholds',
    ],
    'answer.direct': [
      'refactor this function to be async', 'write a debounce helper in typescript', 'suggest a better name for this module',
      'propose a plan for the redesign', 'draft a short apology note', 'explain the difference between precision and recall',
      'outline the workshop plan', 'give me a pros and cons list', 'summarise the feedback', 'review this error handling approach',
      'draft a changelog entry for this release', 'explain how specialists pick tools', 'write a short summary of the architecture',
      'compare the two training methods', 'draft a polite decline to the vendor', 'explain why the benchmark reports p95',
      'suggest an agenda for the workshop', 'outline the steps for adding a specialist', 'write the docstring for this function',
      'propose a plan for the frontend redesign',
    ],
  };
  const examples = Object.entries(topics).flatMap(([label, texts]) => texts.map((text) => ({ text, label })));
  // Held out: new wording for every label, never trained on. A head that
  // memorised the training split would score 0 here.
  const holdout = [
    { text: 'multiply 14 by 7', label: 'calc.evaluate' },
    { text: 'what is 3 * 3 * 3', label: 'calc.evaluate' },
    { text: 'open src/index.css', label: 'workspace.read' },
    { text: 'read the file with the smoke checks', label: 'workspace.read' },
    { text: 'have we stored anything about tokenisation', label: 'memory.search' },
    { text: 'find our earlier note on hiring', label: 'memory.search' },
    { text: 'explain the onboarding flow', label: 'answer.direct' },
    { text: 'write a friendly reminder', label: 'answer.direct' },
  ];
  return { examples, holdout };
}

describe('task-head training', () => {
  it('sizes the feature space from the data instead of a fixed constant', () => {
    expect(autoFeatureDim(1)).toBe(512);
    expect(autoFeatureDim(60)).toBe(512);
    expect(autoFeatureDim(146)).toBe(2048);
    expect(autoFeatureDim(100000)).toBe(4096);
    // Always a power of two, so hashing distributes evenly.
    for (const count of [5, 33, 71, 200, 900]) {
      const dim = autoFeatureDim(count);
      expect(Number.isInteger(Math.log2(dim))).toBe(true);
    }
    expect(DEFAULT_SOFTMAX_HP.featureDim).toBe(512);
  });

  it('actually separates unseen wording in a real training run', () => {
    const { examples, holdout } = buildDataset();
    const result = trainTaskHead({
      examples,
      hyperparameters: { ...DEFAULT_SOFTMAX_HP, featureDim: autoFeatureDim(examples.length) },
    });
    const head = new TaskHead(result.artifact);
    const evaluation = evaluateTaskHead(head, holdout);
    // The head must generalise: memorising the 20 training items would score 0.
    expect(evaluation.accuracy).toBeGreaterThanOrEqual(0.75);
    expect(result.artifact.labels.sort()).toEqual(['answer.direct', 'calc.evaluate', 'memory.search', 'workspace.read']);
    expect(result.history.length).toBeGreaterThan(20);
    expect(result.cancelled).toBe(false);
  });

  it('uses the artefact\'s own feature space when predicting', () => {
    const { examples } = buildDataset();
    const dim = autoFeatureDim(examples.length);
    const result = trainTaskHead({ examples, hyperparameters: { epochs: 20, featureDim: dim } });
    const head = new TaskHead(result.artifact);
    expect(head.featureDim).toBe(dim);
    // A vectorizer built from the artefact, not from a default constant.
    expect(new HashingVectorizer({ featureDim: result.artifact.featureDim }).featureDim).toBe(dim);
    const prediction = head.predict('what is 12 * 4');
    expect(prediction.probabilities).toHaveLength(result.artifact.labels.length);
    expect(prediction.confidence).toBeGreaterThan(0);
    expect(prediction.confidence).toBeLessThanOrEqual(1);
  });

  it('reports cancellation instead of claiming a converged run', () => {
    const { examples } = buildDataset();
    let epochs = 0;
    const result = trainTaskHead({
      examples,
      hyperparameters: { epochs: 100, featureDim: 512 },
      onEpoch: () => {
        epochs++;
      },
      shouldStop: () => epochs >= 5,
    });
    expect(result.cancelled).toBe(true);
    expect(result.history.length).toBeLessThanOrEqual(6);
  });

  it('emits parameter checkpoints that can be reloaded as a model', () => {
    const { examples } = buildDataset();
    const snapshots: Array<{ epoch: number; weights: number[][]; labels: string[] }> = [];
    trainTaskHead({
      examples,
      hyperparameters: { epochs: 12, featureDim: 512 },
      checkpointEvery: 4,
      onCheckpoint: (snapshot) => snapshots.push({ epoch: snapshot.epoch, weights: snapshot.weights, labels: snapshot.labels }),
    });
    expect(snapshots.length).toBeGreaterThanOrEqual(2);
    for (const snapshot of snapshots) {
      expect(snapshot.weights).toHaveLength(snapshot.labels.length);
      expect(snapshot.weights[0]).toHaveLength(512);
      expect(snapshot.weights.flat().every((value) => Number.isFinite(value))).toBe(true);
    }
  });
});
