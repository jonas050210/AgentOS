// @vitest-environment node
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

let dataDir: string;
let workspaceDir: string;
let settingsStore: typeof import('../settingsStore.js').settingsStore;
let toolRegistry: typeof import('./registry.js').toolRegistry;

beforeEach(async () => {
  dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'agentos-data-'));
  workspaceDir = fs.mkdtempSync(path.join(os.tmpdir(), 'agentos-ws-'));
  process.env.AGENTOS_DATA_DIR = dataDir;
  process.env.AGENTOS_WORKSPACE_DIR = workspaceDir;
  vi.resetModules();
  ({ settingsStore } = await import('../settingsStore.js'));
  ({ toolRegistry } = await import('./registry.js'));
});

afterEach(() => {
  fs.rmSync(dataDir, { recursive: true, force: true });
  fs.rmSync(workspaceDir, { recursive: true, force: true });
});

describe('ToolRegistry', () => {
  it('executes a permission-free tool and returns structured output', async () => {
    const outcome = await toolRegistry.execute('calc.evaluate', { expression: '12 * (4 + 3)' });
    expect(outcome.success).toBe(true);
    expect(outcome.output).toEqual({ result: 84 });
    expect(outcome.durationMs).toBeGreaterThanOrEqual(0);
  });

  it('reports arithmetic errors instead of throwing', async () => {
    const outcome = await toolRegistry.execute('calc.evaluate', { expression: '1 / 0' });
    expect(outcome.success).toBe(false);
    expect(outcome.error).toMatch(/Division by zero/);
  });

  it('rejects unknown tools with an error outcome', async () => {
    const outcome = await toolRegistry.execute('does.not.exist', {});
    expect(outcome.success).toBe(false);
    expect(outcome.error).toMatch(/Unknown tool/);
  });

  it('blocks tools whose permission is disabled', async () => {
    expect(settingsStore.get().permissions.allowNetwork).toBe(false);
    const outcome = await toolRegistry.execute('http.fetch', { url: 'https://example.com' });
    expect(outcome.success).toBe(false);
    expect(outcome.error).toMatch(/Permission "network"/);
    expect(outcome.output).toEqual({ code: 'PERMISSION_DENIED' });
  });

  it('validates required structured input before running', async () => {
    const outcome = await toolRegistry.execute('calc.evaluate', {});
    expect(outcome.success).toBe(false);
    expect(outcome.error).toMatch(/expression is required/);
  });

  it('marks tools unavailable while the permission is off, then available once granted', () => {
    settingsStore.update({ permissions: { ...settingsStore.get().permissions, allowFileRead: false } });
    const before = toolRegistry.describe(settingsStore.get()).find((t) => t.id === 'workspace.read')!;
    expect(before.available).toBe(false);

    settingsStore.update({ permissions: { ...settingsStore.get().permissions, allowFileRead: true } });
    const after = toolRegistry.describe(settingsStore.get()).find((t) => t.id === 'workspace.read')!;
    expect(after.available).toBe(true);
  });

  it('reads a workspace file once read permission is granted', async () => {
    fs.writeFileSync(path.join(workspaceDir, 'hello.txt'), 'hello agentos');
    settingsStore.update({ permissions: { ...settingsStore.get().permissions, allowFileRead: true } });
    const outcome = await toolRegistry.execute('workspace.read', { path: 'hello.txt' });
    expect(outcome.success).toBe(true);
    expect((outcome.output as { content: string }).content).toBe('hello agentos');
  });

  it('refuses path traversal outside the workspace', async () => {
    settingsStore.update({ permissions: { ...settingsStore.get().permissions, allowFileRead: true } });
    const outcome = await toolRegistry.execute('workspace.read', { path: '../../etc/passwd' });
    expect(outcome.success).toBe(false);
    expect(outcome.error).toMatch(/outside the permitted directory/);
  });

  it('refuses absolute paths', async () => {
    settingsStore.update({ permissions: { ...settingsStore.get().permissions, allowFileRead: true } });
    const outcome = await toolRegistry.execute('workspace.read', { path: '/etc/passwd' });
    expect(outcome.success).toBe(false);
    expect(outcome.error).toMatch(/outside the permitted directory/);
  });

  it('writes notes only into the data directory and only with write permission', async () => {
    const blocked = await toolRegistry.execute('memory.write', { title: 'n', body: 'b' });
    expect(blocked.success).toBe(false);

    settingsStore.update({ permissions: { ...settingsStore.get().permissions, allowFileWrite: true } });
    const outcome = await toolRegistry.execute('memory.write', { title: 'my note', body: 'content' });
    expect(outcome.success).toBe(true);
    expect(fs.existsSync(path.join(dataDir, 'notes', 'my-note.md'))).toBe(true);
    expect(fs.readFileSync(path.join(dataDir, 'notes', 'my-note.md'), 'utf-8')).toBe('content');
  });

  it('sanitises note titles that try to escape the notes directory', async () => {
    settingsStore.update({ permissions: { ...settingsStore.get().permissions, allowFileWrite: true } });
    const outcome = await toolRegistry.execute('memory.write', { title: '../../evil', body: 'x' });
    expect(outcome.success).toBe(true);
    expect(fs.existsSync(path.join(dataDir, 'notes', 'evil.md'))).toBe(true);
    expect(fs.existsSync(path.join(dataDir, '..', 'evil.md'))).toBe(false);
  });

  it('executes an allowed network tool and surfaces HTTP failures', async () => {
    settingsStore.update({ permissions: { ...settingsStore.get().permissions, allowNetwork: true } });
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('hello world', { status: 200, headers: { 'content-type': 'text/plain' } }),
    );
    const outcome = await toolRegistry.execute('http.fetch', { url: 'https://example.com' });
    expect(outcome.success).toBe(true);
    expect((outcome.output as { status: number }).status).toBe(200);

    fetchSpy.mockRejectedValue(new Error('network unreachable'));
    const failed = await toolRegistry.execute('http.fetch', { url: 'https://example.com' });
    expect(failed.success).toBe(false);
    expect(failed.error).toMatch(/network unreachable/);

    fetchSpy.mockRestore();
    const invalid = await toolRegistry.execute('http.fetch', { url: 'ftp://example.com' });
    expect(invalid.success).toBe(false);
    expect(invalid.error).toMatch(/http\(s\)/);
  });

  it('reports memory tool round-trips', async () => {
    const saved = await toolRegistry.execute('memory.save', { title: 'Project', content: 'Uses Postgres 16', tags: ['db'] });
    expect(saved.success).toBe(true);
    const found = await toolRegistry.execute('memory.search', { query: 'postgres' });
    expect(found.success).toBe(true);
    expect((found.output as { results: unknown[] }).results.length).toBe(1);
  });
});
