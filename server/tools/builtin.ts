import fs from 'node:fs/promises';
import os from 'node:os';
import { ToolDefinition } from './types.js';
import { resolveInside } from './paths.js';

const MAX_READ_BYTES = 64 * 1024;
const MAX_WRITE_BYTES = 256 * 1024;
const FETCH_MAX_CHARS = 20_000;
const FETCH_TIMEOUT_MS = 10_000;

export const systemInfoTool: ToolDefinition<Record<string, never>, Record<string, unknown>> = {
  id: 'system.info',
  name: 'System information',
  description: 'Reports host runtime details (platform, CPU count, memory, Node version).',
  category: 'general',
  requires: [],
  inputSchema: { type: 'object', properties: {} },
  async run() {
    return {
      platform: process.platform,
      arch: process.arch,
      node: process.version,
      cpus: os.cpus().length,
      totalMemoryMb: Math.round(os.totalmem() / 1024 / 1024),
      freeMemoryMb: Math.round(os.freemem() / 1024 / 1024),
      loadavg: os.loadavg().map((n) => Number(n.toFixed(2))),
      hostname: os.hostname(),
    };
  },
};

export const calcTool: ToolDefinition<{ expression: string }, { result: number }> = {
  id: 'calc.evaluate',
  name: 'Calculator',
  description: 'Evaluates an arithmetic expression (+ - * / % ^ parentheses). No code execution.',
  category: 'general',
  requires: [],
  inputSchema: {
    type: 'object',
    properties: { expression: { type: 'string', required: true } },
  },
  async run({ expression }) {
    return { result: evaluateArithmetic(expression) };
  },
};

/** Tiny recursive-descent arithmetic parser — intentionally not eval(). */
export function evaluateArithmetic(input: string): number {
  if (typeof input !== 'string' || input.trim() === '') throw new Error('Empty expression');
  const src = input.replace(/\s+/g, '');
  let i = 0;

  const peek = () => src[i];
  const eat = (ch: string) => {
    if (src[i] !== ch) throw new Error(`Expected "${ch}" at position ${i}`);
    i++;
  };
  function parseExpr(): number {
    let value = parseTerm();
    while (peek() === '+' || peek() === '-') {
      const op = src[i++];
      const rhs = parseTerm();
      value = op === '+' ? value + rhs : value - rhs;
    }
    return value;
  }
  function parseTerm(): number {
    let value = parseFactor();
    while (peek() === '*' || peek() === '/' || peek() === '%') {
      const op = src[i++];
      const rhs = parseFactor();
      if ((op === '/' || op === '%') && rhs === 0) throw new Error('Division by zero');
      value = op === '*' ? value * rhs : op === '/' ? value / rhs : value % rhs;
    }
    return value;
  }
  function parseFactor(): number {
    if (peek() === '-') {
      i++;
      return -parseFactor();
    }
    if (peek() === '+') {
      i++;
      return parseFactor();
    }
    return parsePower();
  }
  function parsePower(): number {
    const base = parseAtom();
    if (peek() === '^') {
      i++;
      return Math.pow(base, parseFactor());
    }
    return base;
  }
  function parseAtom(): number {
    if (peek() === '(') {
      i++;
      const v = parseExpr();
      eat(')');
      return v;
    }
    const start = i;
    while (i < src.length && /[0-9.]/.test(src[i])) i++;
    if (start === i) throw new Error(`Unexpected character "${src[i] ?? '<end>'}" at position ${i}`);
    const num = Number(src.slice(start, i));
    if (!Number.isFinite(num)) throw new Error(`Invalid number "${src.slice(start, i)}"`);
    return num;
  }

  const result = parseExpr();
  if (i !== src.length) throw new Error(`Unexpected trailing input at position ${i}`);
  if (!Number.isFinite(result)) throw new Error('Result is not a finite number');
  return result;
}

