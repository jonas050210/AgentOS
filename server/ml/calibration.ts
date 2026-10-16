import { TaskHead } from './softmax.js';

/**
 * Delegation-threshold calibration.
 *
 * A trained head is useful only up to the confidence where it is actually more
 * trustworthy than the deterministic fallback. Below that confidence the rule
 * planner is the better answer, and above it the head is. This module measures
 * that crossover on data the head has never trained on and returns the
 * threshold with the highest *end-to-end* accuracy, together with the curve it
 * was picked from, so the choice is auditable instead of guessed.
 *
 * The chain being calibrated is the one the runtime uses:
 *
 *   head (confidence ≥ t)  →  rule planner (if it matches)  →  escalate
 *
 * Only the first step moves with `t`; the other two are what the platform would
 * really do, policy confidence included.
 */

export interface CalibrationExample {
  text: string;
  /** Correct label for this input. */
  expected: string;
  /** What the rule planner answers, or `null` when it has no plan. */
  policyLabel: string | null;
}

export interface ThresholdPoint {
  threshold: number;
  /** End-to-end accuracy if the head answered everything at or above `threshold`. */
  accuracy: number;
  correct: number;
  headAnswered: number;
  policyAnswered: number;
  escalated: number;
}

export interface DelegationCalibration {
  /** Threshold with the highest accuracy on the evaluation set. */
  threshold: number;
  accuracy: number;
  headAnswered: number;
  policyAnswered: number;
  escalated: number;
  examples: number;
  /** Accuracy when every request is delegated to the rule planner. */
  policyOnlyAccuracy: number;
  /** Accuracy when the head always answers, regardless of confidence. */
  headOnlyAccuracy: number;
  /** Accuracy at the settings default, for comparison in the UI. */
  defaultThreshold: number;
  defaultThresholdAccuracy: number;
  curve: ThresholdPoint[];
}

/**
 * Candidate thresholds. 0 is the "head always answers" endpoint and is included
 * on purpose: for some datasets that really is the best setting, and the curve
 * has to be able to say so.
 */
export const DEFAULT_THRESHOLDS: number[] = Array.from({ length: 20 }, (_, index) => Number((index * 0.05).toFixed(2)));

interface ScoredExample {
  expected: string;
  headLabel: string;
  confidence: number;
  policyLabel: string | null;
}

interface EvaluationOutcome {
  accuracy: number;
  correct: number;
  headAnswered: number;
  policyAnswered: number;
  escalated: number;
}

function runChain(scored: ScoredExample[], threshold: number): EvaluationOutcome {
  let correct = 0;
  let headAnswered = 0;
  let policyAnswered = 0;
  let escalated = 0;
  for (const item of scored) {
    if (item.confidence >= threshold) {
      headAnswered++;
      if (item.headLabel === item.expected) correct++;
      continue;
    }
    if (item.policyLabel) {
      policyAnswered++;
      if (item.policyLabel === item.expected) correct++;
      continue;
    }
    escalated++;
  }
  return {
    accuracy: scored.length === 0 ? 0 : Number((correct / scored.length).toFixed(4)),
    correct,
    headAnswered,
    policyAnswered,
    escalated,
  };
}

export function calibrateDelegationThreshold(
  head: TaskHead,
  examples: CalibrationExample[],
  options: { thresholds?: number[]; defaultThreshold?: number } = {},
): DelegationCalibration | null {
  if (examples.length === 0) return null;

  const scored: ScoredExample[] = examples.map((example) => {
    const prediction = head.predict(example.text);
    return {
      expected: example.expected,
      headLabel: prediction.label,
      confidence: prediction.confidence,
      policyLabel: example.policyLabel,
    };
  });

  const thresholds = options.thresholds ?? DEFAULT_THRESHOLDS;
  const curve: ThresholdPoint[] = thresholds.map((threshold) => ({ threshold, ...runChain(scored, threshold) }));

  let best = curve[0];
  for (const point of curve) {
    // Highest accuracy wins; on a tie the setting that escalates less is
    // preferred, and failing that the more conservative (higher) threshold.
    if (
      point.accuracy > best.accuracy ||
      (point.accuracy === best.accuracy && point.escalated < best.escalated) ||
      (point.accuracy === best.accuracy && point.escalated === best.escalated && point.threshold > best.threshold)
    ) {
      best = point;
    }
  }

  const defaultThreshold = options.defaultThreshold ?? 0.45;
  const atDefault = curve.reduce((closest, point) =>
    Math.abs(point.threshold - defaultThreshold) < Math.abs(closest.threshold - defaultThreshold) ? point : closest,
  );

  const headOnlyAccuracy = Number(
    (scored.filter((item) => item.headLabel === item.expected).length / scored.length).toFixed(4),
  );
  const policyOnlyAccuracy = Number(
    (scored.filter((item) => item.policyLabel === item.expected).length / scored.length).toFixed(4),
  );

  return {
    threshold: best.threshold,
    accuracy: best.accuracy,
    headAnswered: best.headAnswered,
    policyAnswered: best.policyAnswered,
    escalated: best.escalated,
    examples: scored.length,
    policyOnlyAccuracy,
    headOnlyAccuracy,
    defaultThreshold: atDefault.threshold,
    defaultThresholdAccuracy: atDefault.accuracy,
    curve,
  };
}
