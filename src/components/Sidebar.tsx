import React, { useMemo } from 'react';
import { useSystem } from '@/state/SystemContext';
import { StatusDot } from '@/components/ui';

export type ViewId =
  | 'overview'
  | 'assistant'
  | 'specialist'
  | 'add-ai'
  | 'training'
  | 'benchmarks'
  | 'activity'
  | 'settings';

export interface NavTarget {
  view: ViewId;
  specialistId?: string;
}

interface NavItem {
  id: ViewId;
  label: string;
  hint: string;
  icon: React.ReactNode;
}

const PRIMARY: NavItem[] = [
  { id: 'overview', label: 'Overview', hint: 'System at a glance', icon: <IconCompass /> },
  { id: 'assistant', label: 'AI Assistant', hint: 'One request, right specialist', icon: <IconChat /> },
];

const BUILD: NavItem[] = [
  { id: 'add-ai', label: 'Add AI', hint: 'Register a specialist', icon: <IconPlusCircle /> },
  { id: 'training', label: 'Training Lab', hint: 'Runs, checkpoints, versions', icon: <IconFlask /> },
  { id: 'benchmarks', label: 'Benchmarks', hint: 'Before/after evidence', icon: <IconChart /> },
];

const SYSTEM: NavItem[] = [
  { id: 'activity', label: 'Activity', hint: 'Logs & events', icon: <IconPulse /> },
  { id: 'settings', label: 'Settings', hint: 'Routing, permissions, look', icon: <IconSliders /> },
];

function IconCompass() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
      <circle cx="12" cy="12" r="8.5" />
      <path d="m15 9-2 4-4 2 2-4 4-2Z" strokeLinejoin="round" />
    </svg>
  );
}
function IconChat() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M4 5h16v11H8l-4 4V5Z" strokeLinejoin="round" />
    </svg>
  );
}
function IconPlusCircle() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 8.5v7M8.5 12h7" strokeLinecap="round" />
    </svg>
  );
}
function IconFlask() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M9 3h6M10 3v6.2L5.4 17A2.6 2.6 0 0 0 7.7 21h8.6a2.6 2.6 0 0 0 2.3-4L14 9.2V3" strokeLinejoin="round" strokeLinecap="round" />
      <path d="M7.6 15h8.8" strokeLinecap="round" />
    </svg>
  );
}
function IconChart() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M4 19V5M4 19h16" strokeLinecap="round" />
      <path d="M8 19v-6M12 19V8M16 19v-9" strokeLinecap="round" />
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

function NavButton({
  item,
  target,
  onChange,
  badge,
}: {
  item: NavItem;
  target: NavTarget;
  onChange: (target: NavTarget) => void;
  badge?: React.ReactNode;
}) {
  const active = target.view === item.id;
  return (
    <button
      type="button"
      onClick={() => onChange({ view: item.id })}
      aria-current={active ? 'page' : undefined}
      className="nav-item group w-full"
    >
      <span className="nav-rail" aria-hidden />
      <span className={`shrink-0 transition-colors duration-200 ${active ? 'text-accent-300' : 'text-slate-500 group-hover:text-slate-300'}`}>
        {item.icon}
      </span>
      <span className="min-w-0 flex-1 text-left">
        <span className="block truncate text-sm">{item.label}</span>
        <span className="block truncate text-2xs text-slate-500">{item.hint}</span>
      </span>
      {badge}
    </button>
  );
}

