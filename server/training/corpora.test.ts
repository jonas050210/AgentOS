// @vitest-environment node
import { describe, expect, it } from 'vitest';
import {
  CODING_ROUTING_POOL,
  GENERAL_TOOL_POOL,
  KNOWLEDGE_CORPUS,
  KNOWLEDGE_QUERIES,
  RESEARCH_CORPUS,
  RESEARCH_QUERIES,
  RESEARCH_ROUTING_POOL,
  splitPool,
} from './corpora.js';

/**
 * The split is the foundation of every before/after claim in the platform: the
 * benchmark suites and the evaluation splits of the datasets are both carved out
 * of these pools. These tests pin the properties that were wrong before —
 * labels leaking wholesale into one side of the split — so a before/after
 * comparison can never silently become a comparison of two different tasks.
 */

function labelsOf<T extends { label: string }>(items: T[]): string[] {
  return [...new Set(items.map((item) => item.label))].sort();
}

describe('corpus splits', () => {
  it('keeps every label on both sides of the split', () => {
    for (const pool of [CODING_ROUTING_POOL, RESEARCH_ROUTING_POOL, GENERAL_TOOL_POOL]) {
      const { train, evaluation } = splitPool(pool, 3);
      expect(evaluation.length).toBeGreaterThan(0);
      // The pools are written label by label; a modulo split on that order puts
      // whole labels into the holdout, which makes the score meaningless.
      expect(labelsOf(train)).toEqual(labelsOf(evaluation));
    }
  });

  it('is deterministic, so a suite and a dataset always describe the same split', () => {
    const first = splitPool(CODING_ROUTING_POOL, 3);
    const second = splitPool(CODING_ROUTING_POOL, 3);
    expect(first.evaluation.map((item) => item.text)).toEqual(second.evaluation.map((item) => item.text));
    expect(first.train.map((item) => item.text)).toEqual(second.train.map((item) => item.text));
  });

  it('never lets a training instruction appear in the held-out half', () => {
    const { train, evaluation } = splitPool(CODING_ROUTING_POOL, 3);
    const trained = new Set(train.map((item) => item.text.toLowerCase().trim()));
    const overlap = evaluation.filter((item) => trained.has(item.text.toLowerCase().trim()));
    expect(overlap).toEqual([]);
  });

  it('gives every decision enough training surface to be learnable', () => {
    const { train } = splitPool(CODING_ROUTING_POOL, 3);
    const counts = new Map<string, number>();
    for (const item of train) counts.set(item.label, (counts.get(item.label) ?? 0) + 1);
    for (const [label, count] of counts) {
      expect(count, `${label} only has ${count} training examples`).toBeGreaterThanOrEqual(20);
    }
  });

  it('only labels relevant ids that exist in the corpus it is scored against', () => {
    for (const [corpus, queries] of [
      [RESEARCH_CORPUS, RESEARCH_QUERIES],
      [KNOWLEDGE_CORPUS, KNOWLEDGE_QUERIES],
    ] as const) {
      const ids = new Set(corpus.map((doc) => doc.id));
      expect(ids.size).toBe(corpus.length);
      for (const query of queries) {
        expect(query.relevant.length, `"${query.query}" has no relevant document`).toBeGreaterThan(0);
        for (const id of query.relevant) {
          expect(ids.has(id), `"${query.query}" points at the unknown document ${id}`).toBe(true);
        }
      }
    }
  });
});
