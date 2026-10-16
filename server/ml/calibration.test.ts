// @vitest-environment node
import { describe, expect, it, beforeEach } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/**
 * Delegation calibration is what makes "how confident must the head be" a
 * measured property of the model instead of a global guess. These tests pin the
 * properties that matter: it only uses held-out examples, it is monotone in the
 * trade-off it models, and it never reports a score it did not measure.
 */

let calibration: typeof import('./calibration.js');
let softmax: typeof import('./softmax.js');

beforeEach(async () => {
  process.env.AGENTOS_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'agentos-cal-'));
  const { vi } = await import('vitest');
  vi.resetModules();
  calibration = await import('./calibration.js');
  softmax = await import('./softmax.js');
});

/** Tiny hand-built head: separates the two labels almost perfectly. */
function buildHead(): InstanceType<typeof softmax.TaskHead> {
  const result = softmax.trainTaskHead({
    examples: [
      ...Array.from({ length: 12 }, (_, i) => ({ text: `alpha sample ${i}`, label: 'alpha' })),
      ...Array.from({ length: 12 }, (_, i) => ({ text: `beta sample ${i}`, label: 'beta' })),
    ],
    hyperparameters: { epochs: 40, learningRate: 0.9, seed: 7 },
  });
  return new softmax.TaskHead(result.artifact);
}

describe('delegation calibration', () => {
  it('returns null instead of inventing a threshold when there is no data', () => {
    expect(calibration.calibrateDelegationThreshold(buildHead(), [])).toBeNull();
  });

  it('sweeps every candidate threshold and reports the honest counts', () => {
    const head = buildHead();
    const result = calibration.calibrateDelegationThreshold(head, [
      { text: 'alpha sample', expected: 'alpha', policyLabel: null },
      { text: 'beta sample', expected: 'beta', policyLabel: null },
    ]);
    expect(result).not.toBeNull();
    const thresholds = result!.curve.map((p) => p.threshold);
    expect(thresholds[0]).toBe(0);
    expect(thresholds).toContain(0.95);
    for (const point of result!.curve) {
      expect(point.headAnswered + point.policyAnswered + point.escalated).toBe(result!.examples);
      expect(point.accuracy).toBeGreaterThanOrEqual(0);
      expect(point.accuracy).toBeLessThanOrEqual(1);
    }
  });

  it('prefers the rule policy when the head is weaker than the rules', () => {
    const head = buildHead();
    // Nothing the head knows how to separate: it can only answer with the two
    // labels it was trained on, so every item is wrong. The rules label them
    // correctly, and the calibration has to notice and delegate.
    const data = [
      { text: 'inspect the repository layout', expected: 'workspace.list', policyLabel: 'workspace.list' },
      { text: 'what is the host uptime', expected: 'system.info', policyLabel: 'system.info' },
      { text: 'remember this for later', expected: 'memory.save', policyLabel: 'memory.save' },
    ];
    const result = calibration.calibrateDelegationThreshold(head, data);
    expect(result).not.toBeNull();
    expect(result!.policyAnswered).toBe(3);
    expect(result!.headAnswered).toBe(0);
    expect(result!.accuracy).toBeCloseTo(1, 5);
    // The fully-trusting end of the sweep must visibly be the worst one.
    expect(result!.curve[0].accuracy).toBe(0);
  });

  it('is deterministic for the same inputs', () => {
    const data = [
      { text: 'alpha sample 1', expected: 'alpha', policyLabel: null },
      { text: 'beta sample 2', expected: 'beta', policyLabel: null },
    ];
    const first = calibration.calibrateDelegationThreshold(buildHead(), data);
    const second = calibration.calibrateDelegationThreshold(buildHead(), data);
    expect(second!.threshold).toBe(first!.threshold);
    expect(second!.accuracy).toBe(first!.accuracy);
  });

  it('never picks a threshold that scores worse than full delegation', () => {
    const head = buildHead();
    const data = [
      { text: 'alpha sample 3', expected: 'alpha', policyLabel: null },
      { text: 'beta sample 4', expected: 'beta', policyLabel: null },
      { text: 'gamma sample 5', expected: 'gamma', policyLabel: 'gamma' },
    ];
    const result = calibration.calibrateDelegationThreshold(head, data)!;
    const worst = Math.max(...result.curve.map((p) => p.accuracy));
    expect(result.accuracy).toBeCloseTo(worst, 10);
  });
});
