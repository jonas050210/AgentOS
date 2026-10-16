/**
 * Built-in rule policy.
 *
 * AgentOS keeps a deterministic planner for the requests where rules are
 * precise and auditable (open a named file, list a directory, compute an
 * expression, remember a note, fetch a link). It is not a substitute for a
 * trained model — it is the baseline a trained head has to beat, and the
 * fallback that keeps the app useful before anything is trained.
 *
 * Every plan carries a reason so the UI can show *why* a tool was chosen.
 */

export interface PolicyPlan {
  toolId: string;
  input: Record<string, unknown>;
  reason: string;
  /** Fixed confidence of a deterministic rule (not a learned probability). */
  confidence: number;
}

/** Matches a file reference, longest extensions first so `package.json` is not cut to `package.ts`. */
const FILE_REFERENCE = /[\w./-]+\.(?:tsx|jsx|json|ya?ml|toml|html|ts|js|md|txt|css|py)/i;
const URL = /https?:\/\/[^\s"'<>)]+/i;
const EXPRESSION = /(-?\d[\d\s+\-*/%^().]*\d|\d\s*[+\-*/%^]\s*\d)/;

const RULE_CONFIDENCE = 0.62;

function extractExpression(input: string): string | null {
  const match = input.match(EXPRESSION);
  if (!match || match.index === undefined) return null;
  let candidate = match[1].replace(/\s+/g, '');
  let open = 0;
  for (const char of candidate) {
    if (char === '(') open++;
    else if (char === ')') open--;
  }
  // Re-attach closing brackets the character class stopped before.
  let cursor = match.index + match[0].length;
  while (open > 0 && input[cursor] === ')' && open < 8) {
    candidate += ')';
    open--;
    cursor++;
  }
  if (open !== 0) return null;
  return /[+\-*/%^]/.test(candidate) ? candidate : null;
}

/**
 * Deterministic tool plan for one instruction, limited to the tools the
 * specialist is allowed to use.
 */
export function planWithRules(input: string, allowedToolIds: string[], _specialistId?: string): PolicyPlan | null {
  const text = input.toLowerCase();
  const allow = (id: string) => allowedToolIds.includes(id);
  const has = (...terms: string[]) => terms.some((term) => text.includes(term));

  // 1. An explicit URL is a fetch request (Research AI).
  if (URL.test(input) && allow('http.fetch')) {
    return {
      toolId: 'http.fetch',
      input: { url: input.match(URL)![0] },
      reason: 'The request contains a link, so the page is fetched',
      confidence: RULE_CONFIDENCE,
    };
  }

  // 2. Remembering something is explicit and unambiguous.
  if (has('remember', 'save this', 'save a note', 'note that', 'keep this', 'store this', 'write this down', 'make a note') && allow('memory.save')) {
    return {
      toolId: 'memory.save',
      input: { title: input.slice(0, 60), content: input },
      reason: 'The request asks to store information',
      confidence: RULE_CONFIDENCE,
    };
  }

  // 3. A named file to read.
  const file = input.match(FILE_REFERENCE)?.[0];
  if (file && allow('workspace.read')) {
    return {
      toolId: 'workspace.read',
      input: { path: file },
      reason: `The instruction names the file ${file}`,
      confidence: RULE_CONFIDENCE,
    };
  }

  // 4. Directory listings.
  if (has('list files', 'list the files', 'what files', 'which files', 'directory', 'folder structure', 'list the workspace', 'enumerate') && allow('workspace.list')) {
    return {
      toolId: 'workspace.list',
      input: { path: '.' },
      reason: 'The request asks for a directory listing',
      confidence: RULE_CONFIDENCE,
    };
  }

  // 5. Recalling stored notes.
  if (has('remember', 'recall', 'look up', 'look it up', 'did we save', 'what did we save', 'do you know', 'notes about', 'find the note') && allow('memory.search')) {
    return {
      toolId: 'memory.search',
      input: { query: input },
      reason: 'The request asks for previously stored information',
      confidence: RULE_CONFIDENCE,
    };
  }

  // 6. Host information.
  if (has('system info', 'cpu', 'memory usage', 'how much memory', 'uptime', 'platform', 'node version', 'os version', 'free memory', 'hostname') && allow('system.info')) {
    return {
      toolId: 'system.info',
      input: {},
      reason: 'The request asks about the host machine',
      confidence: RULE_CONFIDENCE,
    };
  }

  // 7. Arithmetic.
  const expression = extractExpression(input);
  if (expression && allow('calc.evaluate')) {
    const looksNumeric = /[+\-*/%^]/.test(expression) && /\d/.test(expression);
    if (looksNumeric) {
      return {
        toolId: 'calc.evaluate',
        input: { expression },
        reason: `The instruction contains the expression ${expression}`,
        confidence: RULE_CONFIDENCE,
      };
    }
  }

  return null;
}

/**
 * Planner entry point used by the orchestrator: the specialist id only decides
 * which *strategy* labels exist (research routes links to `extract.url`), the
 * tool list decides what may actually be called.
 */
export function planForSpecialist(specialistId: string, input: string, allowedToolIds: string[]): PolicyPlan | null {
  const isResearch = specialistId === 'research-ai';
  if (isResearch && URL.test(input)) {
    return {
      toolId: 'extract.url',
      input: { url: input.match(URL)![0] },
      reason: 'The request contains a link to read',
      confidence: RULE_CONFIDENCE,
    };
  }
  return planWithRules(input, allowedToolIds, specialistId);
}

/** URL extraction plans are handled by the research pipeline rather than a tool. */
export function planStrategyWithRules(input: string, allowedToolIds: string[]): PolicyPlan | null {
  if (URL.test(input) && (allowedToolIds.includes('http.fetch') || allowedToolIds.length === 0)) {
    return {
      toolId: 'extract.url',
      input: { url: input.match(URL)![0] },
      reason: 'The request contains a link',
      confidence: RULE_CONFIDENCE,
    };
  }
  return planWithRules(input, allowedToolIds);
}
