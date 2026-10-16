import { round } from './rng.js';
import { tokenizeWords } from './features.js';

export interface RetrievalDoc {
  id: string;
  title: string;
  text: string;
  meta?: Record<string, unknown>;
}

export interface Bm25Hyperparameters {
  k1: number;
  b: number;
  minDf: number;
  charNgrams: boolean;
  titleBoost: number;
}

export const DEFAULT_BM25_HP: Bm25Hyperparameters = {
  k1: 1.2,
  b: 0.75,
  minDf: 1,
  charNgrams: true,
  titleBoost: 2,
};

export interface Bm25Artifact {
  kind: 'retrieval_index';
  formatVersion: 1;
  hyperparameters: Bm25Hyperparameters;
  docCount: number;
  vocabularySize: number;
  averageLength: number;
  builtAt: string;
  /** Serialised postings: term → [[docIndex, termFrequency], …] */
  postings: Record<string, Array<[number, number]>>;
  docIds: string[];
  docTitles: string[];
  docLengths: number[];
  docNorms: number[];
  totalTokens: number;
}

/**
 * BM25 retrieval index over word unigrams/bi-grams with optional character
 * n-grams (helps with typos and inflection). Built in-process, serialised to
 * JSON and loaded back — this is what a Knowledge or Research specialist is
 * trained into when the `retrieval_index` method runs.
 */
export class Bm25Index {
  private postings = new Map<string, Array<[number, number]>>();
  private docIds: string[] = [];
  private docTitles: string[] = [];
  private docLengths: number[] = [];
  private docNorms: number[] = [];
  private totalTokens = 0;
  private averageLength = 0;
  private artifacts: Bm25Hyperparameters = DEFAULT_BM25_HP;
  private vocabularySize = 0;

  static build(docs: RetrievalDoc[], hyperparameters: Partial<Bm25Hyperparameters> = {}): Bm25Index {
    const index = new Bm25Index();
    index.build(docs, hyperparameters);
    return index;
  }

  static fromArtifact(artifact: Bm25Artifact): Bm25Index {
    const index = new Bm25Index();
    index.artifacts = artifact.hyperparameters;
    index.docIds = artifact.docIds.slice();
    index.docTitles = artifact.docTitles.slice();
    index.docLengths = artifact.docLengths.slice();
    index.docNorms = artifact.docNorms.slice();
    index.totalTokens = artifact.totalTokens;
    index.averageLength = artifact.averageLength;
    index.vocabularySize = artifact.vocabularySize;
    index.postings = new Map(Object.entries(artifact.postings).map(([term, entries]) => [term, entries.map((e) => [e[0], e[1]] as [number, number])]));
    return index;
  }

  build(docs: RetrievalDoc[], hyperparameters: Partial<Bm25Hyperparameters> = {}): void {
    this.artifacts = { ...DEFAULT_BM25_HP, ...hyperparameters };
    this.postings = new Map();
    this.docIds = docs.map((d) => d.id);
    this.docTitles = docs.map((d) => d.title);
    this.docLengths = [];
    this.docNorms = [];
    this.totalTokens = 0;

    const documentFrequency = new Map<string, number>();

    docs.forEach((doc, docIndex) => {
      const tokens = this.terms(`${doc.title} ${doc.title} ${doc.text}`, doc.title);
      const counts = new Map<string, number>();
      for (const token of tokens) counts.set(token, (counts.get(token) ?? 0) + 1);
      this.docLengths.push(tokens.length);
      this.totalTokens += tokens.length;
      for (const [term, tf] of counts) {
        const list = this.postings.get(term);
        if (list) list.push([docIndex, tf]);
        else this.postings.set(term, [[docIndex, tf]]);
        documentFrequency.set(term, (documentFrequency.get(term) ?? 0) + 1);
      }
    });

    this.averageLength = docs.length === 0 ? 0 : this.totalTokens / docs.length;

    // Prune rare terms and compute IDF weights per document for fast scoring.
    if (this.artifacts.minDf > 1) {
      for (const [term, df] of documentFrequency) {
        if (df < this.artifacts.minDf) this.postings.delete(term);
      }
    }

    this.vocabularySize = this.postings.size;
    this.docNorms = docs.map(() => 0);
    for (const list of this.postings.values()) {
      const idf = this.idf(list.length);
      for (const [docIndex, tf] of list) {
        this.docNorms[docIndex] += idf * idf * tf;
      }
    }
    for (let i = 0; i < this.docNorms.length; i++) this.docNorms[i] = Math.sqrt(this.docNorms[i]);
  }

