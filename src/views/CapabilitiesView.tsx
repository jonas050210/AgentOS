import { useState } from 'react';
import { api, ApiError } from '@/lib/api';
import { useSystem } from '@/state/SystemContext';
import { Badge, Card, EmptyState, ErrorBanner, SectionTitle, StatusBadge } from '@/components/ui';
import type { CapabilityInfo, StatusLevel, ToolExecutionResult, ToolInfo } from '@shared/types';

const catTone: Record<CapabilityInfo['category'], StatusLevel> = {
  coding: 'ok',
  research: 'ok',
  knowledge: 'ok',
  general: 'ok',
  speech: 'warn',
};

export function CapabilitiesView() {
  const { capabilities, tools, anyExternalAvailable, settings } = useSystemDerived();
  const [selectedTool, setSelectedTool] = useState<ToolInfo | null>(null);
  const [toolInput, setToolInput] = useState('{}');
  const [result, setResult] = useState<ToolExecutionResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runTool() {
    if (!selectedTool) return;
    setError(null);
    setResult(null);
    let parsed: Record<string, unknown>;
    try {
      parsed = toolInput.trim() ? (JSON.parse(toolInput) as Record<string, unknown>) : {};
    } catch {
      setError('Input must be valid JSON.');
      return;
    }
    setRunning(true);
    try {
      const outcome = await api.executeTool(selectedTool.id, parsed);
      setResult(outcome);
      if (!outcome.success) setError(outcome.error ?? 'Tool execution failed');
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="space-y-6">
      {!anyExternalAvailable && (
        <ErrorBanner message="No external model provider is reachable. Capabilities that require inference will fall back to the built-in offline engine and are marked partial." />
      )}

      <div>
        <SectionTitle hint={`${capabilities.filter((c) => c.status === 'available').length} of ${capabilities.length} fully available`}>
          Capability modules
        </SectionTitle>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {capabilities.map((c) => (
            <Card key={c.id} className="!p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-slate-100">{c.name}</h3>
                  <p className="mt-1 text-xs leading-relaxed text-slate-400">{c.description}</p>
                </div>
                <StatusBadge level={c.status === 'available' ? 'ok' : c.status === 'partial' ? 'warn' : 'error'} label={c.status} />
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
                <Badge tone={catTone[c.category]}>{c.category}</Badge>
                {c.toolIds.length === 0 ? (
                  <span className="text-slate-500">no tools bound</span>
                ) : (
                  c.toolIds.map((t) => (
                    <span key={t} className="rounded bg-white/[0.05] px-1.5 py-0.5 font-mono text-slate-400">
                      {t}
                    </span>
                  ))
                )}
              </div>
              {c.status === 'partial' && (
                <p className="mt-3 text-[11px] text-signal-warn">
                  {c.category === 'speech'
                    ? settings?.voice.sttEnabled || settings?.voice.ttsEnabled
                      ? 'Browser speech engines active; server-side engines unconfigured.'
                      : 'Speech disabled in Settings → Voice.'
                    : !anyExternalAvailable
                      ? 'Degraded: needs a reachable model provider (offline engine will answer).'
                      : 'Degraded: at least one bound tool lacks its permission.'}
                </p>
              )}
            </Card>
          ))}
        </div>
      </div>

      <Card
        title="Tools"
        subtitle="Every tool declares its permission requirements; execution is validated and sandboxed by the backend"
      >
        {tools.length === 0 ? (
          <EmptyState title="No tools registered" description="The backend reported an empty tool registry." />
        ) : (
          <div className="space-y-2">
            {tools.map((t) => (
              <div key={t.id} className="glass-subtle flex items-start justify-between gap-4 px-4 py-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-slate-200">{t.id}</span>
                    <Badge tone={t.available ? 'ok' : 'warn'}>{t.requiresPermission ?? t.permission}</Badge>
                    <Badge tone="unknown">{t.category}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-slate-400">{t.description}</p>
                </div>
                <button
                  type="button"
                  className="btn-ghost shrink-0 !py-1.5 text-xs"
                  disabled={!t.available}
                  title={t.available ? 'Run this tool' : 'Required permission is disabled in Settings'}
                  onClick={() => {
                    setSelectedTool(t);
                    setResult(null);
                    setError(null);
                    const props = (t.inputSchema as { properties?: Record<string, { required?: boolean }> }).properties ?? {};
                    const skeleton: Record<string, string> = {};
                    for (const [k, v] of Object.entries(props)) if (v.required) skeleton[k] = '';
                    setToolInput(JSON.stringify(skeleton, null, 2));
                  }}
                >
                  {t.available ? 'Run' : 'Blocked'}
                </button>
              </div>
            ))}
          </div>
        )}
      </Card>

      {selectedTool && (
        <Card
          title={`Run ${selectedTool.id}`}
          subtitle={selectedTool.description}
          actions={
            <button type="button" className="btn-ghost !py-1.5 text-xs" onClick={() => setSelectedTool(null)}>
              Close
            </button>
          }
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <label className="label" htmlFor="tool-input">
                Structured input (JSON)
              </label>
              <textarea
                id="tool-input"
                className="input h-40 font-mono text-xs"
                value={toolInput}
                onChange={(e) => setToolInput(e.target.value)}
              />
              <div className="mt-3 flex gap-2">
                <button type="button" className="btn-primary text-xs" onClick={() => void runTool()} disabled={running}>
                  {running ? 'Executing…' : 'Execute'}
                </button>
              </div>
              <details className="mt-3 text-xs text-slate-500">
                <summary className="cursor-pointer">Input schema</summary>
                <pre className="mt-2 overflow-x-auto rounded bg-black/40 p-3 font-mono text-[11px] text-slate-400">
                  {JSON.stringify(selectedTool.inputSchema, null, 2)}
                </pre>
              </details>
            </div>
            <div>
              <p className="label">Result</p>
              {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
              {result && (
                <div className="mt-2 space-y-2">
                  <div className="flex items-center gap-2 text-xs">
                    <StatusBadge level={result.success ? 'ok' : 'error'} label={result.success ? 'success' : 'failed'} />
                    <span className="text-slate-500">{result.durationMs} ms</span>
                  </div>
                  <pre className="max-h-52 overflow-auto rounded bg-black/40 p-3 font-mono text-[11px] text-slate-300">
                    {JSON.stringify(result.success ? result.output : result.error, null, 2)}
                  </pre>
                </div>
              )}
              {!result && !error && <p className="text-xs text-slate-500">Execute the tool to see structured output here.</p>}
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

/** Adds the derived permission label used by the tool list. */
function useSystemDerived() {
  const { capabilities, tools, anyExternalAvailable, settings } = useSystem();
  const shownTools = tools.map((t) => ({ ...t, requiresPermission: t.permission }));
  return { capabilities, tools: shownTools, anyExternalAvailable, settings };
}
