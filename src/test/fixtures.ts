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
  version: '0.2.0',
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

// ---------------------------------------------------------------------------
// Platform fixtures: specialists, training, benchmarks
// ---------------------------------------------------------------------------

export const specialistFixture = {
  id: 'coding-ai',
  name: 'Coding AI',
  shortName: 'Coding',
  tagline: 'Reads the workspace and computes',
  description: 'Decides between reading files, listing directories and arithmetic.',
  category: 'coding' as const,
  accent: '#6ea8ff',
  glyph: 'CD',
  capabilityIds: ['coding.assistant'],
  toolIds: ['workspace.read', 'calc.evaluate'],
  systemPrompt: 'You are a coding specialist inside AgentOS.',
  params: { temperature: 0.2, maxTokens: 1024, topP: 0.9 },
  trainingMethodIds: ['task_head_sgd'],
  benchmarkSuiteIds: ['coding.routing'],
  outputs: ['Tool-backed answers'],
  builtIn: true,
  createdAt: '2026-01-01T00:00:00.000Z',
  state: 'ready' as const,
  stateDetail: 'Trained locally — v1 answers tool decisions.',
  activeVersion: {
    id: 'coding-ai@v1',
    specialistId: 'coding-ai',
    label: 'v1',
    methodId: 'task_head_sgd' as const,
    methodName: 'Task head · softmax + SGD',
    artifactKind: 'task_head_sgd',
    status: 'active' as const,
    createdAt: '2026-01-01T00:10:00.000Z',
    runId: 'run_a',
    metrics: { holdoutAccuracy: 0.7889 },
    primaryMetric: 'holdoutAccuracy',
    sizeBytes: 68000,
    datasetId: 'coding.tool_routing',
    datasetSize: 146,
    notes: 'Promoted after benchmark evidence.',
    baseModel: 'hashed-ngram-features',
    checksum: 'abc123def456',
  },
  versions: [],
  enabled: true,
  runCount: 2,
  lastRunAt: '2026-01-01T00:10:00.000Z',
  benchmarkScore: 0.8,
  permissionsReady: true,
  blockedTools: [],
};

export const untrainedSpecialistFixture = {
  ...specialistFixture,
  id: 'research-ai',
  name: 'Research AI',
  shortName: 'Research',
  tagline: 'Fetches sources, cites what it read',
  category: 'research' as const,
  accent: '#8f7bff',
  glyph: 'RE',
  toolIds: ['http.fetch', 'memory.search'],
  trainingMethodIds: ['task_head_sgd', 'llm_adapter_finetune'],
  benchmarkSuiteIds: ['research.routing'],
  state: 'untrained' as const,
  stateDetail: 'No trained model version yet — rule policy and escalation only.',
  activeVersion: null,
  versions: [],
  runCount: 0,
  lastRunAt: null,
  benchmarkScore: null,
};

export const trainingMethodFixture = {
  id: 'task_head_sgd' as const,
  name: 'Task head · softmax + SGD',
  kind: 'gradient' as const,
  runtime: 'in-process' as const,
  available: true,
  description: 'Trains a softmax classifier over hashed n-gram features.',
  artifactKind: 'task_head_sgd · softmax weights',
  requirements: ['None — runs in-process'],
  appliesTo: ['coding' as const, 'research' as const],
  datasetKinds: ['routing' as const],
  hyperparameters: [
    { key: 'epochs', label: 'Epochs', type: 'int' as const, default: 120, min: 1, max: 400, description: 'Passes over the training split.' },
    { key: 'learningRate', label: 'Learning rate', type: 'float' as const, default: 0.35, min: 0.001, max: 5, description: 'Initial step size.' },
  ],
  honesty: 'A softmax head over local features — not a fine-tuned base model.',
};

