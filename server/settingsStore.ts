import fs from 'node:fs';
import path from 'node:path';
import { Settings, DEFAULT_SETTINGS } from '../shared/types.js';
import { logger } from './logger.js';

export function defaultDataDir(): string {
  return process.env.AGENTOS_DATA_DIR || path.join(process.cwd(), 'data');
}

function validate(raw: unknown): Settings {
  // Defensive validation: any value coming from disk is treated as untrusted,
  // so a hand-edited or corrupted file can never put the app into a bad state.
  const d = DEFAULT_SETTINGS;
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const bool = (value: unknown, fallback: boolean) => (typeof value === 'boolean' ? value : fallback);
  const str = (value: unknown, fallback: string, max = 400) =>
    typeof value === 'string' && value.length <= max ? value : fallback;
  const perms = (r.permissions && typeof r.permissions === 'object' ? r.permissions : {}) as Record<string, unknown>;
  const prov = (r.providers && typeof r.providers === 'object' ? r.providers : {}) as Record<string, unknown>;
  const openai = (prov.openai && typeof prov.openai === 'object' ? prov.openai : {}) as Record<string, unknown>;
  const ollama = (prov.ollama && typeof prov.ollama === 'object' ? prov.ollama : {}) as Record<string, unknown>;
  const voice = (r.voice && typeof r.voice === 'object' ? r.voice : {}) as Record<string, unknown>;
  const logging = (r.logging && typeof r.logging === 'object' ? r.logging : {}) as Record<string, unknown>;
  const levels = ['debug', 'info', 'warn', 'error'] as const;

  return {
    theme: r.theme === 'light' ? 'light' : 'dark',
    language: str(r.language, d.language, 20),
    permissions: {
      allowFileRead: bool(perms.allowFileRead, d.permissions.allowFileRead),
      allowFileWrite: bool(perms.allowFileWrite, d.permissions.allowFileWrite),
      allowNetwork: bool(perms.allowNetwork, d.permissions.allowNetwork),
      allowShell: bool(perms.allowShell, d.permissions.allowShell),
    },
    providers: {
      openai: {
        apiKey: str(openai.apiKey, '', 1000),
        baseUrl: str(openai.baseUrl, d.providers.openai.baseUrl),
        model: str(openai.model, d.providers.openai.model, 120),
      },
      ollama: {
        baseUrl: str(ollama.baseUrl, d.providers.ollama.baseUrl),
        model: str(ollama.model, d.providers.ollama.model, 120),
      },
    },
    voice: {
      sttEnabled: bool(voice.sttEnabled, d.voice.sttEnabled),
      ttsEnabled: bool(voice.ttsEnabled, d.voice.ttsEnabled),
      voiceName: str(voice.voiceName, d.voice.voiceName, 120),
    },
    logging: {
      level: levels.includes(logging.level as (typeof levels)[number])
        ? (logging.level as Settings['logging']['level'])
        : d.logging.level,
    },
  };
}

export class SettingsStore {
  private cache: Settings;
  readonly dir: string;
  private file: string;

  constructor(dir: string = defaultDataDir()) {
    this.dir = dir;
    this.file = path.join(dir, 'settings.json');
    this.ensureDir();
    this.cache = this.load();
    logger.setLevel(this.cache.logging.level);
  }

  private ensureDir() {
    if (!fs.existsSync(this.dir)) fs.mkdirSync(this.dir, { recursive: true });
  }

  private load(): Settings {
    try {
      if (!fs.existsSync(this.file)) {
        this.write(DEFAULT_SETTINGS);
        return { ...DEFAULT_SETTINGS };
      }
      const raw = fs.readFileSync(this.file, 'utf-8');
      const parsed = JSON.parse(raw);
      const s = validate(parsed);
      logger.info('settings', 'Loaded settings from disk');
      return s;
    } catch (e) {
      logger.warn('settings', 'Failed to load settings, using defaults', { error: String(e) });
      return { ...DEFAULT_SETTINGS };
    }
  }

  private write(s: Settings) {
    this.ensureDir();
    const tmp = this.file + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(s, null, 2), 'utf-8');
    fs.renameSync(tmp, this.file);
  }

  get(): Settings {
    return this.cache;
  }

  update(patch: Partial<Settings>): Settings {
    const next: Settings = {
      ...this.cache,
      ...patch,
      permissions: { ...this.cache.permissions, ...(patch.permissions || {}) },
      providers: {
        openai: { ...this.cache.providers.openai, ...(patch.providers?.openai || {}) },
        ollama: { ...this.cache.providers.ollama, ...(patch.providers?.ollama || {}) },
      },
      voice: { ...this.cache.voice, ...(patch.voice || {}) },
      logging: { ...this.cache.logging, ...(patch.logging || {}) },
    };
    // Run the merged result through the same validator used for disk reads so
    // invalid API input can never reach the persisted file.
    this.cache = validate(next);
    this.write(this.cache);
    logger.setLevel(this.cache.logging.level);
    logger.info('settings', 'Settings updated');
    return this.cache;
  }

  reset(): Settings {
    this.cache = { ...DEFAULT_SETTINGS };
    this.write(this.cache);
    logger.setLevel(this.cache.logging.level);
    return this.cache;
  }

  filePath() {
    return this.file;
  }
}

export const settingsStore = new SettingsStore();
