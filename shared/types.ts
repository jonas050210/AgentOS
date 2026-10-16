// Shared types for AgentOS (frontend + backend).
// This file is the single source of truth for the API/domain contract.

export type StatusLevel = 'ok' | 'warn' | 'error' | 'unknown';

export interface HealthStatus {
  status: 'healthy' | 'degraded' | 'down';
  uptimeSeconds: number;
  version: string;
  timestamp: string;
  components: ComponentStatus[];
}

export interface ComponentStatus {
  id: string;
  name: string;
  status: StatusLevel;
  detail?: string;
}

export interface ProviderInfo {
  id: string;
  name: string;
  type: 'local' | 'cloud' | 'remote';
  available: boolean;
  configured: boolean;
  detail?: string;
}

export interface ModelInfo {
  id: string;
  name: string;
  providerId: string;
  capability: string[];
  status: 'available' | 'unavailable' | 'unconfigured' | 'error';
  detail?: string;
}

export interface CapabilityInfo {
  id: string;
  name: string;
  description: string;
  category: 'coding' | 'research' | 'knowledge' | 'general' | 'speech';
  status: 'available' | 'partial' | 'unavailable';
  backingModelIds: string[];
  toolIds: string[];
}

export interface ToolInfo {
  id: string;
  name: string;
  description: string;
  category: string;
  permission: 'read' | 'write' | 'execute' | 'network';
  permissions?: string[];
  available: boolean;
  inputSchema: Record<string, unknown>;
}

export interface ToolExecutionResult {
  toolId: string;
  success: boolean;
  output?: unknown;
  error?: string;
  durationMs: number;
  timestamp: string;
}

export interface AgentMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  timestamp: string;
  meta?: Record<string, unknown>;
}

export interface AgentChatRequest {
  message: string;
  conversationId?: string;
  capabilityId?: string;
  /** Pin a specialist; omit to let AgentOS route automatically. */
  specialistId?: string;
}

export interface AgentChatResponse {
  conversationId: string;
  message: AgentMessage;
  trace: AgentTraceStep[];
}

export interface AgentTraceStep {
  step: string;
  detail: string;
  status: 'ok' | 'warn' | 'error' | 'skipped';
  durationMs?: number;
}

export interface Settings {
  theme: 'dark' | 'light';
  language: string;
  appearance: {
    /** 'auto' follows the OS prefers-reduced-motion setting. */
    reduceMotion: 'auto' | 'on' | 'off';
    glassIntensity: 'subtle' | 'balanced' | 'vivid';
    ambientGlow: boolean;
  };
  permissions: {
    allowFileWrite: boolean;
    allowFileRead: boolean;
    allowNetwork: boolean;
    allowShell: boolean;
  };
  providers: {
    openai: { apiKey: string; baseUrl: string; model: string };
    ollama: { baseUrl: string; model: string };
  };
  routing: {
    /** Prefer a specialist's locally trained head before escalating to a provider. */
    preferTrainedHeads: boolean;
    /** Confidence below this escalates to the model provider. */
    confidenceThreshold: number;
    /** Allow AgentOS to pick the specialist automatically. */
    autoRoute: boolean;
    /** Let the built-in rule policy answer when no trained head is confident. */
    allowRuleFallback: boolean;
  };
  training: {
    /** Promote a candidate version automatically when it beats the active one. */
    autoPromote: boolean;
    /** Minimum metric gain required for automatic promotion. */
    minImprovement: number;
    /** Default dataset used when no dataset is chosen. */
    defaultDatasetId: string;
    /** Re-run the benchmark suite automatically after a completed training run. */
    autoBenchmark: boolean;
  };
  voice: { sttEnabled: boolean; ttsEnabled: boolean; voiceName: string };
  logging: { level: 'debug' | 'info' | 'warn' | 'error' };
}

