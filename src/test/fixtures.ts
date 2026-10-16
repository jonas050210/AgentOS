import type { HealthStatus, Settings } from '@shared/types';

/** Mirrors the API's settings response shape (keys are never sent back). */
export type SettingsFixture = Settings & {
  settingsPath: string;
  providers: Settings['providers'] & {
    openai: Settings['providers']['openai'] & { hasApiKey: boolean };
  };
};

/**
 * v0.2.0 defaults, mirrored from the server so UI tests exercise the same
 * contract the API validates (appearance, routing and training settings).
 */
export const platformDefaults = {
  appearance: { reduceMotion: 'auto' as const, glassIntensity: 'balanced' as const, ambientGlow: true },
  routing: { preferTrainedHeads: true, confidenceThreshold: 0.45, autoRoute: true, allowRuleFallback: true },
  training: { autoPromote: false, minImprovement: 0.01, defaultDatasetId: '', autoBenchmark: true },
};

export const healthFixture: HealthStatus = {
  status: 'degraded',
  uptimeSeconds: 12,
  version: '0.1.0',
  timestamp: new Date('2026-01-01T00:00:00.000Z').toISOString(),
  components: [
    { id: 'orchestrator', name: 'Agent orchestrator', status: 'ok' },
    { id: 'models', name: 'Model providers', status: 'warn', detail: 'No external provider' },
  ],
};

export const settingsFixture: SettingsFixture = {
  theme: 'dark',
  language: 'en',
  permissions: { allowFileRead: true, allowFileWrite: false, allowNetwork: false, allowShell: false },
  providers: {
    openai: { apiKey: '', baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o-mini', hasApiKey: false },
    ollama: { baseUrl: 'http://127.0.0.1:11434', model: 'llama3.2' },
  },
  voice: { sttEnabled: true, ttsEnabled: true, voiceName: '' },
  logging: { level: 'info' as const },
  ...platformDefaults,
  settingsPath: '/tmp/agentos/data/settings.json',
};

export const providersFixture = [
  { id: 'builtin', name: 'Built-in offline assistant', type: 'local' as const, available: true, configured: true, detail: 'fallback' },
  { id: 'ollama', name: 'Ollama (local)', type: 'local' as const, available: false, configured: true, detail: 'http://127.0.0.1:11434' },
];

export const modelsFixture = {
  models: [
    {
      id: 'builtin:offline-reasoner',
      name: 'offline-reasoner',
      providerId: 'builtin',
      capability: ['chat'],
      status: 'available' as const,
      detail: 'Rule-based local engine',
    },
    {
      id: 'ollama:llama3.2',
      name: 'llama3.2',
      providerId: 'ollama',
      capability: ['chat'],
      status: 'unavailable' as const,
      detail: 'Ollama service not reachable',
    },
  ],
  providers: [
    { id: 'builtin', name: 'Built-in offline assistant', available: true, configured: true, modelCount: 1 },
    { id: 'ollama', name: 'Ollama (local)', available: false, configured: true, modelCount: 1 },
  ],
  generatedAt: new Date('2026-01-01T00:00:00.000Z').toISOString(),
  anyExternalAvailable: false,
};

export const capabilitiesFixture = {
  capabilities: [
    {
      id: 'general.assistant',
      name: 'General assistant',
      description: 'Everyday conversation.',
      category: 'general' as const,
      status: 'partial' as const,
      backingModelIds: [],
      toolIds: ['system.info'],
    },
    {
      id: 'coding.assistant',
      name: 'Coding assistant',
      description: 'Code exploration.',
      category: 'coding' as const,
      status: 'partial' as const,
      backingModelIds: [],
      toolIds: ['workspace.list'],
    },
  ],
  hasExternalModel: false,
};

export const toolsFixture = {
  tools: [
    {
      id: 'calc.evaluate',
      name: 'Calculator',
      description: 'Evaluates arithmetic.',
      category: 'general',
      permission: 'read' as const,
      available: true,
      inputSchema: { type: 'object', properties: { expression: { type: 'string', required: true } } },
    },
    {
      id: 'http.fetch',
      name: 'Fetch URL',
      description: 'GET a URL.',
      category: 'research',
      permission: 'network' as const,
      available: false,
      inputSchema: { type: 'object', properties: { url: { type: 'string', required: true } } },
    },
  ],
};

export const activityFixture = {
  entries: [
    {
      id: 'a1',
      timestamp: new Date('2026-01-01T00:00:01.000Z').toISOString(),
      level: 'info' as const,
      source: 'server',
      message: 'AgentOS backend listening',
    },
    {
      id: 'a2',
      timestamp: new Date('2026-01-01T00:00:02.000Z').toISOString(),
      level: 'error' as const,
      source: 'orchestrator',
      message: 'Inference failed',
      meta: { error: 'socket hang up' },
    },
  ],
  level: 'info',
};

/**
 * v0.2.0 defaults, mirrored from the server so the UI tests exercise the same
 * contract the API validates (routing, appearance and training settings).
 */
