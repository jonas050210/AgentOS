import React from 'react';
import type { StatusLevel } from '@shared/types';

const toneClasses: Record<StatusLevel, string> = {
  ok: 'bg-signal-ok/15 text-signal-ok border-signal-ok/30',
  warn: 'bg-signal-warn/15 text-signal-warn border-signal-warn/30',
  error: 'bg-signal-error/15 text-signal-error border-signal-error/30',
  unknown: 'bg-white/[0.04] text-signal-muted border-white/10',
};

export function StatusDot({ level, pulse = false }: { level: StatusLevel; pulse?: boolean }) {
  const color =
    level === 'ok' ? 'bg-signal-ok' : level === 'warn' ? 'bg-signal-warn' : level === 'error' ? 'bg-signal-error' : 'bg-signal-muted';
  return (
    <span className="relative inline-flex h-2 w-2 shrink-0" aria-hidden>
      <span className={`h-2 w-2 rounded-full ${color} ${pulse ? 'animate-pulse-soft' : ''}`} />
    </span>
  );
}

export function Badge({
  children,
  tone = 'unknown',
  className = '',
}: {
  children: React.ReactNode;
  tone?: StatusLevel;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium ${toneClasses[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

export function StatusBadge({ level, label }: { level: StatusLevel; label: string }) {
  return (
    <Badge tone={level}>
      <StatusDot level={level} />
      {label}
    </Badge>
  );
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = '',
}: {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`glass-panel p-5 ${className}`}>
      {(title || actions) && (
        <header className="mb-4 flex items-start justify-between gap-4">
          <div>
            {title && <h2 className="text-sm font-semibold tracking-wide text-slate-100">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-xs text-slate-400" role="status">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/20 border-t-accent-500" />
      {label}
    </span>
  );
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: React.ReactNode }) {
  return (
    <div className="glass-subtle flex flex-col items-center gap-2 px-6 py-10 text-center">
      <p className="text-sm font-medium text-slate-200">{title}</p>
      <p className="max-w-md text-xs leading-relaxed text-slate-400">{description}</p>
      {action}
    </div>
  );
}

export function ErrorBanner({ message, onDismiss, onRetry }: { message: string; onDismiss?: () => void; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-lg border border-signal-error/30 bg-signal-error/10 px-4 py-3 text-sm text-signal-error"
    >
      <span className="mt-0.5 shrink-0" aria-hidden>
        !
      </span>
      <p className="flex-1 leading-relaxed">{message}</p>
      <div className="flex shrink-0 gap-2">
        {onRetry && (
          <button type="button" onClick={onRetry} className="rounded border border-signal-error/40 px-2 py-0.5 text-xs hover:bg-signal-error/15">
            Retry
          </button>
        )}
        {onDismiss && (
          <button type="button" onClick={onDismiss} className="rounded border border-signal-error/40 px-2 py-0.5 text-xs hover:bg-signal-error/15">
            Dismiss
          </button>
        )}
      </div>
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
  danger = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  description?: string;
  danger?: boolean;
}) {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-4 py-2.5">
      <span>
        <span className="block text-sm text-slate-200">{label}</span>
        {description && <span className="mt-0.5 block text-xs text-slate-400">{description}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={`relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors duration-150 ${
          checked ? (danger ? 'bg-signal-warn' : 'bg-accent-500') : 'bg-base-700'
        }`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform duration-150 ${
            checked ? 'translate-x-[18px]' : 'translate-x-0.5'
          }`}
        />
      </button>
    </label>
  );
}

export function KeyValue({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-white/[0.04] py-2 last:border-0">
      <span className="text-xs uppercase tracking-wider text-slate-500">{label}</span>
      <span className="text-right text-sm text-slate-200">{children}</span>
    </div>
  );
}

export function SectionTitle({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <div className="mb-3 flex items-baseline gap-3">
      <h2 className="text-base font-semibold text-slate-100">{children}</h2>
      {hint && <span className="text-xs text-slate-500">{hint}</span>}
    </div>
  );
}
