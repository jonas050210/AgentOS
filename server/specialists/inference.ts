import {
  AgentTraceStep,
  SpecialistTestResult,
} from '../../shared/types.js';
import { HashingVectorizer } from '../ml/features.js';
import { Bm25Index } from '../ml/retrieval.js';
import { LexiconArtifact, normalizeForSpeech } from '../ml/lexicon.js';
import { TaskHead, TaskHeadArtifact } from '../ml/softmax.js';
import { versionStore, ArtifactKind } from '../models/versions.js';
import { toolRegistry } from '../tools/registry.js';
import { providerRegistry } from '../providers/registry.js';
import { settingsStore } from '../settingsStore.js';
import { logger } from '../logger.js';
import { addUserExample } from '../training/datasets.js';
import { KNOWLEDGE_CORPUS, RESEARCH_CORPUS } from '../training/corpora.js';
import { memoryStore } from '../memory/store.js';
import { FILE_REFERENCE_PATTERN, planForSpecialist } from './policy.js';
import { getSpecialist } from './registry.js';

export interface Decision {
  engine: string;
  versionId: string | null;
  versionLabel: string | null;
  artifactKind: ArtifactKind | null;
  label: string;
  confidence: number;
  probabilities: Array<{ label: string; probability: number }>;
  escalated: boolean;
  escalationReason: string | null;
  retrieval: Array<{ id: string; title: string; score: number }>;
  decisionMs: number;
}

const TOOL_LABELS = new Set([
  'calc.evaluate',
  'workspace.list',
  'workspace.read',
  'system.info',
  'memory.search',
  'memory.save',
  'memory.write',
  'http.fetch',
]);

/** Loads the active artifact for a specialist (cached by the version store). */
function loadActive(
  specialistId: string,
  versionId?: string,
): { versionId: string | null; label: string | null; kind: ArtifactKind | null; artifact: unknown } {
  const version = versionId ? versionStore.get(versionId) ?? null : versionStore.activeVersion(specialistId);
  if (!version || version.specialistId !== specialistId) {
    return { versionId: null, label: null, kind: null, artifact: null };
  }
  return {
    versionId: version.id,
    label: version.label,
    kind: version.artifactKind as ArtifactKind,
    artifact: versionStore.artifact(version.id),
  };
}

/** Monotonic conversion of a raw BM25 score into a 0..1 confidence. */
export function scoreToConfidence(score: number): number {
  return Math.min(0.99, 1 - Math.exp(-score / 4));
}

/**
 * The specialist's decision step. A trained version decides locally; anything
 * below the configured confidence, or with no trained version at all, escalates
 * to a model provider and says so in the trace.
 */
