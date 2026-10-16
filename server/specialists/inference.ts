import {
  AgentTraceStep,
  SpeechNormalizationResult,
  SpecialistTestResult,
} from '../../shared/types.js';
import { logger } from '../logger.js';
import { memoryStore } from '../memory/store.js';
import { Bm25Index } from '../ml/retrieval.js';
import { TaskHead, TaskHeadArtifact } from '../ml/softmax.js';
import { LexiconArtifact, normalizeForSpeech } from '../ml/lexicon.js';
import { versionStore, ArtifactKind } from '../models/versions.js';
import { providerRegistry } from '../providers/registry.js';
import { settingsStore } from '../settingsStore.js';
import { toolRegistry } from '../tools/registry.js';
import { addUserExample } from '../training/datasets.js';
import { getSpecialist } from './registry.js';
import { planStrategyWithRules } from './policy.js';

/**
 * The inference layer.
 *
 * Everything here is honest about where an answer comes from: a trained local
 * model, the deterministic rule baseline, a tool, or an escalated model
 * provider. The returned `engine` string is shown verbatim in the UI and in
 * every scorecard, so a specialist can never appear better trained than it is.
 */

/** How the decision was reached — shown in the UI and in benchmark results. */
export type DecisionSource = 'model' | 'rules' | 'none';

export interface Decision {
  /** `model` = a trained local artifact, `rules` = the built-in rule baseline. */
  source: DecisionSource;
  engine: string;
  versionId: string | null;
  versionLabel: string | null;
  /** Predicted tool id, `extract.url`, `normalize`, or a retrieved document id. */
  label: string | null;
  confidence: number;
  probabilities: Array<{ label: string; probability: number }>;
  escalated: boolean;
  reason: string | null;
  retrieval: Array<{ id: string; title: string; score: number }>;
  lexiconEntries: number;
  decisionMs: number;
}

export interface RunOptions {
  /** Persist the input plus the predicted label as a new training example. */
  saveAsExample?: boolean;
  /** Never call a provider (used by measurements that must stay local). */
  offline?: boolean;
  /** Measure a specific version instead of the active one (benchmarks, reviews). */
  versionId?: string;
  /** Do not escalate: report what the local path can do and stop. */
  skipEscalation?: boolean;
  /** Where the call came from — chat, the playground, a test or a measurement. */
  mode?: 'chat' | 'playground' | 'test' | 'benchmark';
}

export interface DecideOptions {
  /** Evaluate a specific version instead of the active one. */
  versionId?: string;
  /** Allow the built-in rule planner to answer when the model is unsure. */
  allowRuleFallback?: boolean;
}

/** Monotonic conversion of a raw BM25 score into a 0..1 confidence. */
export function scoreToConfidence(score: number): number {
  return Math.min(0.99, 1 - Math.exp(-score / 4));
}

function loadArtifact(
  specialistId: string,
  versionId?: string,
): {
  versionId: string | null;
  versionLabel: string | null;
  kind: ArtifactKind | null;
  artifact: unknown;
} {
  const version = versionId ? versionStore.get(versionId) : versionStore.activeVersion(specialistId);
  if (!version || version.specialistId !== specialistId) {
    return { versionId: null, versionLabel: null, kind: null, artifact: null };
  }
  return {
    versionId: version.id,
    versionLabel: version.label,
    kind: version.artifactKind as ArtifactKind,
    artifact: versionStore.artifact(version.id),
  };
}

const UNTRAINED_ENGINE = 'untrained — no local model, requests escalate to a provider';

