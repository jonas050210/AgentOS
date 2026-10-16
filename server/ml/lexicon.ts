import { round } from './rng.js';

/**
 * Speech lexicon induction + deterministic normalisation rules.
 *
 * The Voice specialist learns a pronunciation lexicon from aligned
 * (abbreviation → spoken form) examples with aligned contexts. This is a
 * statistical learning method rather than gradient descent: candidate
 * expansions are counted, scored by alignment confidence and pruned against a
 * held-out split. The deterministic rules (numbers, currency, symbols) sit
 * underneath and are *not* learned — they are stated as rules in the UI.
 */

export interface LexiconExample {
  /** Raw token as written, e.g. "Dr." */
  term: string;
  /** Spoken form, e.g. "Doctor" */
  spoken: string;
  /** Sentence the term appeared in — used for alignment scoring. */
  context: string;
}

export interface LexiconHyperparameters {
  minCount: number;
  minConfidence: number;
  caseSensitive: boolean;
  pruneThreshold: number;
}

export const DEFAULT_LEXICON_HP: LexiconHyperparameters = {
  minCount: 1,
  minConfidence: 0.6,
  caseSensitive: false,
  pruneThreshold: 0,
};

export interface LexiconEntry {
  term: string;
  spoken: string;
  count: number;
  confidence: number;
  sources: number;
}

export interface LexiconArtifact {
  kind: 'lexicon_induction';
  formatVersion: 1;
  entries: Record<string, LexiconEntry>;
  stats: {
    examples: number;
    uniqueTerms: number;
    learned: number;
    rejected: number;
    meanConfidence: number;
  };
  hyperparameters: LexiconHyperparameters;
  builtAt: string;
}

export interface LexiconTrainingResult {
  artifact: LexiconArtifact;
  /** Exact-match accuracy on the held-out share of the corpus. */
  validationExactMatch: number;
  validationSize: number;
  coverage: number;
  history: Array<{ epoch: number; learned: number; accepted: number; rejected: number; valExactMatch: number }>;
}

interface Candidate {
  term: string;
  spoken: string;
  count: number;
  contexts: Set<string>;
}

/**
 * Induces a lexicon from aligned examples.
 *
 * Each pass over the corpus re-scores candidates: agreement between the term's
 * spoken form and the surrounding context raises confidence, ambiguous terms
 * lose confidence and are pruned. The reported history is a genuine learning
 * curve (coverage and held-out exact match per pass).
 */
