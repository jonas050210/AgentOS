import type {
  ActivityEntry,
  AgentChatRequest,
  AgentChatResponse,
  CapabilityInfo,
  HealthStatus,
  ModelInfo,
  ProviderInfo,
  Settings,
  ToolInfo,
  ToolExecutionResult,
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
  chat: (payload: AgentChatRequest) =>
    request<AgentChatResponse>('/chat', { method: 'POST', body: JSON.stringify(payload) }),
  settings: () => request<SettingsResponse>('/settings'),
  updateSettings: (patch: Partial<Settings>) =>
    request<SettingsResponse>('/settings', { method: 'PUT', body: JSON.stringify(patch) }),
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
};

export function activityStreamUrl() {
  return '/api/activity/stream';
}
