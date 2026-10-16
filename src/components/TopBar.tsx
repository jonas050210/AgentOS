import React from 'react';
import { useSystem } from '@/state/SystemContext';
import { Badge, StatusDot } from '@/components/ui';
import type { NavTarget, ViewId } from '@/components/Sidebar';

/**
 * Context header. It carries the honest status of the machine the views act on:
 * how the backend is doing, which local model can actually answer, and whether a
 * provider is reachable at all.
 */

const TITLES: Record<ViewId, { title: string; subtitle: string }> = {
  overview: { title: 'Overview', subtitle: 'Every specialist, model version and measurement on this machine' },
  assistant: { title: 'AI Assistant', subtitle: 'Ask once — AgentOS routes to the specialist that should answer' },
  specialist: { title: 'Specialist workspace', subtitle: 'Model, capabilities, training history and a test bench' },
  'add-ai': { title: 'Add AI', subtitle: 'Register a new specialist and choose how it will be trained' },
  training: { title: 'Training Lab', subtitle: 'Configure a run, watch it train, keep the better version' },
  benchmarks: { title: 'Benchmarks', subtitle: 'Objective before/after evidence for every promotion' },
  activity: { title: 'Activity', subtitle: 'Everything the runtime did, in order' },
  settings: { title: 'Settings', subtitle: 'Routing, permissions, providers and appearance' },
};

export function TopBar({
  target,
  title,
  subtitle,
  actions,
  onRefresh,
}: {
  target: NavTarget;
  title?: string;
  subtitle?: string;
  actions?: React.ReactNode;
  onRefresh?: () => void;
}) {
  const { connection, health, providers, anyExternalAvailable, runs, settings, trainingActive } = useSystem();
  const local = providers.filter((provider) => provider.id !== 'builtin' && provider.available);
  const external = providers.find((provider) => provider.id !== 'builtin' && provider.available) ?? null;
  const base = TITLES[target.view];
  const running = runs.filter((run) => run.status === 'running' || run.status === 'queued').length;

  return (
    <header className="sticky top-0 z-20 border-b border-white/[0.05] bg-base-950/70 px-6 py-3.5 backdrop-blur-2xl">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="display text-[1.05rem] text-slate-100">{title ?? base.title}</h1>
            {trainingActive && (
              <Badge tone="warn">
                <StatusDot level="warn" pulse />
                {running} training
              </Badge>
            )}
          </div>
          <p className="mt-0.5 truncate text-xs text-slate-400">{subtitle ?? base.subtitle}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {actions}
          <Badge tone={connection === 'online' ? 'ok' : connection === 'connecting' ? 'warn' : 'error'}>
            <StatusDot level={connection === 'online' ? 'ok' : connection === 'connecting' ? 'warn' : 'error'} pulse={connection !== 'online'} />
            {connection}
          </Badge>
          <Badge tone={anyExternalAvailable ? 'ok' : 'warn'} title={external ? `${external.name} reachable` : 'No external model provider is reachable'}>
            {anyExternalAvailable ? (external?.name ?? 'provider ready') : 'offline engine only'}
          </Badge>
          <Badge tone={local.length > 0 ? 'ok' : 'unknown'} title="Locally trained model versions across all specialists">
            {local.length > 0 ? `${local.length} local model(s)` : 'no provider'}
          </Badge>
          {settings && (
            <Badge tone="unknown" title="How requests are routed when no specialist is pinned">
              {settings.routing.autoRoute ? 'auto-route on' : 'auto-route off'}
            </Badge>
          )}
          {onRefresh && (
            <button type="button" onClick={onRefresh} className="btn-ghost" aria-label="Refresh data">
              Refresh
            </button>
          )}
          {health && <span className="hidden text-2xs text-slate-500 xl:inline">build v{health.version}</span>}
        </div>
      </div>
    </header>
  );
}
