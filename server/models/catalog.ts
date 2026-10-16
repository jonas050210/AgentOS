import { ModelInfo } from '../../shared/types.js';
import { providerRegistry } from '../providers/registry.js';
import { logger } from '../logger.js';

export interface ModelCatalogSnapshot {
  models: ModelInfo[];
  providers: Array<{ id: string; name: string; available: boolean; configured: boolean; detail?: string; modelCount: number }>;
  generatedAt: string;
  anyExternalAvailable: boolean;
}

/**
 * The catalog merges every provider's declared models with live availability.
 * Models that cannot be reached are reported as `unavailable`/`unconfigured`
 * instead of being hidden — the UI must never imply capability that isn't real.
 */
export async function getCatalog(): Promise<ModelCatalogSnapshot> {
  const providers = await providerRegistry.describe();
  const models: ModelInfo[] = [];

  for (const p of providers) {
    const impl = providerRegistry.get(p.id);
    if (!impl) continue;
    if (!p.available) {
      // Still surface the provider's declared models, marked unusable.
      let declared: ModelInfo[] = [];
      if (p.id === 'openai') {
        declared = [
          {
            id: 'openai:gpt-4o-mini',
            name: 'gpt-4o-mini',
            providerId: p.id,
            capability: ['chat', 'general'],
            status: p.configured ? 'error' : 'unconfigured',
            detail: p.configured ? 'Provider unreachable' : 'API key not configured',
          },
        ];
      } else if (p.id === 'ollama') {
        declared = [
          {
            id: 'ollama:llama3.2',
            name: 'llama3.2',
            providerId: p.id,
            capability: ['chat', 'general'],
            status: 'unavailable',
            detail: 'Ollama service not reachable',
          },
        ];
      }
      models.push(...declared);
      continue;
    }
    try {
      const list = await impl.listModels();
      models.push(...list);
    } catch (e) {
      logger.warn('models', `Failed to list models for ${p.id}`, { error: String(e) });
    }
  }

  return {
    models,
    providers: providers.map((p) => ({
      id: p.id,
      name: p.name,
      available: p.available,
      configured: p.configured,
      detail: p.detail,
      modelCount: models.filter((m) => m.providerId === p.id).length,
    })),
    generatedAt: new Date().toISOString(),
    anyExternalAvailable: providers.some((p) => p.id !== 'builtin' && p.available),
  };
}
