import fs from 'node:fs';
import path from 'node:path';
import { DatasetInfo } from '../../shared/types.js';
import {
  CODING_EXPRESSION_POOL,
  CODING_ROUTING_POOL,
  GENERAL_TOOL_POOL,
  KNOWLEDGE_CORPUS,
  KNOWLEDGE_QUERIES,
  RESEARCH_CORPUS,
  RESEARCH_EXTRACTION_POOL,
  RESEARCH_QUERIES,
  RESEARCH_ROUTING_POOL,
  RoutingItem,
  SeedDoc,
  SeedQuery,
  VOICE_LEXICON_POOL,
  VOICE_NORMALIZATION_POOL,
  splitLexiconPool,
  splitPool,
} from './corpora.js';
import { LabeledExample } from '../ml/softmax.js';
import { LexiconExample } from '../ml/lexicon.js';
import { logger } from '../logger.js';
import { memoryStore } from '../memory/store.js';

export type DatasetKind = DatasetInfo['kind'];

export interface DatasetPayload {
  info: DatasetInfo;
  /**
   * Instruction → label items (routing / tool selection). `validation` is a
   * slice of the training side, kept out of the benchmark split, and is used to
   * calibrate the delegation threshold of the trained head.
   */
  routing?: { train: RoutingItem[]; validation: RoutingItem[]; evaluation: RoutingItem[] };
  /** Expression items used both as extra training signal and for evaluation. */
  expressions?: { train: ReturnType<typeof splitPool<(typeof CODING_EXPRESSION_POOL)[number]>>['train']; evaluation: typeof CODING_EXPRESSION_POOL };
  extraction?: { train: typeof RESEARCH_EXTRACTION_POOL; evaluation: typeof RESEARCH_EXTRACTION_POOL };
  retrieval?: { docs: SeedDoc[]; evaluation: SeedQuery[] };
  lexicon?: { train: LexiconExample[]; evaluation: LexiconExample[] };
  normalization?: { train: typeof VOICE_NORMALIZATION_POOL; evaluation: typeof VOICE_NORMALIZATION_POOL };
}

function dataDir(): string {
  return process.env.AGENTOS_DATA_DIR || path.join(process.cwd(), 'data');
}

function userExamplesFile(specialistId: string): string {
  return path.join(dataDir(), 'datasets', `${specialistId}.examples.jsonl`);
}

export interface UserExample {
  id: string;
  specialistId: string;
  text: string;
  label: string;
  createdAt: string;
  source: 'playground' | 'manual';
}

/** Examples captured from the specialist playground — the human feedback loop. */
export function listUserExamples(specialistId: string): UserExample[] {
  const file = userExamplesFile(specialistId);
  try {
    if (!fs.existsSync(file)) return [];
    return fs
      .readFileSync(file, 'utf-8')
      .split('\n')
      .filter(Boolean)
      .map((line) => JSON.parse(line) as UserExample);
  } catch (e) {
    logger.warn('datasets', `Could not read user examples for ${specialistId}`, { error: String(e) });
    return [];
  }
}