export function decide(specialistId: string, input: string, options: DecideOptions = {}): Decision {
  const started = performance.now();
  const specialist = getSpecialist(specialistId);
  const base = {
    source: 'none' as DecisionSource,
    versionId: null as string | null,
    versionLabel: null as string | null,
    label: null as string | null,
    confidence: 0,
    probabilities: [] as Array<{ label: string; probability: number }>,
    escalated: true,
    reason: null as string | null,
    retrieval: [] as Array<{ id: string; title: string; score: number }>,
    lexiconEntries: 0,
    decisionMs: 0,
  };

  if (!specialist) {
    return { ...base, engine: `unknown specialist "${specialistId}"`, reason: 'No specialist is registered with that id.', decisionMs: performance.now() - started };
  }

  const settings = settingsStore.get();
  const loaded = loadArtifact(specialistId, options.versionId);
  const rulesAllowed = options.allowRuleFallback ?? settings.routing.allowRuleFallback;

  /**
   * Deterministic fallback. It runs when nothing is trained yet, and when a
   * trained model is not confident enough — so bringing a specialist online
   * never removes capability the platform already had.
   */
  const fromRules = (why: string): Decision | null => {
    if (!rulesAllowed) return null;
    if (specialist.category === 'voice') {
      const artifact = loaded.kind === 'lexicon_induction' ? (loaded.artifact as LexiconArtifact) : null;
      const entries = artifact?.stats.learned ?? 0;
      return {
        ...base,
        source: 'rules',
        engine: artifact
          ? `rules + learned lexicon (${loaded.versionLabel}) · ${entries} pronunciation entries`
          : 'deterministic rules only (no learned lexicon yet)',
        versionId: loaded.versionId,
        versionLabel: loaded.versionLabel,
        label: 'normalize',
        confidence: artifact ? 0.9 : 0.5,
        probabilities: [{ label: 'normalize', probability: artifact ? 0.9 : 0.5 }],
        escalated: false,
        reason: null,
        lexiconEntries: entries,
        decisionMs: performance.now() - started,
      };
    }
    const plan = planStrategyWithRules(input, specialist.toolIds);
    if (!plan) return null;
    return {
      ...base,
      engine: `rule planner (built-in) — ${why}`,
      versionId: loaded.versionId,
      versionLabel: loaded.versionLabel,
      label: plan.toolId,
      confidence: plan.confidence,
      probabilities: [{ label: plan.toolId, probability: plan.confidence }],
      escalated: false,
      reason: null,
      decisionMs: performance.now() - started,
    };
  };

  if (!loaded.artifact || !loaded.kind) {
    const rules = fromRules(`${specialist.name} has no trained version`);
    if (rules) return rules;
    return {
      ...base,
      engine: UNTRAINED_ENGINE,
      reason: `${specialist.name} has no trained model version yet and no rule matches this request — it is escalated instead of guessed at.`,
      decisionMs: performance.now() - started,
    };
  }

  if (loaded.kind === 'task_head_sgd') {
    const artifact = loaded.artifact as TaskHeadArtifact;
    const head = new TaskHead(artifact);
    const prediction = head.predict(input);
    const probabilities = artifact.labels
      .map((label, index) => ({ label, probability: Number(prediction.probabilities[index].toFixed(4)) }))
      .sort((a, b) => b.probability - a.probability);
    const threshold = artifact.calibration?.threshold ?? settings.routing.confidenceThreshold;
    const preferHeads = settings.routing.preferTrainedHeads;
    const belowThreshold = prediction.confidence < threshold;
    if (belowThreshold || !preferHeads) {
      const why = !preferHeads
        ? 'trained head disabled in Settings → Routing'
        : `head only ${(prediction.confidence * 100).toFixed(1)}% confident in "${prediction.label}"`;
      const rules = fromRules(why);
      if (rules) {
        return {
          ...rules,
          probabilities,
          reason: `${why} — rule baseline answered instead.`,
        };
      }
    }
    const escalated = belowThreshold || !preferHeads;
    const reason = !preferHeads
      ? 'Trained heads are disabled in Settings → Routing, so every request escalates.'
      : belowThreshold
        ? `Top decision "${prediction.label}" scored ${(prediction.confidence * 100).toFixed(1)}% — below the ${(threshold * 100).toFixed(0)}% confidence threshold and no rule matched.`
        : null;
    return {
      ...base,
      source: 'model',
      engine: `trained task head (${loaded.versionLabel}) · softmax SGD · ${artifact.featureDim} hashed features · ${artifact.labels.length} labels`,
      versionId: loaded.versionId,
      versionLabel: loaded.versionLabel,
      label: prediction.label,
      confidence: Number(prediction.confidence.toFixed(4)),
      probabilities,
      escalated,
      reason,
      decisionMs: performance.now() - started,
    };
  }

  if (loaded.kind === 'retrieval_index') {
    const artifact = versionStore.artifact(loaded.versionId!) as Parameters<typeof Bm25Index.fromArtifact>[0];
    const index = Bm25Index.fromArtifact(artifact);
    const hits = index.query(input, 5);
    if (hits.length === 0) {
      const rules = fromRules('the index returned no passage');
      if (rules) return rules;
      return {
        ...base,
        engine: `retrieval index (${loaded.versionLabel}) · ${index.size} documents · BM25`,
        versionId: loaded.versionId,
        versionLabel: loaded.versionLabel,
        reason: 'Nothing in the index matched this request closely enough — escalating instead of inventing an answer.',
        decisionMs: performance.now() - started,
      };
    }
    const retrieval = hits.map((hit) => ({ id: hit.id, title: hit.title, score: hit.score }));
    const confidence = scoreToConfidence(hits[0].score);
    const belowThreshold = confidence < settings.routing.confidenceThreshold;
    return {
      ...base,
      source: 'model',
      engine: `retrieval index (${loaded.versionLabel}) · ${index.size} documents · BM25`,
      versionId: loaded.versionId,
      versionLabel: loaded.versionLabel,
      label: hits[0].id,
      confidence: Number(confidence.toFixed(4)),
      probabilities: retrieval.map((hit) => ({ label: hit.title, probability: Number(scoreToConfidence(hit.score).toFixed(4)) })),
      retrieval,
      escalated: belowThreshold || !settings.routing.preferTrainedHeads,
      reason: belowThreshold ? `Best hit scored only ${(confidence * 100).toFixed(1)}% confidence.` : null,
      decisionMs: performance.now() - started,
    };
  }

  // Lexicon: a deterministic pipeline plus everything it learned from data.
  const artifact = loaded.artifact as LexiconArtifact;
  const entries = artifact?.stats?.learned ?? 0;
  return {
    ...base,
    source: 'model',
    engine: `deterministic rules + learned lexicon (${loaded.versionLabel}) · ${entries} pronunciation ${entries === 1 ? 'entry' : 'entries'}`,
    versionId: loaded.versionId,
    versionLabel: loaded.versionLabel,
    label: 'normalize',
    confidence: 1,
    probabilities: [{ label: 'normalize', probability: 1 }],
    escalated: false,
    lexiconEntries: entries,
    decisionMs: performance.now() - started,
  };
}

