import { round } from './rng.js';

/**
 * BM25 retrieval index.
 *
 * The Knowledge and Research specialists answer from documents instead of
 * generating prose: a ranked list of passages the user can verify. BM25 is a
 * real scoring function — term frequency saturation (k1), length normalisation
 * (b), inverse document frequency — and the index is a plain JSON artefact that
 * can be inspected, versioned and compared like any other model.
 */

export interface RetrievalDoc {
  id: string;
  title: string;
  text: string;
  tags?: string[];
}

export interface RetrievalQuery {
  query: string;
  relevant: string[];
}

export interface Bm25Options {
  k1: number;
  b: number;
  /** Ignore terms that appear in fewer documents than this. */
  minDf: number;
  /** Title terms count this many times, because a title states the topic. */
  titleBoost: number;
  /** Add character 4-grams so paraphrases and compounds still match. */
  charNgrams: boolean;
}

export const DEFAULT_BM25_OPTIONS: Bm25Options = { k1: 1.2, b: 0.75, minDf: 1, titleBoost: 2, charNgrams: true };

export interface Bm25Artifact {
  kind: 'retrieval_index';
  formatVersion: 1;
  options: Bm25Options;
  documents: RetrievalDoc[];
  /** Per-document token counts, needed for length normalisation at query time. */
  lengths: Record<string, number>;
  documentFrequency: Record<string, number>;
  averageLength: number;
  vocabularySize: number;
  documentCount: number;
  builtAt: string;
}

export interface RetrievalMetrics {
  recallAtK: number;
  precisionAtK: number;
  /** Mean reciprocal rank of the first relevant hit. */
  mrr: number;
  meanReciprocalRank: number;
  hitRate: number;
  queries: number;
  k: number;
}

const STOPWORDS = new Set([
  'the', 'a', 'an', 'and', 'or', 'of', 'to', 'in', 'is', 'are', 'was', 'were', 'be', 'been', 'for', 'on', 'with',
  'that', 'this', 'it', 'as', 'by', 'at', 'from', 'we', 'you', 'do', 'does', 'did', 'how', 'what', 'which', 'when',
  'where', 'can', 'could', 'should', 'would', 'my', 'our', 'your', 'their', 'its', 'into', 'than', 'then', 'there',
]);

/** Tokenisation for indexing: lower-case, stopwords removed, compounds kept. */
export function tokenizeForIndex(text: string, options: { title?: boolean } = {}): string[] {
  const tokens = text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, ' ')
    .split(/\s+/)
    .map((token) => token.replace(/^-+|-+$/g, ''))
    .filter((token) => token.length > 1 && !STOPWORDS.has(token));
  if (options.title) return tokens;
  return tokens;
}

function charNgrams(token: string): string[] {
  if (token.length < 5) return [];
  const grams: string[] = [];
  for (let i = 0; i + 4 <= token.length; i++) grams.push(`#${token.slice(i, i + 4)}`);
  return grams;
}

export class Bm25Index {
  readonly documents: RetrievalDoc[];
  readonly options: Bm25Options;
  private readonly postings = new Map<string, Map<number, number>>();
  private readonly lengths: number[] = [];
  private readonly averageLength: number;

  constructor(documents: RetrievalDoc[], options: Partial<Bm25Options> = {}) {
    this.documents = documents;
    this.options = { ...DEFAULT_BM25_OPTIONS, ...options };
    documents.forEach((doc, index) => {
      const titleTokens = tokenizeForIndex(doc.title, { title: true });
      const bodyTokens = tokenizeForIndex(`${doc.title} ${doc.text} ${(doc.tags ?? []).join(' ')}`);
      const tokens = [...bodyTokens, ...titleTokens.slice(0, Math.max(0, this.options.titleBoost - 1))];
      if (this.options.charNgrams) tokens.push(...bodyTokens.flatMap(charNgrams));
      this.lengths.push(tokens.length);
      for (const token of tokens) {
        const posting = this.postings.get(token) ?? new Map<number, number>();
        posting.set(index, (posting.get(index) ?? 0) + 1);
        this.postings.set(token, posting);
      }
    });
    const total = this.lengths.reduce((sum, length) => sum + length, 0);
    this.averageLength = this.lengths.length === 0 ? 0 : total / this.lengths.length;
  }

  get size(): number {
    return this.documents.length;
  }

