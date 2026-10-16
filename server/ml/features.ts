/**
 * Feature extraction for the local task heads.
 *
 * Instruction → hashed n-gram features (unigrams, bigrams and character
 * 4-grams), L2-normalised. Hashing keeps the model compact and means no
 * vocabulary has to be stored or aligned between training runs — the same
 * mapping is recomputed deterministically at inference time.
 */

export interface VectorizerConfig {
  featureDim: number;
  useBigrams: boolean;
  useCharGrams: boolean;
}

export const DEFAULT_VECTORIZER: VectorizerConfig = { featureDim: 512, useBigrams: true, useCharGrams: true };

export interface SparseVector {
  indices: Int32Array;
  values: Float64Array;
  dim: number;
}

/** FNV-1a — small, fast and stable across processes. */
export function hashString(input: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

export function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s'%$^*/+\-=().?]/gu, ' ')
    .split(/\s+/)
    .filter((token) => token.length > 0);
}

export class HashingVectorizer {
  readonly config: VectorizerConfig;

  constructor(config: Partial<VectorizerConfig> = {}) {
    const dim = Math.max(64, Math.min(4096, Math.round(config.featureDim ?? DEFAULT_VECTORIZER.featureDim)));
    this.config = {
      featureDim: dim,
      useBigrams: config.useBigrams ?? DEFAULT_VECTORIZER.useBigrams,
      useCharGrams: config.useCharGrams ?? DEFAULT_VECTORIZER.useCharGrams,
    };
  }

  get featureDim(): number {
    return this.config.featureDim;
  }

  /** Raw feature strings for a text — exported for tests and diagnostics. */
  features(text: string): string[] {
    const tokens = tokenize(text);
    const features: string[] = tokens.map((token) => `w:${token}`);
    if (this.config.useBigrams) {
      for (let i = 0; i + 1 < tokens.length; i++) features.push(`b:${tokens[i]}_${tokens[i + 1]}`);
    }
    if (this.config.useCharGrams) {
      const compact = tokens.join(' ');
      for (let i = 0; i + 4 <= compact.length; i++) features.push(`c:${compact.slice(i, i + 4)}`);
    }
    return features;
  }

  vectorize(text: string): SparseVector {
    const dim = this.config.featureDim;
    const features = this.features(text);
    const accumulator = new Map<number, number>();
    for (const feature of features) {
      const index = hashString(feature) % dim;
      accumulator.set(index, (accumulator.get(index) ?? 0) + 1);
    }
    const indices = Int32Array.from(accumulator.keys());
    const raw = Float64Array.from(accumulator.values());
    let norm = 0;
    for (let i = 0; i < raw.length; i++) norm += raw[i] * raw[i];
    norm = Math.sqrt(norm) || 1;
    const values = new Float64Array(raw.length);
    for (let i = 0; i < raw.length; i++) values[i] = raw[i] / norm;
    return { indices, values, dim };
  }
}

export function toDense(vector: SparseVector): Float64Array {
  const dense = new Float64Array(vector.dim);
  for (let i = 0; i < vector.indices.length; i++) dense[vector.indices[i]] = vector.values[i];
  return dense;
}