export function induceLexicon(
  examples: LexiconExample[],
  hyperparameters: Partial<LexiconHyperparameters> = {},
  options: {
    onEpoch?: (report: LexiconTrainingResult['history'][number]) => void;
    shouldStop?: () => boolean;
    /** Held-out sentences for the same terms; when omitted a tail split is used. */
    validation?: LexiconExample[];
  } = {},
): LexiconTrainingResult {
  const hp: LexiconHyperparameters = { ...DEFAULT_LEXICON_HP, ...hyperparameters };
  if (examples.length < 4) throw new Error('Lexicon induction requires at least 4 aligned examples.');

  const providedValidation = options.validation ?? [];
  const splitAt = Math.max(1, Math.floor(examples.length * 0.75));
  const train = providedValidation.length > 0 ? examples : examples.slice(0, splitAt);
  const validation = providedValidation.length > 0 ? providedValidation : examples.slice(splitAt);
  const key = (term: string) => (hp.caseSensitive ? term : term.toLowerCase());

  const candidates = new Map<string, Map<string, Candidate>>();
  for (const example of train) {
    const k = key(example.term);
    const bySpoken = candidates.get(k) ?? new Map<string, Candidate>();
    const existing = bySpoken.get(example.spoken) ?? { term: example.term, spoken: example.spoken, count: 0, contexts: new Set<string>() };
    existing.count++;
    existing.contexts.add(example.context);
    bySpoken.set(example.spoken, existing);
    candidates.set(k, bySpoken);
  }

  const entries = new Map<string, LexiconEntry>();
  const history: LexiconTrainingResult['history'] = [];
  let rejected = 0;
  const maxPasses = 6;

  for (let pass = 1; pass <= maxPasses; pass++) {
    if (options.shouldStop?.()) break;
    entries.clear();
    rejected = 0;

    for (const [k, bySpoken] of candidates) {
      const ranked = [...bySpoken.values()].sort((a, b) => b.count - a.count);
      const winner = ranked[0];
      const total = ranked.reduce((acc, c) => acc + c.count, 0);
      const agreement = winner.count / total;
      // Context-sensitivity: a term that shows up in many distinct sentences is
      // more likely to be a genuine abbreviation than a one-off typo.
      const contextEvidence = Math.min(1, winner.contexts.size / Math.max(1, winner.count * 0.5));
      const confidence = round(agreement * (0.7 + 0.3 * contextEvidence), 4);

      if (winner.count < hp.minCount || confidence < hp.minConfidence) {
        rejected++;
        continue;
      }
      entries.set(k, {
        term: winner.term,
        spoken: winner.spoken,
        count: winner.count,
        confidence,
        sources: winner.contexts.size,
      });
    }

    // Prune candidates that keep losing to a stronger reading.
    if (hp.pruneThreshold > 0) {
      for (const [k, bySpoken] of candidates) {
        const ranked = [...bySpoken.values()].sort((a, b) => b.count - a.count);
        const loser = ranked[ranked.length - 1];
        if (loser && ranked.length > 1 && loser.count <= hp.pruneThreshold) bySpoken.delete(loser.spoken);
        if (entries.has(k) && bySpoken.get(entries.get(k)!.spoken)) continue;
      }
    }

    const partial = buildArtifact(entries, hp, examples.length, rejected);
    const scored = scoreValidation(validation, partial);
    history.push({
      epoch: pass,
      learned: entries.size,
      accepted: entries.size,
      rejected,
      valExactMatch: scored.exactMatch,
    });
    options.onEpoch?.(history[history.length - 1]);

    // Converged: another pass cannot change the winners.
    if (pass > 1 && history[pass - 1].learned === history[pass - 2].learned) break;
  }

  const artifact = buildArtifact(entries, hp, examples.length, rejected);
  const scored = scoreValidation(validation, artifact);

  return {
    artifact,
    validationExactMatch: scored.exactMatch,
    validationSize: validation.length,
    coverage: round(entries.size / Math.max(1, new Set(examples.map((e) => key(e.term))).size), 4),
    history,
  };
}

function buildArtifact(entries: Map<string, LexiconEntry>, hp: LexiconHyperparameters, exampleCount: number, rejected: number): LexiconArtifact {
  const values = [...entries.values()];
  return {
    kind: 'lexicon_induction',
    formatVersion: 1,
    entries: Object.fromEntries(entries),
    stats: {
      examples: exampleCount,
      uniqueTerms: entries.size + rejected,
      learned: entries.size,
      rejected,
      meanConfidence: round(values.reduce((a, e) => a + e.confidence, 0) / Math.max(1, values.length), 4),
    },
    hyperparameters: hp,
    builtAt: new Date().toISOString(),
  };
}

function scoreValidation(examples: LexiconExample[], artifact: LexiconArtifact): { exactMatch: number; size: number } {
  let correct = 0;
  for (const example of examples) {
    const entry = artifact.entries[example.term.toLowerCase()] ?? artifact.entries[example.term];
    if (entry && entry.spoken.toLowerCase() === example.spoken.toLowerCase()) correct++;
  }
  return { exactMatch: round(examples.length === 0 ? 0 : correct / examples.length, 4), size: examples.length };
}

const ONES = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen'];
const TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'];

/** Deterministic number → spoken words (rules, not learned). */
export function numberToSpeech(value: number): string {
  if (!Number.isFinite(value)) return String(value);
  const negative = value < 0;
  let n = Math.abs(value);
  if (n > 1_000_000) return String(value);
  const integer = Math.floor(n);
  const parts: string[] = [];
  if (integer >= 1000) {
    const thousands = Math.floor(integer / 1000);
    parts.push(`${integerToWords(thousands)} thousand`);
    const rest = integer % 1000;
    if (rest > 0) parts.push(integerToWords(rest));
  } else {
    parts.push(integerToWords(integer));
  }
  let text = parts.join(' ').trim();
  // Decimal digits are spoken one by one, exactly as written ("0.4" → "zero point four").
  const decimals = Math.abs(value).toFixed(6).replace(/0+$/, '').split('.')[1] ?? '';
  if (decimals.length > 0) {
    const digits = decimals.split('').map((d) => ONES[Number(d)]);
    text = `${text} point ${digits.join(' ')}`;
  }
  return (negative ? 'minus ' : '') + text.trim();
}

