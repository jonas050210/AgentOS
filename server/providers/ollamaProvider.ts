import { ModelInfo, ProviderInfo } from '../../shared/types.js';
import { GenerateRequest, GenerateResponse, ModelProvider } from './types.js';
import { logger } from '../logger.js';

/**
 * Adapter for a locally running Ollama instance.
 * Availability is probed at runtime; nothing is assumed to be installed.
 */
export class OllamaProvider implements ModelProvider {
  readonly id = 'ollama';
  readonly name = 'Ollama (local)';
  readonly type = 'local' as const;

  constructor(
    private getBaseUrl: () => string,
    private getDefaultModel: () => string,
  ) {}

  info(): ProviderInfo {
    return {
      id: this.id,
      name: this.name,
      type: this.type,
      available: false,
      configured: true,
      detail: this.getBaseUrl(),
    };
  }

  async isAvailable(): Promise<boolean> {
    try {
      const res = await fetch(`${this.getBaseUrl()}/api/tags`, {
        signal: AbortSignal.timeout(1500),
      });
      return res.ok;
    } catch {
      return false;
    }
  }

  async listModels(): Promise<ModelInfo[]> {
    try {
      const res = await fetch(`${this.getBaseUrl()}/api/tags`, {
        signal: AbortSignal.timeout(1500),
      });
      if (!res.ok) return [];
      const data = (await res.json()) as { models?: Array<{ name: string; size?: number }> };
      return (data.models || []).map((m) => ({
        id: `${this.id}:${m.name}`,
        name: m.name,
        providerId: this.id,
        capability: ['chat', 'general'],
        status: 'available' as const,
        detail: m.size ? `${(m.size / 1e9).toFixed(2)} GB` : undefined,
      }));
    } catch (e) {
      logger.debug('provider:ollama', 'listModels failed', { error: String(e) });
      return [];
    }
  }

  async generate(req: GenerateRequest): Promise<GenerateResponse> {
    const model = req.model || this.getDefaultModel();
    const res = await fetch(`${this.getBaseUrl()}/api/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model,
        prompt: req.prompt,
        system: req.system,
        stream: false,
        options: { temperature: req.temperature ?? 0.7 },
      }),
      signal: AbortSignal.timeout(120000),
    });
    if (!res.ok) {
      throw new Error(`Ollama responded with ${res.status}`);
    }
    const data = (await res.json()) as { response?: string; eval_count?: number };
    return {
      text: data.response ?? '',
      model,
      providerId: this.id,
      usage: { completionTokens: data.eval_count },
    };
  }
}
