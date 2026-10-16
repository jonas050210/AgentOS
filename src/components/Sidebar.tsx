import React from 'react';
import { useSystem } from '@/state/SystemContext';

export type ViewId = 'chat' | 'models' | 'capabilities' | 'activity' | 'settings';

const NAV: Array<{ id: ViewId; label: string; hint: string; icon: React.ReactNode }> = [
  { id: 'chat', label: 'Chat', hint: 'Talk to AgentOS', icon: <IconChat /> },
  { id: 'models', label: 'Models', hint: 'Providers & inference', icon: <IconLayers /> },
  { id: 'capabilities', label: 'Capabilities', hint: 'Skills & tools', icon: <IconGrid /> },
  { id: 'activity', label: 'Activity', hint: 'Logs & events', icon: <IconPulse /> },
  { id: 'settings', label: 'Settings', hint: 'System configuration', icon: <IconSliders /> },
];

function IconChat() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M4 5h16v11H8l-4 4V5Z" strokeLinejoin="round" />
    </svg>
  );
}
function IconLayers() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M12 3 3 8l9 5 9-5-9-5Z" strokeLinejoin="round" />
      <path d="M3 13l9 5 9-5" strokeLinejoin="round" />
    </svg>
  );
}
function IconGrid() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </svg>
  );
}
function IconPulse() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M3 12h4l2.5-6 3 12 2.5-6h5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function IconSliders() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M4 8h10M18 8h2M4 16h4M12 16h8" strokeLinecap="round" />
      <circle cx="16" cy="8" r="2" />
      <circle cx="10" cy="16" r="2" />
    </svg>
  );
}

export function Sidebar({ view, onChange }: { view: ViewId; onChange: (v: ViewId) => void }) {
  const { connection, capabilities, tools } = useSystem();
  const readyCapabilities = capabilities.filter((c) => c.status === 'available').length;

  return (
    <aside className="flex w-60 shrink-0 flex-col gap-6 border-r border-white/[0.05] bg-base-900/60 px-4 py-5 backdrop-blur-xl">
      <div className="flex items-center gap-3 px-1">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-accent-500 to-accent-600 text-sm font-bold text-white shadow-glass">
          A
        </div>
        <div className="leading-tight">
          <p className="text-sm font-semibold text-slate-100">AgentOS</p>
          <p className="text-[11px] text-slate-500">Modular AI desktop</p>
        </div>
      </div>

      <nav className="flex flex-col gap-1" aria-label="Main navigation">
        {NAV.map((item) => {
          const active = view === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onChange(item.id)}
              aria-current={active ? 'page' : undefined}
              className={`group flex items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors duration-150 ${
                active ? 'bg-white/[0.06] text-slate-100' : 'text-slate-400 hover:bg-white/[0.03] hover:text-slate-200'
              }`}
            >
              <span className={active ? 'text-accent-400' : 'text-slate-500 group-hover:text-slate-300'}>{item.icon}</span>
              <span className="flex-1">
                <span className="block text-sm">{item.label}</span>
                <span className="block text-[11px] text-slate-500">{item.hint}</span>
              </span>
            </button>
          );
        })}
      </nav>

      <div className="mt-auto space-y-3 px-1 text-[11px] text-slate-500">
        <div className="glass-subtle px-3 py-2">
          <p className="flex items-center justify-between">
            <span>Backend</span>
            <span className={connection === 'online' ? 'text-signal-ok' : connection === 'connecting' ? 'text-signal-warn' : 'text-signal-error'}>
              {connection}
            </span>
          </p>
          <p className="mt-1 flex items-center justify-between">
            <span>Capabilities ready</span>
            <span className="text-slate-300">
              {readyCapabilities}/{capabilities.length || '—'}
            </span>
          </p>
          <p className="mt-1 flex items-center justify-between">
            <span>Tools registered</span>
            <span className="text-slate-300">{tools.length || '—'}</span>
          </p>
        </div>
        <p>v0.1.0 · local-first</p>
      </div>
    </aside>
  );
}