function integerToWords(n: number): string {
  if (n < 20) return ONES[n];
  if (n < 100) {
    const tens = TENS[Math.floor(n / 10)];
    const rest = n % 10;
    return rest === 0 ? tens : `${tens}-${ONES[rest]}`;
  }
  const hundreds = `${ONES[Math.floor(n / 100)]} hundred`;
  const rest = n % 100;
  return rest === 0 ? hundreds : `${hundreds} ${integerToWords(rest)}`;
}

/**
 * Symbol rules. Currency and percent are handled with their numbers in
 * `expandSpokenNumbers`, because the spoken form leads ("fifteen dollars").
 */
const SYMBOLS: Array<[RegExp, string]> = [
  [/&/g, ' and '],
  [/\bkm\/h\b/gi, 'kilometers per hour'],
  [/\be\.g\./gi, 'for example'],
  [/\bi\.e\./gi, 'that is'],
  [/\bvs\.?\b/gi, 'versus'],
  [/\bms\b/gi, 'milliseconds'],
  [/\bkg\b/gi, 'kilograms'],
  [/\bmb\b/gi, 'megabytes'],
  [/\bgb\b/gi, 'gigabytes'],
  [/%/g, ' percent'],
];

/** Currency and percent written before/after a number, spoken in number-first order. */
function expandSpokenNumbers(text: string): string {
  let output = text.replace(/([$€£])\s?(\d+(?:\.\d+)?)/g, (_match, symbol: string, digits: string) => {
    const currency = symbol === '$' ? 'dollars' : symbol === '€' ? 'euros' : 'pounds';
    return `${numberToSpeech(Number(digits))} ${currency}`;
  });
  output = output.replace(/(\d+(?:\.\d+)?)\s?%/g, (_match, digits: string) => `${numberToSpeech(Number(digits))} percent`);
  // Bare numbers. The lookbehind keeps identifiers such as "p95" intact, and a
  // trailing letter (the "x" in 3.5x) becomes its own spoken token.
  return output.replace(/(?<![\w.])(\d+(?:\.\d+)?)([a-zA-Z])?/g, (_match, digits: string, letter?: string) => {
    const spoken = numberToSpeech(Number(digits));
    return letter ? `${spoken} ${letter}` : spoken;
  });
}

export interface NormalizeOptions {
  expandNumbers?: boolean;
  expandSymbols?: boolean;
  stripMarkdown?: boolean;
}

/**
 * Full speech normalisation pipeline: learned lexicon first, then deterministic
 * rules. When no lexicon is available the rules still run — the specialist is
 * then honestly reported as "rules only, no learned lexicon".
 */
export function normalizeForSpeech(text: string, artifact: LexiconArtifact | null, options: NormalizeOptions = {}): string {
  const { expandNumbers = true, expandSymbols = true, stripMarkdown = true } = options;
  let output = text;

  if (stripMarkdown) {
    output = output
      .replace(/```[\s\S]*?```/g, ' code block ')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/[*_>#]+/g, ' ')
      .replace(/(^|\s)-{2,}(\s|$)/g, ' ');
  }
  output = output.replace(/https?:\/\/\S+/g, ' link ');

  if (artifact) {
    for (const entry of Object.values(artifact.entries)) {
      const escaped = entry.term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const regex = new RegExp(`(^|[^A-Za-z0-9])(${escaped})(?![A-Za-z0-9])`, 'gi');
      output = output.replace(regex, (_match, prefix: string, matched: string) => {
        // Only sentence-case the expansion when the source token was written in
        // title case. All-caps acronyms (OS, ASAP, VM) keep their lower-case
        // spoken form.
        const replacement = /^[A-Z][a-z]/.test(matched) ? capitalize(entry.spoken) : entry.spoken;
        return `${prefix}${replacement}`;
      });
    }
  }

  if (expandSymbols) {
    for (const [pattern, replacement] of SYMBOLS) output = output.replace(pattern, replacement);
  }

  if (expandNumbers) {
    output = expandSpokenNumbers(output);
  }

  return output
    .replace(/\s+/g, ' ')
    .replace(/\s+([,.!?;:])/g, '$1')
    .trim();
}

function capitalize(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}