export function decide(
  specialistId: string,
  input: string,
  overrides: { versionId?: string; includePolicy?: boolean } = {},
): Decision {
  const started = performance.now();
  const settings = settingsStore.get();
  const specialist = getSpecialist(specialistId);
  if (!specialist) {
    return {
      engine: 'unknown specialist',
      versionId: null,
      versionLabel: null,
      artifactKind: null,
      label: 'answer.direct',
      confidence: 0,
      probabilities: [],
      escalated: true,
      escalationReason: `No specialist registered with id "${specialistId}"`,
      retrieval: [],
      decisionMs: performance.now() - started,
    };
  }
  const empty: Decision = {
    engine: 'no specialist',
    versionId: null,
    versionLabel: null,
    artifactKind: null,
    label: 'answer.direct',
    confidence: 0,
    probabilities: [],
    escalated: true,
    escalationReason: 'No local decision available',
    retrieval: [],
    decisionMs: 0,
  };

  const loaded: { versionId: string | null; label: string | null; kind: ArtifactKind | null; artifact: unknown } = overrides.versionId
    ? (() => {
        const version = versionStore.get(overrides.versionId!);
        return version
          ? { versionId: version.id, label: version.label, kind: version.artifactKind as ArtifactKind, artifact: versionStore.artifact(version.id) }
          : { versionId: null, label: null, kind: null as ArtifactKind | null, artifact: null };
      })()
    : loadActive(specialistId);

  const base = {
    versionId: loaded.versionId,
    versionLabel: loaded.label,
    artifactKind: loaded.kind,
    retrieval: [] as Array<{ id: string; title: string; score: number }>,
    decisionMs: 0,
  };

  const includePolicy = overrides.includePolicy ?? true;
  const policyFallback = (): Decision | null => {
    if (!includePolicy) return null;
    const plan = planForSpecialist(specialist.id, input, specialist.toolIds);
    if (plan) {
      return {
        ...base,
        engine: loaded.artifact
          ? `rule policy fallback · ${plan.reason}`
          : `rule policy (built-in) · ${plan.reason}`,
        label: plan.toolId,
        confidence: plan.confidence,
        probabilities: [{ label: plan.toolId, probability: plan.confidence }],
        escalated: false,
        escalationReason: null,
        retrieval: [],
        decisionMs: performance.now() - started,
      };
    }
    if (specialist.category === 'voice') {
      return {
        ...base,
        engine: loaded.artifact ? 'lexicon + deterministic rules' : 'rules-only baseline (no learned lexicon)',
        label: 'normalize',
        confidence: loaded.artifact ? 0.9 : 0.5,
        probabilities: [{ label: 'normalize', probability: loaded.artifact ? 0.9 : 0.5 }],
        escalated: false,
        escalationReason: null,
        retrieval: [],
        decisionMs: performance.now() - started,
      };
    }
    return null;
  };

  if (!loaded.artifact || !loaded.kind) {
    const fromPolicy = policyFallback();
    if (fromPolicy) return fromPolicy;
    return {
      ...empty,
      ...base,
      engine: 'untrained — no local decision path',
      escalationReason: 'No trained version for this specialist yet',
      decisionMs: performance.now() - started,
    };
  }

  switch (loaded.kind) {
    case 'task_head_sgd': {
      const artifact = loaded.artifact as TaskHeadArtifact;
      const head = new TaskHead(artifact);
      const prediction = head.predict(input, new HashingVectorizer({ featureDim: artifact.featureDim }));
      const probabilities = artifact.labels
        .map((label, index) => ({ label, probability: Number(prediction.probabilities[index].toFixed(4)) }))
        .sort((a, b) => b.probability - a.probability);
      // A calibrated artefact brings its own measured threshold; Settings is the
      // fallback for versions trained before calibration existed.
      const threshold = artifact.calibration?.threshold ?? settings.routing.confidenceThreshold;
      const lowConfidence = prediction.confidence < threshold;
      const preferHeads = settings.routing.preferTrainedHeads;
      // A weak model decision is not the end of the road: the rule policy still
      // gets a chance before the request escalates to a provider.
      if ((lowConfidence || !preferHeads) && settings.routing.allowRuleFallback) {
        const fromPolicy = policyFallback();
        if (fromPolicy) {
          return {
            ...fromPolicy,
            probabilities,
            engine: `${fromPolicy.engine} (head was ${(prediction.confidence * 100).toFixed(1)}% confident: ${prediction.label})`,
          };
        }
      }
      const escalated = lowConfidence || !preferHeads;
      return {
        engine: `trained head · ${artifact.featureDim} features · ${artifact.labels.length} labels`,
        versionId: loaded.versionId,
        versionLabel: loaded.label,
        artifactKind: loaded.kind,
        label: prediction.label,
        confidence: Number(prediction.confidence.toFixed(4)),
        probabilities,
        escalated,
        escalationReason: !preferHeads
          ? 'Trained heads are disabled in Settings → Routing'
          : lowConfidence
            ? `Confidence ${(prediction.confidence * 100).toFixed(1)}% is below the ${(threshold * 100).toFixed(0)}% threshold`
            : null,
        retrieval: [],
        decisionMs: performance.now() - started,
      };
    }
    case 'retrieval_index': {
      const artifact = loaded.artifact as Parameters<typeof Bm25Index.fromArtifact>[0];
      const index = Bm25Index.fromArtifact(artifact);
      const hits = index.query(input, 5);
      if (hits.length === 0) {
        const fromPolicy = policyFallback();
        if (fromPolicy) return fromPolicy;
        return {
          ...base,
          engine: `BM25 index · ${index.size} docs`,
          label: 'answer.direct',
          confidence: 0,
          probabilities: [],
          escalated: true,
          escalationReason: 'Nothing matched in the index — escalated for a language-model answer',
          decisionMs: performance.now() - started,
        };
      }
      const top = hits[0];
      return {
        ...base,
        engine: `BM25 index · ${index.size} docs`,
        label: top.id,
        confidence: Number(scoreToConfidence(top.score).toFixed(4)),
        probabilities: hits.map((hit) => ({ label: hit.title, probability: Number(scoreToConfidence(hit.score).toFixed(4)) })),
        escalated: false,
        escalationReason: null,
        retrieval: hits.map((hit) => ({ id: hit.id, title: hit.title, score: hit.score })),
        decisionMs: performance.now() - started,
      };
    }
    case 'lexicon_induction': {
      const artifact = loaded.artifact as LexiconArtifact;
      return {
        ...base,
        engine: `lexicon · ${artifact.stats.learned} learned entries + deterministic rules`,
        label: 'normalize',
        confidence: 0.99,
        probabilities: [{ label: 'normalize', probability: 0.99 }],
        escalated: false,
        escalationReason: null,
        retrieval: [],
        decisionMs: performance.now() - started,
      };
    }
    default:
      return {
        ...empty,
        ...base,
        engine: 'unsupported artifact',
        escalationReason: `Artifact kind "${loaded.kind}" has no inference path`,
        decisionMs: performance.now() - started,
      };
  }
}