  private terms(text: string, title: string): string[] {
    const words = tokenizeWords(text);
    const terms = [...words];
    for (let i = 0; i + 1 < words.length; i++) terms.push(`${words[i]}_${words[i + 1]}`);
    if (this.artifacts.charNgrams) {
      const compact = words.join(' ');
      for (let i = 0; i + 4 <= compact.length; i++) terms.push(`#${compact.slice(i, i + 4)}`);
    }
    const titleTerms = tokenizeWords(title);
    for (let i = 0; i < Math.min(titleTerms.length, 8); i++) {
      // Title tokens are weighted by repetition (cheap title boost for BM25).
      for (let r = 1; r < Math.max(1, Math.round(this.artifacts.titleBoost)); r++) terms.push(titleTerms[i]);
    }
    return terms;
  }

  private idf(documentFrequency: number): number {
    const n = Math.max(1, this.docIds.length);
    return Math.log(1 + (n - documentFrequency + 0.5) / (documentFrequency + 0.5));
  }

  query(text: string, limit = 5): Array<{ id: string; title: string; score: number; index: number }> {
    if (this.docIds.length === 0) return [];
    const { k1, b } = this.artifacts;
    const queryTerms = [...new Set(tokenizeWords(text).concat(...(this.artifacts.charNgrams ? charNgrams(tokenizeWords(text).join(' '), 4) : [])))];
    const scores = new Float64Array(this.docIds.length);
    for (const term of queryTerms) {
      const list = this.postings.get(term);
      if (!list) continue;
      const idf = this.idf(list.length);
      for (const [docIndex, tf] of list) {
        const docLength = this.docLengths[docIndex] || 1;
        const denominator = tf + k1 * (1 - b + (b * docLength) / (this.averageLength || 1));
        scores[docIndex] += idf * ((tf * (k1 + 1)) / (denominator || 1));
      }
    }
    return Array.from(scores)
      .map((score, index) => ({ id: this.docIds[index], title: this.docTitles[index], score: round(score, 5), index }))
      .filter((r) => r.score > 0)
      .sort((a, b2) => b2.score - a.score)
      .slice(0, limit);
  }

  get size(): number {
    return this.docIds.length;
  }

  toArtifact(): Bm25Artifact {
    return {
      kind: 'retrieval_index',
      formatVersion: 1,
      hyperparameters: this.artifacts,
      docCount: this.docIds.length,
      vocabularySize: this.vocabularySize,
      averageLength: round(this.averageLength, 4),
      builtAt: new Date().toISOString(),
      postings: Object.fromEntries(this.postings),
      docIds: this.docIds,
      docTitles: this.docTitles,
      docLengths: this.docLengths,
      docNorms: this.docNorms.map((n) => round(n, 6)),
      totalTokens: this.totalTokens,
    };
  }
}

function charNgrams(text: string, n: number): string[] {
  const out: string[] = [];
  for (let i = 0; i + n <= text.length; i++) out.push(`#${text.slice(i, i + n)}`);
  return out;
}

export interface RetrievalQuery {
  query: string;
  /** Documents that should be retrieved, best first. */
  relevantIds: string[];
}

export interface RetrievalMetrics {
  /** Fraction of relevant documents found within the top k, averaged. */
  recallAtK: number;
  precisionAtK: number;
  mrr: number;
  hitRate: number;
  queries: number;
}

/** Standard retrieval metrics — the objective function for index training. */
export function evaluateRetrieval(index: Bm25Index, queries: RetrievalQuery[], k = 5): RetrievalMetrics {
  if (queries.length === 0) return { recallAtK: 0, precisionAtK: 0, mrr: 0, hitRate: 0, queries: 0 };
  let recall = 0;
  let precision = 0;
  let reciprocalRank = 0;
  let hits = 0;

  for (const item of queries) {
    const results = index.query(item.query, k).map((r) => r.id);
    const relevant = new Set(item.relevantIds);
    const found = results.filter((id) => relevant.has(id));
    recall += relevant.size === 0 ? 0 : found.length / relevant.size;
    precision += results.length === 0 ? 0 : found.length / results.length;
    if (found.length > 0) hits++;
    const rank = results.findIndex((id) => relevant.has(id));
    reciprocalRank += rank === -1 ? 0 : 1 / (rank + 1);
  }

  return {
    recallAtK: round(recall / queries.length, 4),
    precisionAtK: round(precision / queries.length, 4),
    mrr: round(reciprocalRank / queries.length, 4),
    hitRate: round(hits / queries.length, 4),
    queries: queries.length,
  };
}