export function Sidebar({ target, onChange }: { target: NavTarget; onChange: (target: NavTarget) => void }) {
  const { connection, specialists, tools, trainingActive } = useSystem();

  const trained = useMemo(() => specialists.filter((s) => s.activeVersion).length, [specialists]);

  const connectionTone =
    connection === 'online' ? 'text-signal-ok' : connection === 'connecting' ? 'text-signal-warn' : 'text-signal-error';

  return (
    <aside className="relative flex w-[16.5rem] shrink-0 flex-col border-r border-white/[0.05] bg-base-950/70 backdrop-blur-2xl">
      <div className="pointer-events-none absolute inset-y-0 right-0 w-px bg-gradient-to-b from-transparent via-white/10 to-transparent" />

      <div className="flex items-center gap-3 px-5 pt-6 pb-5">
        <div className="relative flex h-10 w-10 items-center justify-center rounded-2xl bg-gradient-to-br from-accent-400 via-accent-500 to-accent-600 text-sm font-bold text-white shadow-glow">
          <span className="relative z-10">A</span>
          <span className="absolute inset-0 animate-breathe rounded-2xl bg-accent-400/25 blur-md" aria-hidden />
        </div>
        <div className="leading-tight">
          <p className="display text-[0.95rem] text-slate-100">AgentOS</p>
          <p className="text-2xs uppercase tracking-[0.18em] text-slate-500">AI operating environment</p>
        </div>
      </div>

      <nav className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-3 pb-4" aria-label="Main navigation">
        <div className="space-y-1">
          {PRIMARY.map((item) => (
            <NavButton key={item.id} item={item} target={target} onChange={onChange} />
          ))}
        </div>

        <div className="space-y-1">
          <p className="eyebrow px-3 pb-1">Specialists</p>
          {specialists.length === 0 ? (
            <p className="px-3 text-2xs leading-relaxed text-slate-600">No specialists registered yet.</p>
          ) : (
            specialists.map((specialist) => {
              const active = target.view === 'specialist' && target.specialistId === specialist.id;
              const tone =
                specialist.state === 'ready'
                  ? 'ok'
                  : specialist.state === 'training'
                    ? 'warn'
                    : specialist.state === 'degraded'
                      ? 'warn'
                      : 'unknown';
              return (
                <button
                  key={specialist.id}
                  type="button"
                  onClick={() => onChange({ view: 'specialist', specialistId: specialist.id })}
                  aria-current={active ? 'page' : undefined}
                  className="nav-item group w-full"
                >
                  <span className="nav-rail" aria-hidden />
                  <span
                    className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-[10px] font-semibold"
                    style={{
                      background: `${specialist.accent}22`,
                      color: specialist.accent,
                      boxShadow: `inset 0 0 0 1px ${specialist.accent}44`,
                    }}
                    aria-hidden
                  >
                    {specialist.glyph || specialist.shortName.slice(0, 2)}
                  </span>
                  <span className="min-w-0 flex-1 text-left">
                    <span className="block truncate text-sm">{specialist.shortName || specialist.name}</span>
                    <span className="block truncate text-2xs text-slate-500">
                      {specialist.activeVersion ? specialist.activeVersion.label : 'untrained'} · {specialist.state}
                    </span>
                  </span>
                  <StatusDot level={tone} pulse={specialist.state === 'training'} />
                </button>
              );
            })
          )}
        </div>

        <div className="space-y-1">
          <p className="eyebrow px-3 pb-1">Build</p>
          {BUILD.map((item) => (
            <NavButton
              key={item.id}
              item={item}
              target={target}
              onChange={onChange}
              badge={
                item.id === 'training' && trainingActive ? (
                  <span className="flex items-center gap-1 text-2xs text-signal-warn">
                    <StatusDot level="warn" pulse />
                    live
                  </span>
                ) : undefined
              }
            />
          ))}
        </div>

        <div className="space-y-1">
          <p className="eyebrow px-3 pb-1">System</p>
          {SYSTEM.map((item) => (
            <NavButton key={item.id} item={item} target={target} onChange={onChange} />
          ))}
        </div>
      </nav>

      <div className="space-y-3 px-4 pb-5 pt-3">
        <div className="glass-subtle space-y-1.5 px-3 py-2.5 text-2xs text-slate-500">
          <p className="flex items-center justify-between gap-2">
            <span>Backend</span>
            <span className={connectionTone}>{connection}</span>
          </p>
          <p className="flex items-center justify-between gap-2">
            <span>Trained specialists</span>
            <span className="text-slate-300">
              {trained}/{specialists.length || '—'}
            </span>
          </p>
          <p className="flex items-center justify-between gap-2">
            <span>Tools registered</span>
            <span className="text-slate-300">{tools.length || '—'}</span>
          </p>
        </div>
        <p className="px-1 text-2xs text-slate-600">Local-first · every artefact on this machine</p>
      </div>
    </aside>
  );
}