export function addUserExample(specialistId: string, entry: { text: string; label: string; source?: UserExample['source'] }): UserExample {
  const record: UserExample = {
    id: `ex_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
    specialistId,
    text: entry.text.slice(0, 500),
    label: entry.label.slice(0, 80),
    createdAt: new Date().toISOString(),
    source: entry.source ?? 'playground',
  };
  const file = userExamplesFile(specialistId);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.appendFileSync(file, `${JSON.stringify(record)}\n`, 'utf-8');
  logger.info('datasets', `Recorded training example for ${specialistId}`, { label: record.label });
  return record;
}

export function removeUserExample(specialistId: string, id: string): boolean {
  const examples = listUserExamples(specialistId);
  const remaining = examples.filter((e) => e.id !== id);
  if (remaining.length === examples.length) return false;
  const file = userExamplesFile(specialistId);
  fs.writeFileSync(file, remaining.map((e) => JSON.stringify(e)).join('\n') + (remaining.length ? '\n' : ''), 'utf-8');
  return true;
}

export function userExamplesAsLabeled(specialistId: string): LabeledExample[] {
  return listUserExamples(specialistId).map((e) => ({ text: e.text, label: e.label }));
}

/** Memory-backed knowledge corpus: AgentOS notes plus the curated seed docs. */
export function memoryCorpus(): SeedDoc[] {
  const records = memoryStore.list(500).map((r) => ({
    id: r.id,
    title: r.title,
    text: `${r.content} ${r.tags.join(' ')}`,
  }));
  return [...KNOWLEDGE_CORPUS, ...records];
}

/**
 * Splits the *training* side of a pool once more so a calibration slice exists
 * that is disjoint from both training and the benchmark split. Held-out data is
 * never traded for calibration: the benchmark split stays untouched.
 */
function withValidation<T>(train: T[]): { train: T[]; validation: T[] } {
  const inner = splitPool(train, 5);
  return { train: inner.train, validation: inner.evaluation };
}

function buildDatasets(): DatasetPayload[] {
  const codingSplit = splitPool(CODING_ROUTING_POOL, 3);
  const coding = { ...withValidation(codingSplit.train), evaluation: codingSplit.evaluation };
  const expressions = splitPool(CODING_EXPRESSION_POOL, 2);
  const extraction = splitPool(RESEARCH_EXTRACTION_POOL, 3);
  const researchSplit = splitPool(RESEARCH_ROUTING_POOL, 3);
  const research = { ...withValidation(researchSplit.train), evaluation: researchSplit.evaluation };
  const generalSplit = splitPool(GENERAL_TOOL_POOL, 3);
  const general = { ...withValidation(generalSplit.train), evaluation: generalSplit.evaluation };
  const lexicon = splitLexiconPool(VOICE_LEXICON_POOL);
  const normalization = splitPool(VOICE_NORMALIZATION_POOL, 3);

  return [
    {
      info: {
        id: 'coding.tool_routing',
        name: 'Coding · tool routing',
        specialistId: 'coding-ai',
        kind: 'routing',
        provenance: 'generated',
        provenanceDetail:
          'Curated seed corpus shipped with AgentOS: disjoint train, calibration and benchmark-holdout splits',
        size: coding.train.length,
        labels: [...new Set(coding.train.map((i) => i.label))].sort(),
        description:
          'Instruction → tool decisions for the Coding specialist. Training on this pool teaches the head which tool resolves a request; the held-out split is used by the routing benchmark.',
        createdAt: '2026-01-01T00:00:00.000Z',
      },
      routing: { train: coding.train, validation: coding.validation, evaluation: coding.evaluation },
      expressions: { train: expressions.train, evaluation: expressions.evaluation },
    },
    {
      info: {
        id: 'coding.user_examples',
        name: 'Coding · your examples',
        specialistId: 'coding-ai',
        kind: 'routing',
        provenance: 'user',
        provenanceDetail: 'Captured from the specialist playground on this machine',
        size: 0,
        labels: [],
        description: 'Decisions you confirmed while testing. Added to the training split when "include user examples" is enabled.',
        createdAt: new Date().toISOString(),
      },
    },
    {
      info: {
        id: 'research.tool_routing',
        name: 'Research · tool routing',
        specialistId: 'research-ai',
        kind: 'routing',
        provenance: 'generated',
        provenanceDetail:
          'Curated seed corpus shipped with AgentOS: disjoint train, calibration and benchmark-holdout splits',
        size: research.train.length + extraction.train.length,
        labels: ['answer.direct', 'extract.url', 'memory.search'],
        description:
          'Instruction → research action decisions (fetch a URL, consult memory, or answer directly). Includes URL-heavy instructions so extraction is routed correctly.',
        createdAt: '2026-01-01T00:00:00.000Z',
      },
      routing: {
        train: [...research.train, ...extraction.train.map((e) => ({ text: e.text, label: 'extract.url' }))],
        validation: research.validation,
        evaluation: research.evaluation,
      },
      extraction: { train: extraction.train, evaluation: extraction.evaluation },
    },
    {
      info: {
        id: 'research.corpus',
        name: 'Research · retrieval corpus',
        specialistId: 'research-ai',
        kind: 'retrieval',
        provenance: 'generated',
        provenanceDetail: '14 curated technical abstracts with 18 graded queries',
        size: RESEARCH_CORPUS.length,
        labels: RESEARCH_CORPUS.map((d) => d.id),
        description: 'Technical abstracts plus graded queries. Building the index is measured with recall@k and MRR on the held-out query set.',
        createdAt: '2026-01-01T00:00:00.000Z',
      },
      retrieval: { docs: RESEARCH_CORPUS, evaluation: RESEARCH_QUERIES },
    },
    {
      info: {
        id: 'knowledge.corpus',
        name: 'Knowledge · memory + seed corpus',
        specialistId: 'knowledge-ai',
        kind: 'retrieval',
        provenance: 'memory',
        provenanceDetail: 'Knowledge seed notes merged with your stored memory records at build time',
        size: KNOWLEDGE_CORPUS.length + memoryStore.count(),
        labels: KNOWLEDGE_CORPUS.map((d) => d.id),
        description: 'Your notes plus curated platform notes. Rebuild the index after adding notes to keep recall current.',
        createdAt: '2026-01-01T00:00:00.000Z',
      },
      retrieval: { docs: memoryCorpus(), evaluation: KNOWLEDGE_QUERIES },
    },
    {
      info: {
        id: 'general.tool_routing',
        name: 'General · tool selection',
        specialistId: 'general-ai',
        kind: 'routing',
        provenance: 'generated',
        provenanceDetail:
          'Curated seed corpus shipped with AgentOS: disjoint train, calibration and benchmark-holdout splits',
        size: general.train.length,
        labels: [...new Set(general.train.map((i) => i.label))].sort(),
        description: 'Instruction → tool decisions across the whole tool registry, used by the central assistant when no specialist is pinned.',
        createdAt: '2026-01-01T00:00:00.000Z',
      },
      routing: { train: general.train, validation: general.validation, evaluation: general.evaluation },
    },
    {
      info: {
        id: 'voice.lexicon',
        name: 'Voice · pronunciation lexicon',
        specialistId: 'voice-ai',
        kind: 'lexicon',
        provenance: 'generated',
        provenanceDetail: `${lexicon.train.length} training sentences plus ${lexicon.evaluation.length} held-out sentences for the same terms`,
        size: lexicon.train.length,
        labels: VOICE_LEXICON_POOL.map((l) => l.term),
        description: 'Aligned (abbreviation → spoken form) pairs with sentences. Induction learns which reading is correct and reports held-out exact match.',
        createdAt: '2026-01-01T00:00:00.000Z',
      },
      lexicon: { train: lexicon.train, evaluation: lexicon.evaluation },
      normalization: normalization,
    },
    {
      info: {
        id: 'voice.normalization',
        name: 'Voice · speech normalisation',
        specialistId: 'voice-ai',
        kind: 'normalization',
        provenance: 'generated',
        provenanceDetail: '36 curated input/expected pairs for end-to-end speech normalisation',
        size: normalization.train.length,
        labels: [],
        description: 'Raw text → expected spoken text. The held-out split is scored verbatim by the voice benchmark and combines learned lexicon entries with deterministic rules.',
        createdAt: '2026-01-01T00:00:00.000Z',
      },
      normalization: normalization,
    },
  ];
}

/** Live dataset catalogue (user-example datasets appear once they have data). */
export function listDatasets(): DatasetInfo[] {
  const datasets = buildDatasets();
  for (const dataset of datasets) {
    if (dataset.info.id === 'coding.user_examples') {
      const examples = listUserExamples('coding-ai');
      dataset.info.size = examples.length;
      dataset.info.labels = [...new Set(examples.map((e) => e.label))].sort();
    }
    if (dataset.info.id === 'knowledge.corpus') {
      dataset.info.size = KNOWLEDGE_CORPUS.length + memoryStore.count();
    }
  }
  return datasets.map((d) => d.info);
}

export function getDataset(id: string): DatasetPayload | undefined {
  if (id === 'coding.user_examples') {
    const info = listDatasets().find((d) => d.id === id)!;
    const examples = userExamplesAsLabeled('coding-ai');
    return { info, routing: { train: examples.map((e) => ({ text: e.text, label: e.label })), validation: [], evaluation: [] } };
  }
  return buildDatasets().find((d) => d.info.id === id);
}

/**
 * Datasets a specialist may train on: its own first, then any dataset whose
 * kind a compatible method can consume (used by custom specialists).
 */
export function datasetsForSpecialist(specialistId: string, category: string): DatasetInfo[] {
  const all = listDatasets().filter((d) => d.size > 0 || d.specialistId === specialistId);
  const primary = all.filter((d) => d.specialistId === specialistId);
  if (primary.length > 0) return primary;
  return all.filter((d) => category === 'custom' || d.id !== 'coding.user_examples');
}
