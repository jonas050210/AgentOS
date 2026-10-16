import { BenchmarkSuiteId, BenchmarkSuiteInfo } from '../../shared/types.js';
import {
  CODING_EXPRESSION_POOL,
  RESEARCH_ROUTING_POOL,
  CODING_ROUTING_POOL,
  GENERAL_TOOL_POOL,
  KNOWLEDGE_QUERIES,
  RESEARCH_EXTRACTION_POOL,
  RESEARCH_QUERIES,
  VOICE_NORMALIZATION_POOL,
  splitPool,
} from '../training/corpora.js';

export type TaskKind = 'label' | 'numeric' | 'extraction' | 'normalization' | 'retrieval';

export interface BenchmarkTask {
  id: string;
  input: string;
  expected: string;
  kind: TaskKind;
  meta?: Record<string, unknown>;
}

export interface BenchmarkSuite extends BenchmarkSuiteInfo {
  tasks: BenchmarkTask[];
}

/**
 * Held-out evaluation splits.
 *
 * The same curated pools feed training, but every third item is reserved for
 * evaluation and is never trained on. That is what makes a before/after
 * comparison meaningful: the model is measured on instructions it has not seen.
 */
const codingRouting = splitPool(CODING_ROUTING_POOL, 3).evaluation;
const codingExpressions = splitPool(CODING_EXPRESSION_POOL, 2).evaluation;
const researchExtraction = splitPool(RESEARCH_EXTRACTION_POOL, 3).evaluation;
const researchRouting = splitPool(RESEARCH_ROUTING_POOL, 3).evaluation;
const generalTools = splitPool(GENERAL_TOOL_POOL, 3).evaluation;
const voiceNormalization = splitPool(VOICE_NORMALIZATION_POOL, 3).evaluation;

