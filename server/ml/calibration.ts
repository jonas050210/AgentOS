import { HashingVectorizer } from './features.js';
import { TaskHead } from './softmax.js';

/**
 * Delegation calibration.
 *
 * A softmax head that is 40% "confident" is not necessarily wrong 60% of the
 * time: raw softmax scores are uncalibrated. AgentOS therefore measures, on the
 * validation split the head never trained on, how accurate the *whole decision
 * chain* is for every candidate abstention threshold:
 *
 *   confidence >= threshold  -> answer from the trained head
 *   otherwise, a rule applies -> answer from the built-in rule policy
 *   otherwise                 -> escalate (counted as unresolved)
 *
 * The threshold with the best measured end-to-end accuracy is stored with the
 * artefact, so a version carries the confidence at which it is actually
 * trustworthy instead of inheriting one global guess. This is deliberately a
 * small, honest calibration step — not temperature scaling on a held-out log
 * likelihood — and it is evaluated on data the head never saw.
 */

export interface CalibrationExample {
  text: string;
  expected: string;
  /** Label the rule policy would answer with, or null when no rule applies. */
  policyLabel: string | null;
}

export interface ThresholdPoint {
  threshold: number;
  accuracy: number;
  correct: number;
  headAnswered: number;
  policyAnswered: number;
  escalated: number;
}

export interface DelegationCalibration {
  /** Chosen abstention threshold (head answers at or above it). */
  threshold: number;
  accuracy: number;
  headAnswered: number;
  policyAnswered: number;
  escalated: number;
  examples: number;
  curve: ThresholdPoint[];
}

const DEFAULT_THRESHOLDS = Array.from({ length: 20 }, (_, i) => Number((i * 0.05).toFixed(2)));

export function calibrateDelegationThreshold(
  head: TaskHead,
  examples: CalibrationExample[],
  options: { thresholds?: number[]; vectorizer?: HashingVectorizer } = {},
): DelegationCalibration | null {
  if (examples.length === 0) return null;
  const vectorizer = options.vectorizer ?? new HashingVectorizer({ featureDim: head.featureDim });
  const thresholds = options.thresholds ?? DEFAULT_THRESHOLDS;

  // Score every example once; the sweep below only re-applies the threshold.
  const scored = examples.map((example) => {
    const prediction = head.predict(example.text, vectorizer);
    return {
      expected: example.expected,
      label: prediction.label,
      confidence: prediction.confidence,
      policyLabel: example.policyLabel,
    };
  });

  const curve: ThresholdPoint[] = thresholds.map((threshold) => {
    let correct = 0;
    let headAnswered = 0;
    let policyAnswered = 0;
    let escalated = 0;
    for (const item of scored) {
      if (item.confidence >= threshold) {
        headAnswered++;
        if (item.label === item.expected) correct++;
      } else if (item.policyLabel) {
        policyAnswered++;
        if (item.policyLabel === item.expected) correct++;
      } else {
        escalated++;
      }
    }
    return {
      threshold,
      accuracy: correct / scored.length,
      correct,
      headAnswered,
      policyAnswered,
      escalated,
    };
  });

  const best = curve.reduce((acc, point) => {
    if (point.correct > acc.correct) return point;
    if (point.correct < acc.correct) return acc;
    // Tie on accuracy: prefer fewer unresolved escalations, then the safer
    // (higher) threshold.
    if (point.escalated < acc.escalated) return point;
    if (point.escalated > acc.escalated) return acc;
    return point.threshold > acc.threshold ? point : acc;
  });

  return {
    threshold: best.threshold,
    accuracy: best.accuracy,
    headAnswered: best.headAnswered,
    policyAnswered: best.policyAnswered,
    escalated: best.escalated,
    examples: scored.length,
    curve,
  };
}