export const externalMethodFixture = {
  ...trainingMethodFixture,
  id: 'llm_adapter_finetune' as const,
  name: 'Adapter fine-tuning (LoRA/PEFT)',
  kind: 'external' as const,
  runtime: 'external' as const,
  available: false,
  appliesTo: ['research' as const],
  datasetKinds: ['routing' as const],
  requirements: ['Python 3.11 with torch, transformers and peft', 'A configured model provider'],
  honesty: 'Needs an external runtime; reported unavailable until it exists.',
};

export const datasetFixture = {
  id: 'coding.tool_routing',
  name: 'Coding · tool routing',
  specialistId: 'coding-ai',
  kind: 'routing' as const,
  provenance: 'generated' as const,
  provenanceDetail: 'Curated seed corpus shipped with AgentOS.',
  size: 146,
  labels: ['calc.evaluate', 'workspace.read'],
  description: 'Instruction → tool decisions for the Coding specialist.',
  createdAt: '2026-01-01T00:00:00.000Z',
};

export const suiteFixture = {
  id: 'coding.routing' as const,
  name: 'Coding · decision routing',
  specialistCategory: 'coding' as const,
  description: 'Held-out instructions; the specialist must pick the tool that resolves each one.',
  taskCount: 90,
  metrics: ['accuracy', 'latencyMsP50'],
  scorer: 'exact label match against the curated expectation',
  provenance: 'Held-out split of the curated coding routing pool',
};

export const scorecardFixture = {
  id: 'card_a',
  suiteId: 'coding.routing' as const,
  suiteName: 'Coding · decision routing',
  specialistId: 'coding-ai',
  specialistName: 'Coding AI',
  versionId: 'coding-ai@v1',
  versionLabel: 'v1',
  quality: 0.8,
  taskSuccessRate: 0.8,
  tasksPassed: 72,
  tasksTotal: 90,
  latencyMsP50: 1.04,
  latencyMsP95: 2.1,
  latencyMsMean: 1.2,
  reliability: 1,
  failures: 0,
  cpuMs: 48.2,
  heapDeltaMb: -0.8,
  metrics: { headAnswered: 75, policyAnswered: 6, escalated: 9, accuracy: 0.8 },
  taskResults: [
    { taskId: 'coding.routing#1', inputPreview: 'multiply 14 by 7', expected: 'calc.evaluate', actual: 'calc.evaluate', correct: true, latencyMs: 0.9, error: null, source: 'head' as const },
    { taskId: 'coding.routing#2', inputPreview: 'open src/index.css', expected: 'workspace.read', actual: 'workspace.list', correct: false, latencyMs: 0.7, error: null, source: 'head' as const },
  ],
  createdAt: '2026-01-01T00:12:00.000Z',
  durationMs: 210,
  engine: 'Task head · softmax + SGD · v1 (task_head_sgd) · calibrated threshold 0.35',
};

export const baselineScorecardFixture = {
  ...scorecardFixture,
  id: 'card_baseline',
  versionId: 'coding-ai@untrained',
  versionLabel: 'untrained baseline',
  quality: 0.5222,
  taskSuccessRate: 0.5222,
  tasksPassed: 47,
  engine: 'untrained baseline — no local artefact, requests escalate',
  createdAt: '2026-01-01T00:05:00.000Z',
};