/** Normalises text for speech with the specialist's learned lexicon when present. */
export function normalizeSpeechFor(
  specialistId: string,
  text: string,
  options: { versionId?: string } = {},
): { text: string; lexiconEntries: number; versionId: string | null } {
  const loaded = loadActive(specialistId, options.versionId);
  const artifact = loaded.kind === 'lexicon_induction' ? (loaded.artifact as LexiconArtifact | null) : null;
  return {
    text: normalizeForSpeech(text, artifact),
    lexiconEntries: artifact?.stats.learned ?? 0,
    versionId: loaded.versionId,
  };
}

/** Deterministic URL target extraction: host + path without scheme or trailing slash. */
export function extractUrlTarget(text: string): string | null {
  const match = text.match(/https?:\/\/([^\s"'<>)]+)/i);
  if (!match) return null;
  const raw = match[1].replace(/[.,;:]+$/, '');
  const withoutWww = raw.replace(/^www\./i, '');
  const trimmedQuery = withoutWww.split('?')[0].replace(/\/$/, '').replace(/#.*$/, '');
  return trimmedQuery;
}

/**
 * Extracts an arithmetic expression from a natural-language instruction.
 *
 * The character class stops before a closing bracket, so `12 * (4 + 3)` would
 * otherwise be handed to the calculator as `12*(4+3` and fail. Balanced closing
 * brackets that directly follow the match are re-attached; a still-unbalanced
 * expression is refused instead of guessed at.
 */
export function extractExpression(text: string): string | null {
  const match = text.match(/(-?\d[\d\s+\-*/%^().]*\d|\(?[\d.]+\)?\s*[+\-*/%^]\s*\(?[\d.]+\)?)/);
  if (!match) return null;
  let candidate = match[1].trim();
  let open = 0;
  for (const char of candidate) {
    if (char === '(') open++;
    else if (char === ')') open--;
  }
  let cursor = (match.index ?? 0) + match[0].length;
  while (open > 0 && text[cursor] === ')') {
    candidate += ')';
    open--;
    cursor++;
  }
  if (open !== 0) return null;
  const compact = candidate.replace(/\s+/g, '');
  return /[+\-*/%^]/.test(compact) ? compact : null;
}

function summarizeToolOutput(toolId: string, output: unknown, success: boolean, error?: string): string {
  if (!success) return `The ${toolId} tool could not run: ${error ?? 'unknown error'}`;
  const value = output as Record<string, unknown>;
  switch (toolId) {
    case 'calc.evaluate':
      return `Result: ${String(value.result)}`;
    case 'system.info':
      return [
        `Platform ${String(value.platform)} (${String(value.arch)}), Node ${String(value.node)}`,
        `${String(value.cpus)} CPU cores · ${String(value.totalMemoryMb)} MB RAM (${String(value.freeMemoryMb)} MB free)`,
        `Host ${String(value.hostname)}`,
      ].join('\n');
    case 'workspace.list': {
      const entries = (value.entries as Array<{ name: string; type: string }>) ?? [];
      return `Workspace contains ${entries.length} entries:\n${entries
        .slice(0, 30)
        .map((e) => `- ${e.name}${e.type === 'dir' ? '/' : ''}`)
        .join('\n')}`;
    }
    case 'workspace.read': {
      const content = String(value.content ?? '');
      return `${String(value.path)}${value.truncated ? ' (truncated)' : ''}:\n\n${content.slice(0, 1500)}`;
    }
    case 'memory.search': {
      const results = (value.results as Array<{ title: string; snippet: string }>) ?? [];
      if (results.length === 0) return 'No matching notes were found in the knowledge store.';
      return `Found ${results.length} note(s):\n${results.map((r) => `- ${r.title}: ${r.snippet.slice(0, 160)}`).join('\n')}`;
    }
    case 'memory.save':
      return `Saved to the knowledge store as ${String(value.id)}.`;
    case 'http.fetch':
      return `Retrieved with status ${String(value.status)} (${String(value.contentType)}):\n\n${String(value.body ?? '').slice(0, 1200)}`;
    default:
      return JSON.stringify(output).slice(0, 800);
  }
}

export interface RunSpecialistOptions {
  versionId?: string;
  /** Deterministic normalisation of text before answering (used by Voice AI). */
  mode?: 'chat' | 'playground';
  saveAsExample?: boolean;
  skipEscalation?: boolean;
}

/**
 * Full specialist turn: decide → act (tool runs are permission-checked) →
 * answer locally or escalate → return a complete trace for the UI.
 */
export async function runSpecialist(specialistId: string, input: string, options: RunSpecialistOptions = {}): Promise<SpecialistTestResult> {
  const started = performance.now();
  const trace: AgentTraceStep[] = [];
  const specialist = getSpecialist(specialistId);
  if (!specialist) throw new Error(`Unknown specialist "${specialistId}"`);
  const settings = settingsStore.get();

  const decision = decide(specialistId, input, { versionId: options.versionId });
  trace.push({
    step: 'decide',
    detail: decision.escalated
      ? `${decision.engine} → escalating (${decision.escalationReason ?? 'no local decision'})`
      : `${decision.engine} → ${decision.label} at ${(decision.confidence * 100).toFixed(1)}% confidence`,
    status: decision.escalated ? 'warn' : 'ok',
    durationMs: Math.round(decision.decisionMs),
  });

  const toolCalls: SpecialistTestResult['toolCalls'] = [];
  let answer = '';
  let escalated = decision.escalated;
  let escalationReason = decision.escalationReason;

  const runTool = async (toolId: string, toolInput: Record<string, unknown>) => {
    if (!specialist.toolIds.includes(toolId)) {
      toolCalls.push({ toolId, success: false, durationMs: 0, summary: 'Not bound to this specialist' });
      trace.push({ step: `tool:${toolId}`, detail: 'Not bound to this specialist — skipped', status: 'warn' });
      return null;
    }
    const outcome = await toolRegistry.execute(toolId, toolInput);
    toolCalls.push({
      toolId,
      success: outcome.success,
      durationMs: outcome.durationMs,
      summary: outcome.success ? summarizeToolOutput(toolId, outcome.output, true).slice(0, 160) : (outcome.error ?? 'failed'),
    });
    trace.push({
      step: `tool:${toolId}`,
      detail: outcome.success ? `Executed in ${outcome.durationMs} ms` : `Blocked or failed: ${outcome.error}`,
      status: outcome.success ? 'ok' : 'warn',
      durationMs: outcome.durationMs,
    });
    return outcome.success ? outcome.output : null;
  };

  // Local handling first: only when the decision says the specialist is ready.
  if (!escalated) {
    if (TOOL_LABELS.has(decision.label)) {
      const input_ =
        decision.label === 'calc.evaluate'
          ? { expression: extractExpression(input) ?? input }
          : decision.label === 'workspace.read'
            ? { path: matchPath(input) ?? '' }
            : decision.label === 'memory.search'
              ? { query: input }
              : decision.label === 'memory.save'
                ? { title: input.slice(0, 60), content: input }
                : decision.label === 'workspace.list'
                  ? { path: '.' }
                  : {};
      const output = await runTool(decision.label, input_);
      if (output !== null) {
        answer = summarizeToolOutput(decision.label, output, true);
      } else {
        escalated = true;
        escalationReason = `Tool ${decision.label} could not run (permission or input) — escalating instead of guessing`;
      }
    } else if (decision.label === 'extract.url') {
      const target = extractUrlTarget(input);
      if (!target) {
        escalated = true;
        escalationReason = 'No URL could be extracted from the instruction';
      } else {
        trace.push({ step: 'extract', detail: `Target: ${target}`, status: 'ok' });
        if (settings.permissions.allowNetwork && specialist.toolIds.includes('http.fetch')) {
          const url = input.match(/https?:\/\/[^\s"'<>)]+/i)?.[0];
          const output = url ? await runTool('http.fetch', { url }) : null;
          answer = output
            ? `Source ${target} retrieved.\n\n${summarizeToolOutput('http.fetch', output, true)}`
            : `Target extracted: ${target}. Fetching failed, so only the target is reported.`;
        } else {
          answer = [
            `Target extracted: ${target}`,
            '',
            'Fetching the page needs the network permission (Settings → Permissions). The research decision itself ran locally.',
          ].join('\n');
          trace.push({ step: 'fetch', detail: 'Network permission disabled — reported the extracted target only', status: 'skipped' });
        }
      }
    } else if (decision.label === 'normalize' || decision.artifactKind === 'lexicon_induction') {
      const normalized = normalizeSpeechFor(specialistId, input);
      answer = normalized.text;
      trace.push({
        step: 'normalize',
        detail: `Speech text produced with ${normalized.lexiconEntries} learned lexicon entries plus deterministic number/symbol rules`,
        status: 'ok',
      });
    } else if (decision.retrieval.length > 0) {
      // Retrieval specialist: answer extractively from the retrieved passages.
      const top = decision.retrieval.slice(0, 3);
      answer = [
        `Retrieved ${top.length} passage(s) from the local index:`,
        '',
        ...top.map((hit, index) => `${index + 1}. ${hit.title} (score ${hit.score})\n   ${snippetFor(hit.id, hit.title)}`),
        '',
        'This answer was assembled from the index, not generated by a language model.',
      ].join('\n');
      trace.push({ step: 'retrieve', detail: `${top.length} passages, top score ${top[0].score}`, status: 'ok' });
    } else if (decision.label === 'answer.direct') {
      escalated = true;
      escalationReason = 'Decision: this request needs language reasoning rather than a tool';
    }
  }

  let providerError: string | null = null;
  let answeredBy: 'local' | 'offline-engine' | 'provider' = 'local';
  if (escalated && !options.skipEscalation) {
    const provider = await providerRegistry.preferred();
    const providerInfo = provider.info();
    const contextBlock = decision.retrieval.length
      ? decision.retrieval.map((hit) => `- ${hit.title} (score ${hit.score})`).join('\n')
      : '';
    try {
      const result = await provider.generate({
        model: provider.id === 'openai' ? settings.providers.openai.model : settings.providers.ollama.model,
        prompt: input,
        system: [specialist.systemPrompt, contextBlock ? `Relevant notes:\n${contextBlock}` : '', 'State clearly when information is missing instead of inventing it.']
          .filter(Boolean)
          .join('\n\n'),
        temperature: specialist.params.temperature,
        maxTokens: specialist.params.maxTokens,
      });
      answer = result.text;
      answeredBy = provider.id === 'builtin' ? 'offline-engine' : 'provider';
      trace.push({
        step: 'escalate',
        detail: `${providerInfo.name} answered (${provider.id === 'builtin' ? 'offline engine' : 'external provider'}) — ${escalationReason ?? 'needed language reasoning'}`,
        status: provider.id === 'builtin' ? 'warn' : 'ok',
      });
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      providerError = message;
      answer = `The specialist could not complete this request: ${message}\n\nRetry, or configure a provider in Settings → Model providers.`;
      trace.push({ step: 'escalate', detail: `Provider failure: ${message}`, status: 'error' });
    }
  } else if (escalated && options.skipEscalation) {
    answer = answer || `No local decision available (${escalationReason ?? 'specialist is untrained'}).`;
    trace.push({ step: 'escalate', detail: 'Escalation skipped for this test run', status: 'skipped' });
  }

  let savedExampleId: string | null = null;
  if (options.saveAsExample && decision.label) {
    const example = addUserExample(specialistId, { text: input, label: decision.label });
    savedExampleId = example.id;
    trace.push({ step: 'dataset', detail: `Saved as training example labelled "${decision.label}"`, status: 'ok' });
  }

  logger.info('specialists', `${specialist.name} handled a request`, {
    label: decision.label,
    escalated,
    engine: decision.engine,
  });

  return {
    specialistId: specialist.id,
    specialistName: specialist.name,
    engine: decision.engine,
    versionId: decision.versionId,
    versionLabel: decision.versionLabel,
    predicted: decision.label,
    confidence: decision.confidence,
    probabilities: decision.probabilities,
    escalated,
    escalationReason: escalated ? escalationReason : null,
    providerError,
    answeredBy,
    toolCalls,
    answer,
    latencyMs: Number((performance.now() - started).toFixed(2)),
    retrieved: decision.retrieval,
    savedExampleId,
    trace,
  };
}

function matchPath(input: string): string | null {
  return input.match(FILE_REFERENCE_PATTERN)?.[0] ?? null;
}

/**
 * Extractive snippets for retrieval answers, taken from the same corpora the
 * index was built from. Unknown ids (a user note) report their stored content.
 */
function snippetFor(id: string, title: string): string {
  const doc = [...RESEARCH_CORPUS, ...KNOWLEDGE_CORPUS].find((d) => d.id === id);
  if (doc) return doc.text.slice(0, 220);
  const record = memoryStore.list(500).find((r) => r.id === id);
  if (record) return record.content.slice(0, 220);
  return `Stored note "${title}" — open the Knowledge workspace to see the full record.`;
}