/** Deterministic tool arguments derived from the instruction text. */
export function toolInputFor(toolId: string, input: string): Record<string, unknown> {
  const fileMatch = input.match(/[\w./-]+\.(?:tsx|jsx|json|ya?ml|toml|html|ts|js|md|txt|css|py)/i);
  const urlMatch = input.match(/https?:\/\/[^\s"'<>)]+/i);
  switch (toolId) {
    case 'calc.evaluate': {
      // The tool needs the expression, not the instruction around it.
      const expression = extractExpression(input);
      return expression ? { expression } : {};
    }
    case 'workspace.read':
      return { path: fileMatch?.[0] ?? '.' };
    case 'workspace.list':
      return { path: '.' };
    case 'memory.search':
      return { query: input };
    case 'memory.save':
      return { title: `Note from ${new Date().toISOString().slice(0, 10)}: ${input.slice(0, 48)}`, content: input };
    case 'memory.write':
      return { title: input.slice(0, 60), content: input };
    case 'http.fetch':
      return { url: urlMatch?.[0] ?? '' };
    case 'system.info':
      return {};
    default:
      return {};
  }
}

function summarizeToolOutput(toolId: string, output: unknown, input: Record<string, unknown> = {}): string {
  if (toolId === 'calc.evaluate') {
    const result = (output as { result?: number }).result;
    // The tool returns the value; the expression it evaluated comes from the
    // call input, which is what the user actually asked about.
    const expression = (output as { expression?: string }).expression ?? (input.expression as string | undefined);
    return expression ? `Result: ${expression} = ${result}` : `Result: ${result}`;
  }
  if (toolId === 'workspace.read') {
    const content = String((output as { content?: string }).content ?? '');
    const path = (output as { path?: string }).path ?? 'file';
    const lines = content.split('\n').length;
    const preview = content.length > 1400 ? `${content.slice(0, 1400)}\n…` : content;
    return `${path} (${lines} lines)\n\n${preview}`;
  }
  if (toolId === 'workspace.list') {
    const entries = (output as { entries?: Array<{ name: string; type: string; size?: number }> }).entries ?? [];
    return entries.length
      ? entries.map((entry) => `${entry.type === 'directory' ? '📁' : '📄'} ${entry.name}${entry.size ? ` (${entry.size} B)` : ''}`).join('\n')
      : 'The directory is empty.';
  }
  if (toolId === 'system.info') {
    const info = output as Record<string, unknown>;
    return Object.entries(info)
      .map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
      .join('\n');
  }
  if (toolId === 'memory.search') {
    const records = (output as { records?: Array<{ title: string; content: string }> }).records ?? [];
    return records.length
      ? records.map((record, index) => `${index + 1}. ${record.title}\n   ${record.content.slice(0, 200)}`).join('\n')
      : 'No stored note matched.';
  }
  if (toolId === 'memory.save' || toolId === 'memory.write') {
    const id = (output as { id?: string }).id;
    return id ? `Saved to memory as ${id}.` : 'Saved to memory.';
  }
  if (toolId === 'http.fetch') {
    const body = String((output as { body?: string }).body ?? '');
    return body.slice(0, 900);
  }
  return JSON.stringify(output).slice(0, 600);
}

/** Extracts the host + path of the first URL in the text (no scheme, no query). */
export function extractUrlTarget(text: string): string | null {
  const match = text.match(/https?:\/\/([^\s"'<>)]+)/i);
  if (!match) return null;
  const raw = match[1].replace(/[.,;:]+$/, '');
  const withoutWww = raw.replace(/^www\./i, '');
  return withoutWww.split('#')[0].replace(/\/$/, '');
}

/** Extracts a balanced arithmetic expression from free text. */
export function extractExpression(text: string): string | null {
  const match = text.match(/(-?\d[\d\s+\-*/%^().]*\d|\d\s*[+\-*/%^]\s*\d)/);
  if (!match || match.index === undefined) return null;
  let candidate = match[1].replace(/\s+/g, '');
  let open = 0;
  for (const char of candidate) {
    if (char === '(') open++;
    else if (char === ')') open--;
  }
  // Re-attach closing brackets the character class stopped before, so
  // "12 * (4 + 3)" is not handed to the calculator as "12*(4+3".
  let cursor = match.index + match[0].length;
  while (open > 0 && text[cursor] === ')' && open < 8) {
    candidate += ')';
    open--;
    cursor++;
  }
  if (open !== 0) return null;
  return /[+\-*/%^]/.test(candidate) ? candidate : null;
}

function retrieveAnswer(retrieval: Array<{ id: string; title: string; score: number }>): string {
  const lines = [`Retrieved from the local index (${retrieval.length} passages):`];
  for (const [index, hit] of retrieval.entries()) {
    const record = memoryStore.list(500).find((entry) => entry.id === hit.id);
    const snippet = record ? record.content.slice(0, 240) : `Document ${hit.id}`;
    lines.push(`${index + 1}. ${hit.title} — score ${hit.score.toFixed(2)}\n   ${snippet}`);
  }
  lines.push('', 'This answer is assembled from indexed documents, not generated by a language model.');
  return lines.join('\n');
}

async function escalate(
  specialistId: string,
  input: string,
  reason: string,
  context: string,
): Promise<{ answer: string; error: string | null; trace: AgentTraceStep }> {
  const specialist = getSpecialist(specialistId);
  const settings = settingsStore.get();
  const provider = await providerRegistry.preferred();
  try {
    const result = await provider.generate({
      model: provider.id === 'openai' ? settings.providers.openai.model : settings.providers.ollama.model,
      prompt: input,
      system: [specialist?.systemPrompt ?? 'You are AgentOS.', context].filter(Boolean).join('\n\n'),
      temperature: specialist?.params.temperature ?? 0.5,
      maxTokens: specialist?.params.maxTokens ?? 700,
    });
    return {
      answer: result.text,
      error: null,
      trace: {
        step: 'escalate',
        detail: `${provider.info().name} answered (${provider.id === 'builtin' ? 'offline engine' : 'external provider'}) — ${reason}`,
        status: 'ok',
      },
    };
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    logger.warn('specialists', `Escalation to ${provider.id} failed: ${message}`);
    return {
      answer: `The specialist could not complete this request: ${message}\n\nRetry, or configure a model provider in Settings → Model providers.`,
      error: message,
      trace: { step: 'escalate', detail: `Provider failure: ${message}`, status: 'error' },
    };
  }
}

export async function runSpecialist(
  specialistId: string,
  input: string,
  options: RunOptions = {},
): Promise<SpecialistTestResult> {
  const started = performance.now();
  const specialist = getSpecialist(specialistId);
  if (!specialist) throw new Error(`Unknown specialist "${specialistId}"`);
  const decision = decide(specialistId, input, { versionId: options.versionId });
  const trace: AgentTraceStep[] = [
    {
      step: 'decide',
      detail:
        `[${decision.source === 'model' ? 'local model' : decision.source === 'rules' ? 'built-in rules' : 'no local path'}] ` +
        decision.engine +
        (decision.label ? ` → ${decision.label} (${(decision.confidence * 100).toFixed(1)}%)` : '') +
        (decision.reason ? ` — ${decision.reason}` : ''),
      status: decision.escalated ? 'warn' : 'ok',
      durationMs: Number(decision.decisionMs.toFixed(2)),
    },
  ];
  const toolCalls: SpecialistTestResult['toolCalls'] = [];
  let answer = '';
  let providerError: string | null = null;
  let escalated = decision.escalated;
  let answeredBy: SpecialistTestResult['answeredBy'] = 'local';

  if (!escalated && decision.label) {
    if (decision.retrieval.length > 0 && decision.label !== 'normalize') {
      answer = retrieveAnswer(decision.retrieval);
      trace.push({ step: 'retrieve', detail: `${decision.retrieval.length} passages from the index`, status: 'ok' });
    } else if (decision.label === 'normalize') {
      const normalized = normalizeSpeechFor(specialistId, input);
      answer = normalized.text;
      trace.push({
        step: 'normalize',
        detail: `${normalized.applied.length} learned expansion(s) applied, ${normalized.lexiconEntries} in the lexicon`,
        status: 'ok',
      });
    } else if (decision.label === 'extract.url') {
      const target = extractUrlTarget(input);
      const settings = settingsStore.get();
      if (!target) {
        answer = 'No URL was found in that request.';
        trace.push({ step: 'extract', detail: 'No URL present in the input', status: 'warn' });
      } else if (!settings.permissions.allowNetwork) {
        answer = `Target identified: ${target}\n\nNetwork access is disabled in Settings → Permissions, so the page was not fetched. Enable it to let the Research specialist read the resource.`;
        trace.push({ step: 'extract', detail: `Detected ${target} (network access disabled)`, status: 'warn' });
      } else {
        const call = await toolRegistry.execute('http.fetch', { url: `https://${target}` });
        toolCalls.push({ toolId: 'http.fetch', success: call.success, durationMs: call.durationMs, summary: call.error ?? 'fetched' });
        answer = call.success
          ? `Fetched ${target}:\n\n${summarizeToolOutput('http.fetch', call.output)}`
          : `Could not fetch ${target}: ${call.error ?? 'unknown error'}`;
        trace.push({
          step: 'tool:http.fetch',
          detail: call.success ? `Fetched ${target}` : (call.error ?? 'failed'),
          status: call.success ? 'ok' : 'warn',
          durationMs: call.durationMs,
        });
      }
    } else {
      const toolInput = toolInputFor(decision.label, input);
      const call = await toolRegistry.execute(decision.label, toolInput);
      toolCalls.push({ toolId: decision.label, success: call.success, durationMs: call.durationMs, summary: call.error ?? 'ok' });
      if (call.success) {
        answer = summarizeToolOutput(decision.label, call.output, toolInput);
        trace.push({ step: `tool:${decision.label}`, detail: `Executed ${decision.label}`, status: 'ok', durationMs: call.durationMs });
      } else {
        // A tool the model chose but cannot run is a real failure: escalate
        // rather than returning a half-truth.
        escalated = true;
        trace.push({ step: `tool:${decision.label}`, detail: call.error ?? 'Tool failed', status: 'warn', durationMs: call.durationMs });
      }
    }
  }

  if (escalated) {
    const retrievalContext = decision.retrieval.length
      ? `Relevant notes:\n${decision.retrieval.map((hit) => `- ${hit.title}`).join('\n')}`
      : '';
    const reason = decision.reason ?? 'The specialist could not resolve this locally.';
    if (options.offline || options.skipEscalation) {
      providerError = options.offline ? 'model provider disabled for this measurement' : null;
      answer = `No local answer available (${reason})`;
      trace.push({ step: 'escalate', detail: `Escalation skipped: ${reason}`, status: 'skipped' });
    } else {
      const result = await escalate(specialistId, input, reason, retrievalContext);
      answer = result.answer;
      providerError = result.error;
      trace.push(result.trace);
      answeredBy = result.error ? 'local' : 'offline-engine';
    }
  }

  let savedExampleId: string | null = null;
  if (options.saveAsExample && decision.label) {
    const example = addUserExample(specialistId, { text: input, label: decision.label, source: 'playground' });
    savedExampleId = example.id;
    trace.push({ step: 'dataset', detail: `Saved as training example labelled "${decision.label}"`, status: 'ok' });
    logger.info('specialists', `Saved playground example ${example.id} for ${specialistId}`);
  }

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
    escalationReason: escalated ? decision.reason : null,
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

export function normalizeSpeechFor(
  specialistId: string,
  text: string,
  options: { versionId?: string } = {},
): SpeechNormalizationResult {
  const loaded = loadArtifact(specialistId, options.versionId);
  const artifact = loaded.kind === 'lexicon_induction' ? (loaded.artifact as LexiconArtifact | null) : null;
  // Which learned expansions this text actually used — shown in the UI so the
  // effect of training is visible sentence by sentence.
  const applied: Array<{ from: string; to: string }> = [];
  for (const entry of Object.values(artifact?.entries ?? {})) {
    const escaped = entry.term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    if (new RegExp(`(^|[^A-Za-z0-9])(${escaped})(?![A-Za-z0-9])`, 'i').test(text)) {
      applied.push({ from: entry.term, to: entry.spoken });
    }
  }
  return {
    text: normalizeForSpeech(text, artifact),
    engine: artifact
      ? `deterministic rules + learned lexicon (${loaded.versionLabel}) · ${artifact.stats.learned} entries`
      : 'deterministic rules only — no learned lexicon yet',
    versionId: loaded.versionId,
    lexiconEntries: artifact?.stats.learned ?? 0,
    applied,
  };
}
