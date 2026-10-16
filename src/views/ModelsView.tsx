import { useSystem } from '@/state/SystemContext';
import { Badge, Card, EmptyState, KeyValue, SectionTitle, Spinner, StatusBadge } from '@/components/ui';
import type { ModelInfo, ProviderInfo, StatusLevel } from '@shared/types';

function providerTone(p: ProviderInfo): StatusLevel {
  if (p.available) return 'ok';
  return p.configured ? 'error' : 'warn';
}

function modelTone(m: ModelInfo): StatusLevel {
  if (m.status === 'available') return 'ok';
  if (m.status === 'unconfigured') return 'warn';
  return 'error';
}

export function ModelsView({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { models, modelProviders, providers, anyExternalAvailable, loading, refresh, health } = useSystem();

  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-3">
        {(providers.length ? providers : []).map((p) => (
          <Card key={p.id} className="!p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-100">{p.name}</h3>
                <p className="mt-0.5 text-xs text-slate-500">{p.detail || 'No endpoint configured'}</p>
              </div>
              <StatusBadge level={providerTone(p)} label={p.available ? 'reachable' : p.configured ? 'unreachable' : 'unconfigured'} />
            </div>
            <div className="mt-3 flex items-center gap-2 text-[11px] text-slate-500">
              <Badge tone="unknown">{p.type}</Badge>
              <span>{modelProviders.find((m) => m.id === p.id)?.modelCount ?? 0} models</span>
            </div>
          </Card>
        ))}
        {providers.length === 0 && !loading && <EmptyState title="No providers reported" description="The backend did not return any provider adapters." />}
      </div>

      <Card
        title="Model catalog"
        subtitle={anyExternalAvailable ? 'Live availability from reachable providers' : 'No external provider is reachable — only the built-in offline engine can serve requests'}
        actions={
          <button type="button" className="btn-ghost !py-1.5 text-xs" onClick={() => void refresh()}>
            {loading ? 'Refreshing…' : 'Refresh catalog'}
          </button>
        }
      >
        {loading && models.length === 0 ? (
          <Spinner label="Loading models…" />
        ) : models.length === 0 ? (
          <EmptyState
            title="No models registered"
            description="Start Ollama locally or add an OpenAI-compatible API key in Settings to populate the catalog."
            action={
              <button type="button" className="btn-ghost mt-2 text-xs" onClick={onOpenSettings}>
                Open provider settings
              </button>
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="text-[11px] uppercase tracking-wider text-slate-500">
                  <th className="pb-2 pr-4 font-medium">Model</th>
                  <th className="pb-2 pr-4 font-medium">Provider</th>
                  <th className="pb-2 pr-4 font-medium">Capabilities</th>
                  <th className="pb-2 pr-4 font-medium">Status</th>
                  <th className="pb-2 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {models.map((m) => (
                  <tr key={m.id} className="align-top">
                    <td className="py-2.5 pr-4 font-mono text-xs text-slate-200">{m.name}</td>
                    <td className="py-2.5 pr-4 text-xs text-slate-400">{modelProviders.find((p) => p.id === m.providerId)?.name ?? m.providerId}</td>
                    <td className="py-2.5 pr-4">
                      <div className="flex flex-wrap gap-1">
                        {m.capability.map((c) => (
                          <Badge key={c} tone="unknown">
                            {c}
                          </Badge>
                        ))}
                      </div>
                    </td>
                    <td className="py-2.5 pr-4">
                      <StatusBadge level={modelTone(m)} label={m.status} />
                    </td>
                    <td className="py-2.5 text-xs text-slate-500">{m.detail ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Setup guide" subtitle="How to make real inference available">
          <ol className="space-y-3 text-xs leading-relaxed text-slate-400">
            <li>
              <SectionTitle>Option A — local Ollama</SectionTitle>
              Install Ollama, run <code className="rounded bg-white/[0.06] px-1 font-mono">ollama pull llama3.2</code>, then set the base URL in
              Settings → Providers. The catalog refreshes automatically once the service answers on <code className="rounded bg-white/[0.06] px-1 font-mono">/api/tags</code>.
            </li>
            <li>
              <SectionTitle>Option B — OpenAI-compatible API</SectionTitle>
              Paste an API key and (optionally) a custom base URL in Settings → Providers. Keys are stored server-side in{' '}
              <code className="rounded bg-white/[0.06] px-1 font-mono">data/settings.json</code>, never returned to the browser and redacted in logs.
            </li>
            <li>
              <SectionTitle>Fallback behaviour</SectionTitle>
              Without either option, chat still works through the deterministic built-in engine. Responses are explicitly labelled so the degraded
              mode is never mistaken for real model output.
            </li>
          </ol>
        </Card>

        <Card title="Runtime diagnostics" subtitle="Values reported by the backend">
          <KeyValue label="Backend version">{health?.version ?? '—'}</KeyValue>
          <KeyValue label="Uptime">{health ? `${health.uptimeSeconds}s` : '—'}</KeyValue>
          <KeyValue label="Health">{health ? <StatusBadge level={health.status === 'healthy' ? 'ok' : 'warn'} label={health.status} /> : '—'}</KeyValue>
          <KeyValue label="External provider">{anyExternalAvailable ? 'reachable' : 'none'}</KeyValue>
          <KeyValue label="Catalog size">{models.length}</KeyValue>
        </Card>
      </div>
    </div>
  );
}
