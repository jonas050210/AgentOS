// Shared types for AgentOS (frontend + backend)

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
  permission: 'read' | 'write' | 'execute';
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

export const DEFAULT_SETTINGS: Settings = {
  theme: 'dark',
  language: 'en',
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
  voice: { sttEnabled: true, ttsEnabled: true, voiceName: '' },
  logging: { level: 'info' },
};
