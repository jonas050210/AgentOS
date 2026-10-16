import { StatusDot } from './ui';
import { useSystem } from '@/state/SystemContext';
import type { ViewId } from './Sidebar';

const TITLES: Record<ViewId, { title: string; subtitle: string }> = {
  chat: { title: 'Interaction', subtitle: 'Route requests to capabilities and models' },
  models: { title: 'Models', subtitle: 'Providers, inference backends and availability' },
  capabilities: { title: 'Capabilities', subtitle: 'Skills, tools and execution status' },
  activity: { title: 'Activity', subtitle: 'Live system log and event stream' },
  settings: { title: 'Settings', subtitle: 'Persistent configuration and permissions' },
};

export function TopBar({ view }: { view: ViewId }) {
  const { health, connection, settings, anyExternalAvailable, refresh } = useSystem();
  const meta = TITLES[view];

  const modelStatus: 'ok' | 'warn' | 'error' = !health
    ? 'error'
    : anyExternalAvailable
      ? 'ok'
      : 'warn';

  return (
    <header className="flex items-center justify-between gap-6 border-b border-white/[0.05] bg-base-900/50 px-6 py-4 backdrop-blur-xl">
      <div>
        <h1 className="text-sm font-semibold tracking-wide text-slate-100">{meta.title}</h1>
        <p className="text-xs text-slate-500">{meta.subtitle}</p>
      </div>

      <div className="flex items-center gap-4 text-xs">
        <Indicator
          level={modelStatus}
          label={anyExternalAvailable ? 'Model online' : 'Offline engine'}
          detail={anyExternalAvailable ? 'External provider reachable' : 'No external provider — deterministic fallback'}
        />
        <Indicator
          level={connection === 'online' ? 'ok' : connection === 'connecting' ? 'warn' : 'error'}
          label={connection === 'online' ? 'System nominal' : connection === 'connecting' ? 'Connecting' : 'Backend offline'}
          detail={health ? `uptime ${health.uptimeSeconds}s · v${health.version}` : 'Health check pending'}
        />
        <Indicator
          level={settings?.permissions.allowShell || settings?.permissions.allowFileWrite ? 'warn' : 'ok'}
          label={settings?.permissions.allowShell || settings?.permissions.allowFileWrite ? 'Elevated access' : 'Restricted mode'}
          detail={
            settings
              ? `read ${settings.permissions.allowFileRead ? 'on' : 'off'} · write ${settings.permissions.allowFileWrite ? 'on' : 'off'} · net ${settings.permissions.allowNetwork ? 'on' : 'off'}`
              : 'Loading permissions'
          }
        />
        <button type="button" onClick={() => void refresh()} className="btn-ghost !px-2.5 !py-1.5 text-xs">
          Refresh
        </button>
      </div>
    </header>
  );
}

function Indicator({ level, label, detail }: { level: 'ok' | 'warn' | 'error'; label: string; detail: string }) {
  return (
    <div className="hidden items-center gap-2 lg:flex" title={detail}>
      <StatusDot level={level} pulse={level !== 'ok'} />
      <div className="leading-tight">
        <p className="text-slate-300">{label}</p>
        <p className="text-[10px] text-slate-500">{detail}</p>
      </div>
    </div>
  );
}
