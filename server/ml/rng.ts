/**
 * Deterministic RNG helpers. Every training and benchmarking routine in
 * AgentOS is seeded so results are reproducible run over run.
 */

/** mulberry32 — small, fast, well-distributed 32-bit PRNG. */
export function createRng(seed: number): () => number {
  let a = seed >>> 0;
  return function next() {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Stable string hash (FNV-1a) used for feature hashing. */
export function fnv1a(input: string, seed = 0x811c9dc5): number {
  let h = seed >>> 0;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

export function hashString(input: string): number {
  return fnv1a(input);
}

/** In-place Fisher–Yates shuffle driven by a seeded PRNG. */
export function shuffle<T>(items: T[], rng: () => number): T[] {
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/** Deterministic train/validation split preserving class distribution. */
export function stratifiedSplit<T>(items: T[], labelOf: (item: T) => string, valFraction: number, rng: () => number) {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = labelOf(item);
    const bucket = groups.get(key);
    if (bucket) bucket.push(item);
    else groups.set(key, [item]);
  }
  const train: T[] = [];
  const val: T[] = [];
  for (const bucket of groups.values()) {
    const shuffled = shuffle(bucket, rng);
    const valCount = Math.max(1, Math.round(shuffled.length * valFraction));
    val.push(...shuffled.slice(0, Math.min(valCount, shuffled.length - 1)));
    train.push(...shuffled.slice(Math.min(valCount, shuffled.length - 1)));
  }
  return { train: shuffle(train, rng), val: shuffle(val, rng) };
}

export function round(value: number, digits = 4): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}
