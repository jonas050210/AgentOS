import { settingsStore } from '../settingsStore.js';

/**
 * Built-in rule policy.
 *
 * AgentOS keeps a deterministic policy for the cases where rules are precise and
 * reliable (reading a named file, listing a directory, arithmetic, remembering a
 * note). A trained head competes with it: the promotion gate only accepts a
 * version that actually performs better on the held-out benchmark, so the
 * policy is both a safety net and a real baseline to beat.
 */

export interface PolicyPlan {
  toolId: string;
  input: Record<string, unknown>;
  reason: string;
  confidence: number;
}

/**
 * File references in instructions. Alternatives are ordered longest-first so
 * `package.json` is not truncated to `package.ts`-style partial matches.
 */
export const FILE_REFERENCE_PATTERN = /[\w./-]+\.(?:json|ya?ml|toml|tsx|jsx|html|ts|js|md|py|css|txt)/i;
const URL_PATTERN = /https?:\/\/[^\s"'<>)]+/i;
const EXPRESSION_PATTERN = /(-?\d[\d\s+\-*/%^().]*[+\-*/%^]\s*[\d(.]+|\d+\s*[+\-*/%^]\s*\d+)/;

function has(text: string, terms: string[]): boolean {
  return terms.some((term) => text.includes(term));
}

export function planWithRules(input: string, allowedToolIds: string[], specialistId: string): PolicyPlan | null {
  const text = input.toLowerCase();
  const allow = (id: string) => allowedToolIds.includes(id);

  // 1. An explicit URL with a retrieval-capable specialist is an extraction task.
  const url = input.match(URL_PATTERN)?.[0];
  if (url && allow('http.fetch')) {
    return {
      toolId: 'http.fetch',
      input: { url },
      reason: 'Instruction contains a URL and the specialist can fetch it',
      confidence: 0.7,
    };
  }

  // 2. Reading a named file is unambiguous.
  const file = input.match(FILE_REFERENCE_PATTERN)?.[0];
  if (file && allow('workspace.read') && has(text, ['read', 'open', 'cat', 'show', 'what is inside', 'display', 'inspect'])) {
    return { toolId: 'workspace.read', input: { path: file }, reason: `Named file ${file}`, confidence: 0.72 };
  }

  // 3. Directory listing.
  if (allow('workspace.list') && has(text, ['list ', 'list the', 'directory', 'folder', 'what files', 'which files', 'project structure', 'enumerate'])) {
    return { toolId: 'workspace.list', input: { path: '.' }, reason: 'Directory listing request', confidence: 0.7 };
  }

  // 4. Explicit capture requests.
  if (allow('memory.save') && /\b(remember|save|store|note|write (this|that) down|keep a note)\b/.test(text)) {
    return {
      toolId: 'memory.save',
      input: { title: input.slice(0, 60), content: input },
      reason: 'User asked to persist information',
      confidence: 0.68,
    };
  }

  // 5. Recall requests.
  if (allow('memory.search') && /\b(search|look ?up|find|recall|do you remember|what did we|remember anything|notes? about)\b/.test(text)) {
    return { toolId: 'memory.search', input: { query: input }, reason: 'Recall request against the knowledge store', confidence: 0.66 };
  }

  // 6. Host facts.
  if (allow('system.info') && /\b(host|cpu|memory|ram|platform|node version|uptime|load average|machine|system information|cores)\b/.test(text)) {
    return { toolId: 'system.info', input: {}, reason: 'Host information request', confidence: 0.7 };
  }

  // 7. Arithmetic.
  if (allow('calc.evaluate')) {
    const expression = input.match(EXPRESSION_PATTERN)?.[1] ?? (/([\d.]+\s*[+\-*/%^]\s*[\d.]+)/.exec(input)?.[1] ?? null);
    if (expression) {
      return {
        toolId: 'calc.evaluate',
        input: { expression: expression.replace(/\s+/g, '') },
        reason: 'Arithmetic expression detected',
        confidence: 0.75,
      };
    }
  }

  // 8. Vector/voice specialists normalise whatever they are given.
  if (specialistId === 'voice-ai') {
    return { toolId: 'normalize', input: {}, reason: 'Voice specialists normalise text for speech', confidence: 0.6 };
  }

  return null;
}

/** Rule policy that is allowed to run for a specialist, respecting permissions. */
export function planForSpecialist(specialistId: string, input: string, boundToolIds: string[]): PolicyPlan | null {
  // The planner only proposes tools the specialist is bound to; permission
  // enforcement still happens in the tool registry before anything executes.
  const plan = planWithRules(input, boundToolIds, specialistId);
  if (!plan) return null;
  if (plan.toolId === 'http.fetch' && !settingsStore.get().permissions.allowNetwork) {
    // Without network access the useful part is the extracted target.
    return { ...plan, toolId: 'extract.url', reason: 'URL present; network permission is disabled, so only the target is extracted' };
  }
  return plan;
}
