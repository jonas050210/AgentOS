import { fnv1a } from './rng.js';

export interface SparseVector {
  indices: Int32Array;
  values: Float64Array;
}

export interface VectorizerConfig {
  featureDim: number;
  wordUnigrams: boolean;
  wordBigrams: boolean;
  charNgrams: number;
}

export const DEFAULT_VECTORIZER: VectorizerConfig = {
  featureDim: 512,
  wordUnigrams: true,
  wordBigrams: true,
  charNgrams: 4,
};

const WORD_RE = /[a-z0-9]+/g;

export function tokenizeWords(text: string): string[] {
  return text.toLowerCase().match(WORD_RE) ?? [];
}

/**
 * Feature hashing over word unigrams, word bigrams and character n-grams.
 *
 * Hashing keeps the feature space fixed and memory-bounded (no vocabulary that
 * grows with the corpus) and is fully deterministic, which matters because the
 * resulting vectorizer must be reproducible when a saved head is loaded.
 */
export class HashingVectorizer {
  readonly config: VectorizerConfig;

  constructor(config: Partial<VectorizerConfig> = {}) {
    const dim = config.featureDim ?? DEFAULT_VECTORIZER.featureDim;
    this.config = {
      featureDim: Math.max(64, Math.min(8192, Math.round(dim))),
      wordUnigrams: config.wordUnigrams ?? DEFAULT_VECTORIZER.wordUnigrams,
      wordBigrams: config.wordBigrams ?? DEFAULT_VECTORIZER.wordBigrams,
      charNgrams: config.charNgrams ?? DEFAULT_VECTORIZER.charNgrams,
    };
  }

  /** Extracts the raw feature tokens for a text (exported for tests/debugging). */
  features(text: string): string[] {
    const words = tokenizeWords(text);
    const features: string[] = [];
    if (this.config.wordUnigrams) {
      for (const w of words) features.push(`w:${w}`);
    }
    if (this.config.wordBigrams) {
      for (let i = 0; i + 1 < words.length; i++) features.push(`b:${words[i]}_${words[i + 1]}`);
    }
    const n = this.config.charNgrams;
    if (n > 1) {
      const compact = words.join(' ');
      for (let i = 0; i + n <= compact.length; i++) features.push(`c:${compact.slice(i, i + n)}`);
    }
    return features;
  }

  vectorize(text: string): SparseVector {
    const accum = new Map<number, number>();
    for (const feature of this.features(text)) {
      const h = fnv1a(feature);
      const index = h % this.config.featureDim;
      // Second, independent hash decides the sign: signed hashing keeps the
      // expectation of hash collisions at zero.
      const sign = fnv1a(feature, 0x9e3779b1) % 2 === 0 ? 1 : -1;
      accum.set(index, (accum.get(index) ?? 0) + sign);
    }

    const indices = new Int32Array(accum.size);
    const values = new Float64Array(accum.size);
    let i = 0;
    let norm = 0;
    for (const [index, value] of accum) {
      indices[i] = index;
      values[i] = value;
      norm += value * value;
      i++;
    }
    const length = Math.sqrt(norm) || 1;
    for (let k = 0; k < values.length; k++) values[k] = values[k] / length;
    return { indices, values };
  }
}

export function toDense(vector: SparseVector, dim: number): Float64Array {
  const dense = new Float64Array(dim);
  for (let i = 0; i < vector.indices.length; i++) dense[vector.indices[i]] += vector.values[i];
  return dense;
}
