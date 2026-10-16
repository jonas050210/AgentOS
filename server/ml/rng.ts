/**
 * Deterministic pseudo-random numbers.
 *
 * Every training run must be reproducible: same seed, same data, same weights.
 * A small explicit generator is used instead of `Math.random()` so that a run
 * can be replayed and compared months later, and so tests can assert exact
 * behaviour.
 */
export class Rng {
  private state: number;

  constructor(seed: number) {
    this.state = (seed >>> 0) || 0x9e3779b9;
  }

  /** mulberry32 — fast, well-distributed, dependency free. */
  next(): number {
    this.state = (this.state + 0x6d2b79f5) >>> 0;
    let t = this.state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  int(maxExclusive: number): number {
    return Math.floor(this.next() * maxExclusive);
  }

  /** Fisher–Yates returning a new array. */
  shuffle<T>(items: T[]): T[] {
    const copy = items.slice();
    this.shuffleInPlace(copy);
    return copy;
  }

  shuffleInPlace<T>(items: T[] | Int32Array): void {
    for (let i = items.length - 1; i > 0; i--) {
      const j = this.int(i + 1);
      const a = items[i] as T;
      const b = items[j] as T;
      (items as T[])[i] = b;
      (items as T[])[j] = a;
    }
  }
}

/** Rounds to a fixed number of decimals — used when serialising artefacts. */
export function round(value: number, digits = 4): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}