export interface ActivityEntry {
  id: string;
  timestamp: string;
  level: 'debug' | 'info' | 'warn' | 'error';
  source: string;
  message: string;
  meta?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Specialists
// ---------------------------------------------------------------------------

export type SpecialistCategory = 'coding' | 'research' | 'knowledge' | 'voice' | 'general' | 'custom';

export type SpecialistState = 'ready' | 'training' | 'untrained' | 'degraded' | 'disabled';

export interface SpecialistDefinition {
  id: string;
  name: string;
  shortName: string;
  tagline: string;
  description: string;
  category: SpecialistCategory;
  /** Accent colour used for the specialist's identity across the UI. */
  accent: string;
  glyph: string;
  capabilityIds: string[];
  toolIds: string[];
  systemPrompt: string;
  params: { temperature: number; maxTokens: number; topP: number };
  trainingMethodIds: string[];
  benchmarkSuiteIds: string[];
  outputs: string[];
  builtIn: boolean;
  createdAt: string;
}

export interface SpecialistSummary extends SpecialistDefinition {
  state: SpecialistState;
  stateDetail: string;
  activeVersion: ModelVersionSummary | null;
  versions: ModelVersionSummary[];
  enabled: boolean;
  runCount: number;
  lastRunAt: string | null;
  benchmarkScore: number | null;
  permissionsReady: boolean;
  blockedTools: string[];
}

export interface SpecialistPatch {
  name?: string;
  shortName?: string;
  tagline?: string;
  description?: string;
  accent?: string;
  glyph?: string;
  systemPrompt?: string;
  params?: Partial<SpecialistDefinition['params']>;
  toolIds?: string[];
  capabilityIds?: string[];
  enabled?: boolean;
}

export interface CreateSpecialistRequest {
  name: string;
  shortName?: string;
  tagline?: string;
  description?: string;
  category?: SpecialistCategory;
  accent?: string;
  glyph?: string;
  templateId?: string;
  toolIds?: string[];
  systemPrompt?: string;
  params?: Partial<SpecialistDefinition['params']>;
}

// ---------------------------------------------------------------------------
// Training
// ---------------------------------------------------------------------------

export type TrainingMethodId = 'task_head_sgd' | 'retrieval_index' | 'lexicon_induction' | 'llm_adapter_finetune';

export interface HyperparameterSpec {
  key: string;
  label: string;
  type: 'int' | 'float' | 'boolean' | 'enum';
  default: number | boolean | string;
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
  description: string;
  advanced?: boolean;
}

export interface TrainingMethodInfo {
  id: TrainingMethodId;
  name: string;
  kind: 'gradient' | 'statistical' | 'index' | 'external';
  runtime: 'in-process' | 'external';
  available: boolean;
  description: string;
  /** What the resulting artifact actually is — never overstated. */
  artifactKind: string;
  requirements: string[];
  appliesTo: SpecialistCategory[];
  /** Dataset kinds this method can consume; the UI uses this to pair them up. */
  datasetKinds: DatasetInfo['kind'][];
  hyperparameters: HyperparameterSpec[];
  honesty: string;
}

export interface DatasetInfo {
  id: string;
  name: string;
  specialistId: string;
  kind: 'routing' | 'expression' | 'retrieval' | 'normalization' | 'lexicon';
  provenance: 'generated' | 'user' | 'memory';
  provenanceDetail: string;
  size: number;
  labels: string[];
  description: string;
  createdAt: string;
}

export type TrainingRunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface TrainingEpoch {
  epoch: number;
  trainLoss: number;
  trainAccuracy: number;
  valLoss: number;
  valAccuracy: number;
  learningRate: number;
  durationMs: number;
}

export interface CheckpointInfo {
  id: string;
  epoch: number;
  path: string;
  valLoss: number;
  valAccuracy: number;
  sizeBytes: number;
  createdAt: string;
  best: boolean;
}

export interface DecidedPromotion {
  action: 'promoted' | 'kept_active' | 'awaiting_review' | 'no_baseline';
  reason: string;
  candidateMetric: number | null;
  activeMetric: number | null;
  delta: number | null;
  metric: string;
}

export interface TrainingRun {
  id: string;
  specialistId: string;
  specialistName: string;
  methodId: TrainingMethodId;
  methodName: string;
  datasetId: string;
  datasetName: string;
  datasetSize: number;
  hyperparameters: Record<string, number | boolean | string>;
  status: TrainingRunStatus;
  progress: number;
  startedAt: string | null;
  finishedAt: string | null;
  durationMs: number | null;
  epochs: TrainingEpoch[];
  checkpoints: CheckpointInfo[];
  metrics: Record<string, number> | null;
  /** Metric used for the promotion decision (e.g. `valAccuracy`). */
  primaryMetric: string | null;
  promotion: DecidedPromotion | null;
  versionId: string | null;
  /** Scorecard produced by the post-training benchmark, when one ran. */
  scorecardId: string | null;
  error: string | null;
  notes: string;
  triggeredBy: 'user' | 'schedule' | 'baseline';
  artifactPath: string | null;
}

export interface TrainingRunRequest {
  specialistId: string;
  methodId: TrainingMethodId;
  datasetId: string;
  hyperparameters?: Record<string, number | boolean | string>;
  autoPromote?: boolean;
  runBenchmarkAfter?: boolean;
  notes?: string;
}

export type TrainingEvent =
  | { type: 'run.created'; run: TrainingRun }
  | { type: 'run.status'; runId: string; status: TrainingRunStatus; progress: number; message: string }
  | { type: 'run.epoch'; runId: string; epoch: TrainingEpoch; progress: number }
  | { type: 'run.checkpoint'; runId: string; checkpoint: CheckpointInfo }
  | { type: 'run.finished'; run: TrainingRun };

// ---------------------------------------------------------------------------
// Model versions & benchmarks
// ---------------------------------------------------------------------------

export type VersionStatus = 'candidate' | 'active' | 'archived' | 'rejected';

export interface ModelVersionSummary {
  id: string;
  specialistId: string;
  label: string;
  methodId: TrainingMethodId;
  methodName: string;
  artifactKind: string;
  status: VersionStatus;
  createdAt: string;
  runId: string | null;
  metrics: Record<string, number>;
  primaryMetric: string | null;
  sizeBytes: number;
  datasetId: string | null;
  datasetSize: number | null;
  notes: string;
  baseModel: string;
  checksum: string;
}

export interface BenchmarkTaskResult {
  taskId: string;
  inputPreview: string;
  expected: string;
  actual: string;
  correct: boolean;
  latencyMs: number;
  error: string | null;
  /** Which mechanism produced this answer. */
  source: 'head' | 'policy' | 'escalated' | 'error';
}

export type BenchmarkSuiteId =
  | 'coding.routing'
  | 'coding.expression'
  | 'research.extraction'
  | 'research.routing'
  | 'research.retrieval'
  | 'knowledge.retrieval'
  | 'voice.normalization'
  | 'general.tool_selection';

export interface BenchmarkSuiteInfo {
  id: BenchmarkSuiteId;
  name: string;
  specialistCategory: SpecialistCategory;
  description: string;
  taskCount: number;
  metrics: string[];
  scorer: string;
  provenance: string;
}

export interface BenchmarkScorecard {
  id: string;
  suiteId: BenchmarkSuiteId;
  suiteName: string;
  specialistId: string;
  specialistName: string;
  versionId: string;
  versionLabel: string;
  /** 0..1 quality score for the suite. */
  quality: number;
  taskSuccessRate: number;
  tasksPassed: number;
  tasksTotal: number;
  latencyMsP50: number;
  latencyMsP95: number;
  latencyMsMean: number;
  reliability: number;
  failures: number;
  cpuMs: number;
  heapDeltaMb: number;
  metrics: Record<string, number>;
  taskResults: BenchmarkTaskResult[];
  createdAt: string;
  durationMs: number;
  /** How the specialist produced answers during this run. */
  engine: string;
}

export interface BenchmarkComparisonRow {
  metric: string;
  label: string;
  candidate: number;
  baseline: number;
  delta: number;
  deltaPct: number;
  direction: 'better' | 'worse' | 'same';
  /** True when the change is large enough to be considered a regression. */
  regression: boolean;
}

export interface BenchmarkComparison {
  suiteId: BenchmarkSuiteId;
  candidateId: string;
  baselineId: string | null;
  rows: BenchmarkComparisonRow[];
  verdict: 'improved' | 'regressed' | 'unchanged' | 'no_baseline';
  summary: string;
}

export interface BenchmarkRunRequest {
  suiteId: BenchmarkSuiteId;
  specialistId: string;
  versionId?: string;
  repeats?: number;
}

// ---------------------------------------------------------------------------
// Specialist playground
// ---------------------------------------------------------------------------

export interface SpecialistTestRequest {
  input: string;
  /** Persist the input plus the produced label as a new training example. */
  saveAsExample?: boolean;
}

export interface SpecialistTestResult {
  specialistId: string;
  specialistName: string;
  engine: string;
  versionId: string | null;
  versionLabel: string | null;
  /** Predicted label/route, e.g. a tool id or an answer strategy. */
  predicted: string | null;
  confidence: number | null;
  probabilities: Array<{ label: string; probability: number }>;
  escalated: boolean;
  escalationReason: string | null;
  /** Set when the escalation to a model provider threw, so callers never claim a provider answered. */
  providerError: string | null;
  /** Who produced the final answer: the specialist itself, the offline engine, or an external provider. */
  answeredBy: 'local' | 'offline-engine' | 'provider';
  toolCalls: Array<{ toolId: string; success: boolean; durationMs: number; summary: string }>;
  answer: string;
  latencyMs: number;
  retrieved: Array<{ id: string; title: string; score: number }>;
  savedExampleId: string | null;
  trace: AgentTraceStep[];
}

/** Result of running the Voice specialist's normalisation pipeline. */
export interface SpeechNormalizationResult {
  text: string;
  engine: string;
  versionId: string | null;
  /** Learned pronunciation entries the active version carries. */
  lexiconEntries: number;
  /** Which learned expansions were actually applied to this text. */
  applied: Array<{ from: string; to: string }>;
}

export interface TrainingDefaultsResponse {
  methods: TrainingMethodInfo[];
  datasets: DatasetInfo[];
  specialist: SpecialistSummary;
}

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

export const DEFAULT_SETTINGS: Settings = {
  theme: 'dark',
  language: 'en',
  appearance: {
    reduceMotion: 'auto',
    glassIntensity: 'balanced',
    ambientGlow: true,
  },
  permissions: {
    allowFileRead: true,
    allowFileWrite: false,
    allowNetwork: false,
    allowShell: false,
  },
  providers: {
    openai: { apiKey: '', baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
    ollama: { baseUrl: 'http://127.0.0.1:11434', model: 'llama3.2' },
  },
  routing: {
    preferTrainedHeads: true,
    confidenceThreshold: 0.45,
    autoRoute: true,
    allowRuleFallback: true,
  },
  training: {
    autoPromote: false,
    minImprovement: 0.01,
    defaultDatasetId: '',
    autoBenchmark: true,
  },
  voice: { sttEnabled: true, ttsEnabled: true, voiceName: '' },
  logging: { level: 'info' },
};