  get vocabulary(): number {
    return this.postings.size;
  }

  private idf(token: string): number {
    const df = this.postings.get(token)?.size ?? 0;
    if (df === 0) return 0;
    return Math.log(1 + (this.documents.length - df + 0.5) / (df + 0.5));
  }

  /** BM25 scores for a query, highest first. */
  query(text: string, topK = 5): Array<{ id: string; title: string; score: number; index: number }> {
    const tokens = tokenizeForIndex(text);
    if (tokens.length === 0 || this.documents.length === 0) return [];
    const queryTokens = [...tokens, ...(this.options.charNgrams ? tokens.flatMap(charNgrams) : [])];
    const scores = new Float64Array(this.documents.length);
    for (const token of queryTokens) {
      const posting = this.postings.get(token);
      if (!posting || posting.size < Math.max(1, this.options.minDf)) continue;
      const idf = this.idf(token);
      if (idf <= 0) continue;
      for (const [docIndex, termFrequency] of posting) {
        const length = this.lengths[docIndex] || 1;
        const denominator = termFrequency + this.options.k1 * (1 - this.options.b + (this.options.b * length) / (this.averageLength || 1));
        scores[docIndex] += idf * ((termFrequency * (this.options.k1 + 1)) / (denominator || 1));
      }
    }
    return Array.from(scores)
      .map((score, index) => ({ score, index }))
      .filter((entry) => entry.score > 0)
      .sort((a, b) => b.score - a.score || a.index - b.index)
      .slice(0, topK)
      .map((entry) => ({
        id: this.documents[entry.index].id,
        title: this.documents[entry.index].title,
        score: round(entry.score, 4),
        index: entry.index,
      }));
  }

  toArtifact(): Bm25Artifact {
    const lengths: Record<string, number> = {};
    this.documents.forEach((doc, index) => {
      lengths[doc.id] = this.lengths[index];
    });
    const documentFrequency: Record<string, number> = {};
    for (const [token, posting] of this.postings) documentFrequency[token] = posting.size;
    return {
      kind: 'retrieval_index',
      formatVersion: 1,
      options: this.options,
      documents: this.documents,
      lengths,
      documentFrequency,
      averageLength: round(this.averageLength, 4),
      vocabularySize: this.vocabulary,
      documentCount: this.documents.length,
      builtAt: new Date().toISOString(),
    };
  }

  /** Builds an index from a set of documents (the training entry point). */
  static build(documents: RetrievalDoc[], options: Partial<Bm25Options> = {}): Bm25Index {
    return new Bm25Index(documents, options);
  }

  static fromArtifact(artifact: Bm25Artifact): Bm25Index {
    if (!artifact || artifact.kind !== 'retrieval_index' || !Array.isArray(artifact.documents)) {
      throw new Error('Not a retrieval index artefact.');
    }
    return new Bm25Index(artifact.documents, artifact.options);
  }
}

/** Standard retrieval metrics over a labelled query set. */
export function evaluateRetrieval(index: Bm25Index, queries: RetrievalQuery[], k = 5): RetrievalMetrics {
  if (queries.length === 0) {
    return { recallAtK: 0, precisionAtK: 0, mrr: 0, meanReciprocalRank: 0, hitRate: 0, queries: 0, k };
  }
  let recallSum = 0;
  let precisionSum = 0;
  let reciprocalRankSum = 0;
  let hits = 0;
  for (const item of queries) {
    const ids = index.query(item.query, k).map((hit) => hit.id);
    const relevantFound = item.relevant.filter((id) => ids.includes(id)).length;
    recallSum += item.relevant.length === 0 ? 0 : relevantFound / item.relevant.length;
    precisionSum += ids.length === 0 ? 0 : relevantFound / ids.length;
    const firstRelevant = ids.findIndex((id) => item.relevant.includes(id));
    if (firstRelevant >= 0) {
      reciprocalRankSum += 1 / (firstRelevant + 1);
      hits++;
    }
  }
  const mrr = round(reciprocalRankSum / queries.length, 4);
  return {
    recallAtK: round(recallSum / queries.length, 4),
    precisionAtK: round(precisionSum / queries.length, 4),
    mrr,
    meanReciprocalRank: mrr,
    hitRate: round(hits / queries.length, 4),
    queries: queries.length,
    k,
  };
}
