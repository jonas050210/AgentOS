import { BenchmarkSuiteId, BenchmarkSuiteInfo } from '../../shared/types.js';
import {
  CODING_EXPRESSION_POOL,
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

const codingRouting = splitPool(CODING_ROUTING_POOL, 3).evaluation;
const codingExpressions = splitPool(CODING_EXPRESSION_POOL, 2).evaluation;
const researchExtraction = splitPool(RESEARCH_EXTRACTION_POOL, 3).evaluation;
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
    provenance:
      'Held-out benchmark split of the curated coding routing pool — disjoint from both the training split and the calibration slice',
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
    name: 'Research · source extraction',
    specialistCategory: 'research',
    description:
      'Research instructions containing URLs. The specialist must recognise the request as a retrieval task and extract the exact target (host and path).',
    taskCount: researchExtraction.length,
    metrics: ['accuracy', 'latencyMsP50', 'reliability'],
    scorer: 'exact match on the normalised host/path',
    provenance: 'Held-out split of the curated extraction pool (never trained on)',
    tasks: researchExtraction.map((item, index) => ({
      id: `research.extraction#${index + 1}`,
      input: item.text,
      expected: item.expected,
      kind: 'extraction',
    })),
  },
  {
    id: 'research.retrieval',
    name: 'Research · corpus retrieval',
    specialistCategory: 'research',
    description:
      'Graded queries against the technical corpus. Measures recall@5 and MRR of the specialist’s index — the objective the retrieval training method optimises.',
    taskCount: RESEARCH_QUERIES.length,
    metrics: ['recallAt5', 'mrr', 'latencyMsP50'],
    scorer: 'recall@5 and MRR against graded relevant documents',
    provenance: '18 graded queries over 14 curated abstracts',
    tasks: RESEARCH_QUERIES.map((query, index) => ({
      id: `research.retrieval#${index + 1}`,
      input: query.query,
      expected: query.relevantIds.join(', '),
      kind: 'retrieval',
      meta: { relevantIds: query.relevantIds },
    })),
  },
  {
    id: 'knowledge.retrieval',
    name: 'Knowledge · note retrieval',
    specialistCategory: 'knowledge',
    description:
      'Queries against your memory store merged with the platform notes. Recall here shows how well the Knowledge specialist can find your own material.',
    taskCount: KNOWLEDGE_QUERIES.length,
    metrics: ['recallAt5', 'mrr', 'latencyMsP50'],
    scorer: 'recall@5 and MRR against curated relevant notes',
    provenance: '12 graded queries over platform notes plus your stored records',
    tasks: KNOWLEDGE_QUERIES.map((query, index) => ({
      id: `knowledge.retrieval#${index + 1}`,
      input: query.query,
      expected: query.relevantIds.join(', '),
      kind: 'retrieval',
      meta: { relevantIds: query.relevantIds },
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
    provenance:
      'Sentences that never appear in training. The lexicon is learned from different sentences containing the same terms, so this measures generalisation of the learned expansions.',
    tasks: voiceNormalization.map((item, index) => ({
      id: `voice.normalization#${index + 1}`,
      input: item.text,
      expected: item.expected,
      kind: 'normalization',
    })),
  },
  {
    id: 'general.tool_selection',
    name: 'Concierge · tool selection',
    specialistCategory: 'general',
    description:
      'General requests across the whole tool registry. Trains and measures the tool choice of the default specialist behind the central assistant.',
    taskCount: generalTools.length,
    metrics: ['accuracy', 'latencyMsP50', 'reliability'],
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
  return BENCHMARK_SUITES.find((s) => s.id === id);
}

export function suiteInfos(): BenchmarkSuiteInfo[] {
  return BENCHMARK_SUITES.map(({ tasks: _tasks, ...info }) => info);
}
