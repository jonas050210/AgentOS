import { ModelProvider } from './types.js';
import { OllamaProvider } from './ollamaProvider.js';
import { OpenAIProvider } from './openaiProvider.js';
import { EchoProvider } from './echoProvider.js';
import { settingsStore } from '../settingsStore.js';
import { logger } from '../logger.js';
import { ProviderInfo } from '../../shared/types.js';

class ProviderRegistry {
  private providers = new Map<string, ModelProvider>();

  constructor() {
    this.register(new EchoProvider());
    this.register(new OllamaProvider(() => settingsStore.get().providers.ollama.baseUrl, () => settingsStore.get().providers.ollama.model));
    this.register(
      new OpenAIProvider(() => settingsStore.get().providers.openai),
    );
  }

  register(p: ModelProvider) {
    if (this.providers.has(p.id)) {
      logger.warn('providers', `Overwriting provider ${p.id}`);
    }
    this.providers.set(p.id, p);
    logger.debug('providers', `Registered provider ${p.id}`);
  }

  get(id: string): ModelProvider | undefined {
    return this.providers.get(id);
  }

  all(): ModelProvider[] {
    return [...this.providers.values()];
  }

  async describe(): Promise<ProviderInfo[]> {
    const out: ProviderInfo[] = [];
    for (const p of this.providers.values()) {
      const base = p.info();
      let available = false;
      try {
        available = await p.isAvailable();
      } catch {
        available = false;
      }
      out.push({ ...base, available });
    }
    return out;
  }

  /** First available provider, preferring configured external ones over the offline fallback. */
  async preferred(): Promise<ModelProvider> {
    const ordered = ['openai', 'ollama', 'builtin']
      .map((id) => this.providers.get(id))
      .filter((p): p is ModelProvider => Boolean(p));
    for (const p of ordered) {
      try {
        if (await p.isAvailable()) return p;
      } catch {
        /* keep probing */
      }
    }
    return this.providers.get('builtin')!;
  }
}

export const providerRegistry = new ProviderRegistry();
