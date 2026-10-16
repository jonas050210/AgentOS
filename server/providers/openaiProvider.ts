import { ModelInfo, ProviderInfo } from '../../shared/types.js';
import { GenerateRequest, GenerateResponse, ModelProvider } from './types.js';
import { logger } from '../logger.js';

/**
 * Adapter for OpenAI-compatible HTTP APIs (OpenAI, Azure-compatible gateways,
 * LM Studio, vLLM, ...). Requires a user-supplied API key.
 */
export class OpenAIProvider implements ModelProvider {
  readonly id = 'openai';
  readonly name = 'OpenAI-compatible API';
  readonly type = 'cloud' as const;

  constructor(
    private getConfig: () => { apiKey: string; baseUrl: string; model: string },
  ) {}

  info(): ProviderInfo {
    const cfg = this.getConfig();
    return {
      id: this.id,
      name: this.name,
      type: this.type,
      available: false,
      configured: Boolean(cfg.apiKey),
      detail: cfg.apiKey ? cfg.baseUrl : 'API key not set',
    };
  }

  async isAvailable(): Promise<boolean> {
    const cfg = this.getConfig();
    if (!cfg.apiKey) return false;
    try {
      const res = await fetch(`${cfg.baseUrl}/models`, {
        headers: { Authorization: `Bearer ${cfg.apiKey}` },
        signal: AbortSignal.timeout(3000),
      });
      return res.ok;
    } catch (e) {
      logger.debug('provider:openai', 'availability probe failed', { error: String(e) });
      return false;
    }
  }

  async listModels(): Promise<ModelInfo[]> {
    const cfg = this.getConfig();
    if (!cfg.apiKey) return [];
    try {
      const res = await fetch(`${cfg.baseUrl}/models`, {
        headers: { Authorization: `Bearer ${cfg.apiKey}` },
        signal: AbortSignal.timeout(5000),
      });
      if (!res.ok) return [];
      const data = (await res.json()) as { data?: Array<{ id: string }> };
      return (data.data || [])
        .slice(0, 50)
        .map((m) => ({
          id: `${this.id}:${m.id}`,
          name: m.id,
          providerId: this.id,
          capability: ['chat', 'general'],
          status: 'available' as const,
        }));
    } catch {
      return [];
    }
  }

  async generate(req: GenerateRequest): Promise<GenerateResponse> {
    const cfg = this.getConfig();
    if (!cfg.apiKey) throw new Error('API key not configured');
    const model = req.model || cfg.model;
    const res = await fetch(`${cfg.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${cfg.apiKey}`,
      },
      body: JSON.stringify({
        model,
        messages: [
          ...(req.system ? [{ role: 'system', content: req.system }] : []),
          { role: 'user', content: req.prompt },
        ],
        temperature: req.temperature ?? 0.7,
        max_tokens: req.maxTokens,
      }),
      signal: AbortSignal.timeout(120000),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`Provider responded with ${res.status}: ${body.slice(0, 200)}`);
    }
    const data = (await res.json()) as {
      choices?: Array<{ message?: { content?: string } }>;
      usage?: { prompt_tokens?: number; completion_tokens?: number };
    };
    return {
      text: data.choices?.[0]?.message?.content ?? '',
      model,
      providerId: this.id,
      usage: {
        promptTokens: data.usage?.prompt_tokens,
        completionTokens: data.usage?.completion_tokens,
      },
    };
  }
}
