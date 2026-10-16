import type {
  ActivityEntry,
  AgentChatRequest,
  AgentChatResponse,
  BenchmarkComparison,
  BenchmarkRunRequest,
  BenchmarkScorecard,
  BenchmarkSuiteInfo,
  CapabilityInfo,
  CreateSpecialistRequest,
  DatasetInfo,
  HealthStatus,
  ModelInfo,
  ModelVersionSummary,
  ProviderInfo,
  Settings,
  SpecialistPatch,
  SpecialistSummary,
  SpecialistTestRequest,
  SpecialistTestResult,
  SpeechNormalizationResult,
  ToolInfo,
  ToolExecutionResult,
  TrainingMethodInfo,
  TrainingRun,
  TrainingRunRequest,
} from '@shared/types';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = 'UNKNOWN',
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

const TIMEOUT_MS = 30_000;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (e) {
    const detail = e instanceof Error ? e.message : String(e);
    throw new ApiError(`Cannot reach the AgentOS backend (${detail}). Is the server running?`, 0, 'NETWORK_ERROR');
  }
  if (!res.ok) {
    let message = `Request failed with status ${res.status}`;
    let code = 'HTTP_ERROR';
    try {
      const body = (await res.json()) as { error?: string; code?: string };
      if (body.error) message = body.error;
      if (body.code) code = body.code;
    } catch {
      /* response had no JSON body */
    }
    throw new ApiError(message, res.status, code);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface SettingsResponse extends Settings {
  providers: Settings['providers'] & { openai: Settings['providers']['openai'] & { hasApiKey?: boolean } };
  settingsPath: string;
}

export interface PlatformOverview {
  specialists: SpecialistSummary[];
  runs: TrainingRun[];
  counts: { running: number; queued: number; completed: number; failed: number; cancelled: number; total: number };
  scorecards: BenchmarkScorecard[];
  versions: ModelVersionSummary[];
  recentComparisons: Array<{
    scorecardId: string;
    specialistId: string;
    suiteName: string;
    verdict: 'improved' | 'regressed' | 'unchanged' | 'no_baseline';
    quality: number;
    createdAt: string;
  }>;
  methods: TrainingMethodInfo[];
  datasets: DatasetInfo[];
  suites: BenchmarkSuiteInfo[];
  /** How many in-process training methods are usable right now. */
  trainingAvailable: number;
}

export const api = {
  health: () => request<HealthStatus>('/health'),
  providers: () => request<ProviderInfo[]>('/providers'),
  models: () =>
    request<{
      models: ModelInfo[];
      providers: Array<{ id: string; name: string; available: boolean; configured: boolean; detail?: string; modelCount: number }>;
      generatedAt: string;
      anyExternalAvailable: boolean;
    }>('/models'),
  capabilities: () => request<{ capabilities: CapabilityInfo[]; hasExternalModel: boolean }>('/capabilities'),
  tools: () => request<{ tools: ToolInfo[] }>('/tools'),
  executeTool: (id: string, input: Record<string, unknown>) =>
    request<ToolExecutionResult>(`/tools/${encodeURIComponent(id)}/execute`, {
      method: 'POST',
      body: JSON.stringify({ input }),
    }),
  chat: (payload: AgentChatRequest) => request<AgentChatResponse>('/chat', { method: 'POST', body: JSON.stringify(payload) }),
  settings: () => request<SettingsResponse>('/settings'),
  updateSettings: (patch: Partial<Settings>) => request<SettingsResponse>('/settings', { method: 'PUT', body: JSON.stringify(patch) }),
  resetSettings: () => request<SettingsResponse>('/settings/reset', { method: 'POST' }),
  activity: (limit = 100) => request<{ entries: ActivityEntry[]; level: string }>(`/activity?limit=${limit}`),
  memory: (q?: string) =>
    request<{ records: Array<{ id: string; title: string; content: string; tags: string[]; updatedAt: string }>; count: number }>(
      `/memory${q ? `?q=${encodeURIComponent(q)}` : ''}`,
    ),
  speechEngines: () =>
    request<{
      engines: Array<{ id: string; name: string; kind: 'stt' | 'tts'; available: boolean; location: 'client' | 'server'; detail: string }>;
      settings: Settings['voice'];
    }>('/speech/engines'),
  deleteMemory: (id: string) => request<{ removed: boolean }>(`/memory/${id}`, { method: 'DELETE' }),

  // ---------- platform: specialists ----------
  overview: () => request<PlatformOverview>('/platform/overview'),
  specialists: () => request<{ specialists: SpecialistSummary[] }>('/specialists'),
  specialist: (id: string) => request<SpecialistSummary>(`/specialists/${encodeURIComponent(id)}`),
  createSpecialist: (payload: CreateSpecialistRequest) =>
    request<SpecialistSummary>('/specialists', { method: 'POST', body: JSON.stringify(payload) }),
  updateSpecialist: (id: string, patch: SpecialistPatch) =>
    request<SpecialistSummary>(`/specialists/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteSpecialist: (id: string) => request<{ removed: boolean }>(`/specialists/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  /** Runs one input through a specialist and returns the full decision trace. */
  testSpecialist: (id: string, payload: SpecialistTestRequest) =>
    request<SpecialistTestResult>(`/specialists/${encodeURIComponent(id)}/test`, { method: 'POST', body: JSON.stringify(payload) }),
  examples: (id: string) =>
    request<{ examples: Array<{ id: string; text: string; label: string; createdAt: string; source: string }> }>(
      `/specialists/${encodeURIComponent(id)}/examples`,
    ),
  addExample: (id: string, payload: { text: string; label: string }) =>
    request<{ id: string; text: string; label: string }>(`/specialists/${encodeURIComponent(id)}/examples`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  removeExample: (id: string, exampleId: string) =>
    request<{ removed: boolean }>(`/specialists/${encodeURIComponent(id)}/examples/${encodeURIComponent(exampleId)}`, { method: 'DELETE' }),
  normalize: (id: string, text: string) =>
    request<SpeechNormalizationResult>(`/specialists/${encodeURIComponent(id)}/normalize`, { method: 'POST', body: JSON.stringify({ text }) }),

  // ---------- platform: training ----------
  trainingMethods: () => request<{ methods: TrainingMethodInfo[] }>('/training/methods'),
  datasets: () => request<{ datasets: DatasetInfo[] }>('/training/datasets'),
  runs: (params: { specialistId?: string; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.specialistId) query.set('specialistId', params.specialistId);
    query.set('limit', String(params.limit ?? 20));
    return request<{ runs: TrainingRun[] }>(`/training/runs?${query.toString()}`);
  },
  run: (id: string) => request<TrainingRun>(`/training/runs/${encodeURIComponent(id)}`),
  startRun: (payload: TrainingRunRequest) => request<TrainingRun>('/training/runs', { method: 'POST', body: JSON.stringify(payload) }),
  cancelRun: (id: string) => request<{ cancelled: boolean }>(`/training/runs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  deleteRun: (id: string) => request<{ removed: boolean }>(`/training/runs/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // ---------- platform: versions ----------
  versions: (specialistId?: string) =>
    request<{ versions: ModelVersionSummary[]; activeVersionIds: Record<string, string | null> }>(
      `/models/versions${specialistId ? `?specialistId=${encodeURIComponent(specialistId)}` : ''}`,
    ),
  version: (id: string) =>
    request<{ summary: ModelVersionSummary; artifact: Record<string, unknown> | null; scorecards: BenchmarkScorecard[] }>(
      `/models/versions/${encodeURIComponent(id)}`,
    ),
  promoteVersion: (id: string, options: { verify?: boolean; force?: boolean } = {}) =>
    request<{ version: ModelVersionSummary; verification: { ok: boolean; message: string } | null }>(
      `/models/versions/${encodeURIComponent(id)}/promote`,
      { method: 'POST', body: JSON.stringify(options) },
    ),
  rejectVersion: (id: string) =>
    request<{ version: ModelVersionSummary }>(`/models/versions/${encodeURIComponent(id)}/reject`, { method: 'POST' }),
  deleteVersion: (id: string) => request<{ removed: boolean }>(`/models/versions/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // ---------- platform: benchmarks ----------
  suites: () => request<{ suites: BenchmarkSuiteInfo[] }>('/benchmarks/suites'),
  scorecards: (params: { specialistId?: string; suiteId?: string; versionId?: string; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.specialistId) query.set('specialistId', params.specialistId);
    if (params.suiteId) query.set('suiteId', params.suiteId);
    if (params.versionId) query.set('versionId', params.versionId);
    query.set('limit', String(params.limit ?? 25));
    return request<{ scorecards: BenchmarkScorecard[] }>(`/benchmarks/scorecards?${query.toString()}`);
  },
  scorecard: (id: string) => request<BenchmarkScorecard>(`/benchmarks/scorecards/${encodeURIComponent(id)}`),
  runBenchmark: (payload: BenchmarkRunRequest) => request<BenchmarkScorecard>('/benchmarks/run', { method: 'POST', body: JSON.stringify(payload) }),
  compare: (candidateId: string, baselineId?: string) =>
    request<BenchmarkComparison>(
      `/benchmarks/compare?candidateId=${encodeURIComponent(candidateId)}${baselineId ? `&baselineId=${encodeURIComponent(baselineId)}` : ''}`,
    ),
};

export function activityStreamUrl() {
  return '/api/activity/stream';
}

export function trainingStreamUrl() {
  return '/api/training/stream';
}
