// @vitest-environment node
import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { SettingsStore } from './settingsStore.js';
import { DEFAULT_SETTINGS } from '../shared/types.js';

let dir: string;

beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agentos-settings-'));
});

afterEach(() => {
  fs.rmSync(dir, { recursive: true, force: true });
});

describe('SettingsStore', () => {
  it('creates a settings file with defaults on first run', () => {
    const store = new SettingsStore(dir);
    expect(fs.existsSync(path.join(dir, 'settings.json'))).toBe(true);
    expect(store.get().theme).toBe('dark');
    expect(store.get().permissions.allowShell).toBe(false);
  });

  it('persists updates so a new instance (restart) reads them back', () => {
    const store = new SettingsStore(dir);
    store.update({
      theme: 'light',
      permissions: { ...DEFAULT_SETTINGS.permissions, allowNetwork: true },
      providers: { openai: { apiKey: 'sk-test-secret', baseUrl: 'https://example.test/v1', model: 'gpt-x' }, ollama: DEFAULT_SETTINGS.providers.ollama },
    });

    const reopened = new SettingsStore(dir);
    expect(reopened.get().theme).toBe('light');
    expect(reopened.get().permissions.allowNetwork).toBe(true);
    expect(reopened.get().providers.openai.apiKey).toBe('sk-test-secret');
    expect(reopened.get().providers.openai.baseUrl).toBe('https://example.test/v1');
  });

  it('merges partial updates without dropping unrelated values', () => {
    const store = new SettingsStore(dir);
    store.update({ permissions: { ...DEFAULT_SETTINGS.permissions, allowNetwork: true } });
    store.update({ logging: { level: 'debug' } });
    expect(store.get().permissions.allowNetwork).toBe(true);
    expect(store.get().logging.level).toBe('debug');
    expect(store.get().voice.ttsEnabled).toBe(true);
  });

  it('falls back to defaults when the file is corrupt instead of crashing', () => {
    fs.writeFileSync(path.join(dir, 'settings.json'), '{not json');
    const store = new SettingsStore(dir);
    expect(store.get().theme).toBe('dark');
    expect(store.get().permissions.allowFileWrite).toBe(false);
  });

  it('sanitises invalid values from disk', () => {
    fs.writeFileSync(
      path.join(dir, 'settings.json'),
      JSON.stringify({ theme: 'neon', permissions: { allowShell: 'yes' }, logging: { level: 'trace' } }),
    );
    const store = new SettingsStore(dir);
    expect(store.get().theme).toBe('dark');
    // Unknown enum values are not trusted; the merge keeps the raw value but the
    // type guard maps theme back to a supported value.
    expect(['debug', 'info', 'warn', 'error']).toContain(store.get().logging.level);
  });

  it('resets to defaults', () => {
    const store = new SettingsStore(dir);
    store.update({ theme: 'light' });
    store.reset();
    expect(store.get().theme).toBe('dark');
    const reopened = new SettingsStore(dir);
    expect(reopened.get().theme).toBe('dark');
  });
});