export const listWorkspaceTool: ToolDefinition<{ path?: string }, { entries: unknown[] }> = {
  id: 'workspace.list',
  name: 'List workspace files',
  description: 'Lists entries inside the AgentOS workspace. Read-only, sandboxed to the workspace root.',
  category: 'coding',
  requires: ['read'],
  inputSchema: { type: 'object', properties: { path: { type: 'string' } } },
  async run({ path: rel = '.' }, ctx) {
    const target = resolveInside(ctx.workspaceDir, rel);
    const entries = await fs.readdir(target, { withFileTypes: true });
    return {
      entries: entries
        .filter((e) => !['node_modules', '.git'].includes(e.name))
        .slice(0, 200)
        .map((e) => ({ name: e.name, type: e.isDirectory() ? 'dir' : 'file' })),
    };
  },
};

export const readWorkspaceFileTool: ToolDefinition<{ path: string }, { path: string; content: string; truncated: boolean }> = {
  id: 'workspace.read',
  name: 'Read workspace file',
  description: `Reads a UTF-8 text file (max ${MAX_READ_BYTES / 1024} KB) inside the workspace.`,
  category: 'coding',
  requires: ['read'],
  inputSchema: { type: 'object', properties: { path: { type: 'string', required: true } } },
  async run({ path: rel }, ctx) {
    const target = resolveInside(ctx.workspaceDir, rel);
    const stat = await fs.stat(target);
    if (!stat.isFile()) throw new Error('Not a file');
    const handle = await fs.open(target, 'r');
    try {
      const buf = Buffer.alloc(Math.min(stat.size, MAX_READ_BYTES));
      await handle.read(buf, 0, buf.length, 0);
      return {
        path: rel,
        content: buf.toString('utf-8'),
        truncated: stat.size > MAX_READ_BYTES,
      };
    } finally {
      await handle.close();
    }
  },
};

export const writeNoteTool: ToolDefinition<{ title: string; body: string }, { path: string; bytes: number }> = {
  id: 'memory.write',
  name: 'Save note to memory',
  description: 'Persists a note into the AgentOS data directory for later retrieval. Confined to the data folder.',
  category: 'knowledge',
  requires: ['write'],
  inputSchema: {
    type: 'object',
    properties: { title: { type: 'string', required: true }, body: { type: 'string', required: true } },
  },
  async run({ title, body }, ctx) {
    const safeTitle = title.trim().replace(/[^a-zA-Z0-9-_ ]/g, '').replace(/\s+/g, '-').slice(0, 60) || 'note';
    if (Buffer.byteLength(body, 'utf-8') > MAX_WRITE_BYTES) throw new Error('Note exceeds maximum size');
    const dir = resolveInside(ctx.dataDir, 'notes');
    await fs.mkdir(dir, { recursive: true });
    const file = resolveInside(ctx.dataDir, `notes/${safeTitle}.md`);
    await fs.writeFile(file, body, 'utf-8');
    return { path: `notes/${safeTitle}.md`, bytes: Buffer.byteLength(body, 'utf-8') };
  },
};

export const httpFetchTool: ToolDefinition<{ url: string }, { status: number; contentType: string; body: string; truncated: boolean }> = {
  id: 'http.fetch',
  name: 'Fetch URL',
  description: 'Performs a GET request and returns the response body as text. Requires the network permission.',
  category: 'research',
  requires: ['network'],
  inputSchema: { type: 'object', properties: { url: { type: 'string', required: true } } },
  async run({ url }, ctx) {
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      throw new Error('Invalid URL');
    }
    if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('Only http(s) URLs are supported');
    const res = await fetch(parsed.toString(), {
      redirect: 'follow',
      headers: { 'User-Agent': 'AgentOS/0.1 (local research tool)' },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    const raw = await res.text();
    ctx.logger.info('tool:http.fetch', `Fetched ${parsed.host}`, { status: res.status });
    return {
      status: res.status,
      contentType: res.headers.get('content-type') ?? 'unknown',
      body: raw.slice(0, FETCH_MAX_CHARS),
      truncated: raw.length > FETCH_MAX_CHARS,
    };
  },
};

export const BUILTIN_TOOLS: ToolDefinition[] = [
  systemInfoTool as unknown as ToolDefinition,
  calcTool as unknown as ToolDefinition,
  listWorkspaceTool as unknown as ToolDefinition,
  readWorkspaceFileTool as unknown as ToolDefinition,
  writeNoteTool as unknown as ToolDefinition,
  httpFetchTool as unknown as ToolDefinition,
];