export const BENCHMARK_SUITES: BenchmarkSuite[] = [
  {
    id: 'coding.routing',
    name: 'Coding · decision routing',
    specialistCategory: 'coding',
    description:
      'Held-out instructions from the coding pool. The specialist must pick the tool (or direct answering) that resolves each request. This is the decision the trained head learns.',
    taskCount: codingRouting.length,
    metrics: ['accuracy', 'macroF1', 'latencyMsP50', 'latencyMsP95', 'reliability'],
    scorer: 'exact label match against the curated expectation',
    provenance: 'Held-out split of the curated coding routing pool (never trained on)',
    tasks: codingRouting.map((item, index) => ({
      id: `coding.routing#${index + 1}`,
      input: item.text,
      expected: item.label,
      kind: 'label',
    })),
  },
  {
    id: 'coding.expression',
    name: 'Coding · arithmetic end-to-end',
    specialistCategory: 'coding',
    description:
      'Full pipeline test: route the instruction to the calculator, extract the expression and produce the number. Measures whether a trained head actually improves end-to-end task success.',
    taskCount: codingExpressions.length,
    metrics: ['taskSuccessRate', 'latencyMsMean', 'reliability'],
    scorer: 'numeric equality (tolerance 1e-6)',
    provenance: 'Held-out split of the curated expression pool (never trained on)',
    tasks: codingExpressions.map((item, index) => ({
      id: `coding.expression#${index + 1}`,
      input: item.text,
      expected: item.expected,
      kind: 'numeric',
    })),
  },
  {
    id: 'research.extraction',
    name: 'Research · URL extraction',
    specialistCategory: 'research',
    description:
      'Extract the canonical target (host + path) from instructions containing links. Routing to the fetch strategy is learned; the extraction itself is deterministic and auditable.',
    taskCount: researchExtraction.length,
    metrics: ['accuracy', 'latencyMsMean', 'reliability'],
    scorer: 'exact match on host + path',
    provenance: 'Held-out split of the curated URL corpus (never trained on)',
    tasks: researchExtraction.map((item, index) => ({
      id: `research.extraction#${index + 1}`,
      input: item.text,
      expected: item.expected,
      kind: 'extraction',
    })),
  },
  {
    id: 'research.routing',
    name: 'Research · decision routing',
    specialistCategory: 'research',
    description:
      'Held-out research instructions: fetch a link, consult saved notes, or answer from knowledge. This is the decision the Research specialist learns — the rule planner solves only the smaller share of them.',
    taskCount: researchRouting.length,
    metrics: ['accuracy', 'macroF1', 'latencyMsP50', 'latencyMsP95', 'reliability'],
    scorer: 'exact label match against the curated expectation',
    provenance: 'Held-out split of the curated research routing pool (never trained on)',
    tasks: researchRouting.map((item, index) => ({
      id: `research.routing#${index + 1}`,
      input: item.text,
      expected: item.label,
      kind: 'label',
    })),
  },
  {
    id: 'research.retrieval',
    name: 'Research · corpus retrieval',
    specialistCategory: 'research',
    description:
      'Retrieve the right passages from the research corpus for held-out questions. Scored with recall@5 and MRR, the standard measures for a retrieved (not generated) answer.',
    taskCount: RESEARCH_QUERIES.length,
    metrics: ['recallAt5', 'mrr', 'precisionAt5', 'hitRate', 'latencyMsMean'],
    scorer: 'recall@5 / mean reciprocal rank against curated relevant ids',
    provenance: 'Held-out queries over the curated research corpus',
    tasks: RESEARCH_QUERIES.map((item, index) => ({
      id: `research.retrieval#${index + 1}`,
      input: item.query,
      expected: item.relevant.join(', '),
      kind: 'retrieval',
      meta: { relevant: item.relevant, k: 5 },
    })),
  },
  {
    id: 'knowledge.retrieval',
    name: 'Knowledge · note retrieval',
    specialistCategory: 'knowledge',
    description:
      'Find the notes that answer a question. The index is built from the shipped knowledge base plus everything the user saved, so this benchmark measures the real memory of the installation.',
    taskCount: KNOWLEDGE_QUERIES.length,
    metrics: ['recallAt5', 'mrr', 'precisionAt5', 'hitRate', 'latencyMsMean'],
    scorer: 'recall@5 / mean reciprocal rank against curated relevant ids',
    provenance: 'Held-out queries over the knowledge base of this installation',
    tasks: KNOWLEDGE_QUERIES.map((item, index) => ({
      id: `knowledge.retrieval#${index + 1}`,
      input: item.query,
      expected: item.relevant.join(', '),
      kind: 'retrieval',
      meta: { relevant: item.relevant, k: 5 },
    })),
  },
  {
    id: 'voice.normalization',
    name: 'Voice · speech normalisation',
    specialistCategory: 'voice',
    description:
      'Raw text must be turned into exactly the spoken form: learned abbreviation expansions plus deterministic number, currency and symbol rules.',
    taskCount: voiceNormalization.length,
    metrics: ['exactMatch', 'latencyMsMean', 'reliability'],
    scorer: 'verbatim string equality after whitespace normalisation',
    provenance: 'Held-out split of the curated normalisation pool (never trained on)',
    tasks: voiceNormalization.map((item, index) => ({
      id: `voice.normalization#${index + 1}`,
      input: item.text,
      expected: item.expected,
      kind: 'normalization',
    })),
  },
  {
    id: 'general.tool_selection',
    name: 'General · tool selection',
    specialistCategory: 'general',
    description:
      'General requests that should be resolved with a tool (calculator, system info, memory, workspace) rather than escalated to a language model.',
    taskCount: generalTools.length,
    metrics: ['accuracy', 'macroF1', 'latencyMsP50', 'latencyMsP95', 'reliability'],
    scorer: 'exact label match against the curated expectation',
    provenance: 'Held-out split of the curated general tool pool (never trained on)',
    tasks: generalTools.map((item, index) => ({
      id: `general.tool_selection#${index + 1}`,
      input: item.text,
      expected: item.label,
      kind: 'label',
    })),
  },
];

export function getSuite(id: BenchmarkSuiteId): BenchmarkSuite | undefined {
  return BENCHMARK_SUITES.find((suite) => suite.id === id);
}

/** Throws when a suite is asked to test a specialist category it was not built for. */
export function assertSuiteForSpecialist(suite: BenchmarkSuite, category: string): void {
  if (suite.specialistCategory === category || category === 'custom') return;
  throw new Error(
    `Suite "${suite.name}" targets ${suite.specialistCategory} specialists, but this specialist is a ${category} specialist.`,
  );
}

export function suiteInfos(): BenchmarkSuiteInfo[] {
  return BENCHMARK_SUITES.map(({ tasks: _tasks, ...info }) => info);
}

/**
 * Which suite measures the model produced from a dataset. A training run uses
 * this to measure the *state before* training on the very suite the candidate
 * will be judged on, so the before/after pair is comparable by construction.
 */
export const DATASET_SUITE: Record<string, BenchmarkSuiteId> = {
  'coding.tool_routing': 'coding.routing',
  'coding.user_examples': 'coding.routing',
  'research.tool_routing': 'research.routing',
  'research.corpus': 'research.retrieval',
  'knowledge.corpus': 'knowledge.retrieval',
  'general.tool_routing': 'general.tool_selection',
  'voice.lexicon': 'voice.normalization',
  'voice.normalization': 'voice.normalization',
};

export function suiteForDataset(datasetId: string): BenchmarkSuiteId | null {
  return DATASET_SUITE[datasetId] ?? null;
}