export const runFixture = {
  id: 'run_a',
  specialistId: 'coding-ai',
  specialistName: 'Coding AI',
  methodId: 'task_head_sgd' as const,
  methodName: 'Task head · softmax + SGD',
  datasetId: 'coding.tool_routing',
  datasetName: 'Coding · tool routing',
  datasetSize: 146,
  hyperparameters: { epochs: 120, learningRate: 0.35 },
  status: 'completed' as const,
  progress: 1,
  startedAt: '2026-01-01T00:00:10.000Z',
  finishedAt: '2026-01-01T00:10:00.000Z',
  durationMs: 9000,
  epochs: [
    { epoch: 1, trainLoss: 1.2, trainAccuracy: 0.4, valLoss: 1.1, valAccuracy: 0.5, learningRate: 0.3, durationMs: 2 },
    { epoch: 2, trainLoss: 0.9, trainAccuracy: 0.7, valLoss: 0.8, valAccuracy: 0.7, learningRate: 0.25, durationMs: 2 },
    { epoch: 3, trainLoss: 0.6, trainAccuracy: 0.9, valLoss: 0.6, valAccuracy: 0.85, learningRate: 0.2, durationMs: 2 },
  ],
  checkpoints: [
    { id: 'cp_1', epoch: 30, path: '/data/training/run_a/checkpoints/ep30.json', valLoss: 0.7, valAccuracy: 0.78, sizeBytes: 68000, createdAt: '2026-01-01T00:05:00.000Z', best: true },
  ],
  metrics: { holdoutAccuracy: 0.7889, calibratedThreshold: 0.35, benchmarkQuality: 0.8 },
  primaryMetric: 'benchmarkQuality',
  promotion: {
    action: 'promoted' as const,
    reason: 'Coding · decision routing: quality 80.0% vs untrained baseline 52.2% (+27.8 points, gate 1.0).',
    candidateMetric: 0.8,
    activeMetric: 0.5222,
    delta: 0.2778,
    metric: 'benchmarkQuality',
  },
  versionId: 'coding-ai@v1',
  scorecardId: 'card_a',
  error: null,
  notes: '',
  triggeredBy: 'user' as const,
  artifactPath: '/data/models/coding-ai/abc123.json',
};

export const overviewFixture = {
  specialists: [specialistFixture, untrainedSpecialistFixture],
  runs: [runFixture],
  counts: { running: 0, queued: 0, completed: 1, failed: 0, cancelled: 0, total: 1 },
  scorecards: [scorecardFixture, baselineScorecardFixture],
  versions: [specialistFixture.activeVersion],
  recentComparisons: [
    { scorecardId: 'card_a', specialistId: 'coding-ai', suiteName: 'Coding · decision routing', verdict: 'improved' as const, baselineLabel: 'untrained baseline', quality: 0.8, createdAt: '2026-01-01T00:12:00.000Z' },
  ],
  methods: [trainingMethodFixture, externalMethodFixture],
  datasets: [datasetFixture],
  suites: [suiteFixture],
  trainingAvailable: 3,
};

export const comparisonFixture = {
  suiteId: 'coding.routing' as const,
  candidateId: 'card_a',
  baselineId: 'card_baseline',
  rows: [
    { metric: 'quality', label: 'Quality score', candidate: 0.8, baseline: 0.5222, delta: 0.2778, deltaPct: 53.2, direction: 'better' as const, regression: false },
    { metric: 'latencyMsP50', label: 'Latency p50 (ms)', candidate: 1.04, baseline: 0.0152, delta: 1.02, deltaPct: 6740, direction: 'worse' as const, regression: true },
  ],
  verdict: 'improved' as const,
  tradeoffs: ['Latency p50 (ms)'],
  summary: 'v1 improves on untrained baseline: quality +27.8 points, at the cost of Latency p50 (ms).',
};

export const testResultFixture = {
  specialistId: 'coding-ai',
  specialistName: 'Coding AI',
  engine: 'trained task head (v1) · softmax SGD · 1024 hashed features · 7 labels',
  versionId: 'coding-ai@v1',
  versionLabel: 'v1',
  predicted: 'calc.evaluate',
  confidence: 0.9473,
  probabilities: [
    { label: 'calc.evaluate', probability: 0.9473 },
    { label: 'workspace.read', probability: 0.03 },
  ],
  escalated: false,
  escalationReason: null,
  providerError: null,
  answeredBy: 'local' as const,
  toolCalls: [{ toolId: 'calc.evaluate', success: true, durationMs: 0.4, summary: 'ok' }],
  answer: 'Result: 128*4 = 512',
  latencyMs: 2.4,
  retrieved: [],
  savedExampleId: 'ex_1',
  trace: [
    { step: 'decide', detail: 'Trained task head chose calc.evaluate', status: 'ok' as const },
    { step: 'tool:calc.evaluate', detail: 'Executed calc.evaluate', status: 'ok' as const },
  ],
};
