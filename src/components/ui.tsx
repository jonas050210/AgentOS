import React from 'react';
import type { SpecialistState, StatusLevel, VersionStatus } from '@shared/types';

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
  title,
}: {
  children: React.ReactNode;
  tone?: StatusLevel;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
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

/** Tone for a specialist lifecycle state — the honest reading of what it can do. */
export function specialistTone(state: SpecialistState): StatusLevel {
  if (state === 'ready') return 'ok';
  if (state === 'training') return 'warn';
  if (state === 'untrained') return 'unknown';
  if (state === 'degraded') return 'warn';
  return 'unknown';
}

export function versionTone(status: VersionStatus): StatusLevel {
  if (status === 'active') return 'ok';
  if (status === 'candidate') return 'warn';
  if (status === 'rejected') return 'error';
  return 'unknown';
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = '',
  as: Tag = 'section',
}: {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  as?: 'section' | 'article' | 'div';
}) {
  return (
    <Tag className={`glass p-5 ${className}`}>
      {(title || actions) && (
        <header className="mb-4 flex items-start justify-between gap-4">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold tracking-wide text-slate-100">{title}</h2>}
            {subtitle && <p className="mt-1 text-xs leading-relaxed text-slate-400">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </Tag>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-xs text-slate-400" role="status">
      <span className="spinner" aria-hidden />
      {label}
    </span>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <div className="glass-subtle flex flex-col items-center gap-2 px-6 py-10 text-center">
      {icon && <div className="mb-1 text-slate-500">{icon}</div>}
      <p className="text-sm font-medium text-slate-200">{title}</p>
      <p className="max-w-md text-xs leading-relaxed text-slate-400">{description}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorBanner({ message, onDismiss, onRetry }: { message: string; onDismiss?: () => void; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-xl border border-signal-error/30 bg-signal-error/10 px-4 py-3 text-sm text-signal-error"
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
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  description?: string;
  danger?: boolean;
  disabled?: boolean;
}) {
  return (
    <label className={`flex items-start justify-between gap-4 py-2.5 ${disabled ? 'opacity-50' : 'cursor-pointer'}`}>
      <span>
        <span className="block text-sm text-slate-200">{label}</span>
        {description && <span className="mt-0.5 block text-xs leading-relaxed text-slate-400">{description}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors duration-200 ${
          checked ? (danger ? 'bg-signal-warn' : 'bg-accent-500') : 'bg-base-700'
        }`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform duration-200 ${
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
      <span className="text-2xs uppercase tracking-wider text-slate-500">{label}</span>
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

/**
 * Compact headline number. `hint` is where honesty lives: it says what the
 * number was measured on, never just what looks good.
 */
export function StatTile({
  label,
  value,
  hint,
  tone = 'default',
  progress,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: 'default' | 'ok' | 'warn' | 'error';
  progress?: number | null;
}) {
  const valueTone =
    tone === 'ok' ? 'text-signal-ok' : tone === 'warn' ? 'text-signal-warn' : tone === 'error' ? 'text-signal-error' : 'text-slate-100';
  return (
    <div className="glass-subtle tile-hover p-3.5">
      <p className="eyebrow">{label}</p>
      <p className={`stat-value mt-1.5 ${valueTone}`}>{value}</p>
      {typeof progress === 'number' && (
        <div className="progress-track mt-2">
          <div className="progress-fill" style={{ width: `${Math.max(0, Math.min(100, progress * 100))}%` }} />
        </div>
      )}
      {hint && <p className="mt-1.5 text-2xs leading-relaxed text-slate-500">{hint}</p>}
    </div>
  );
}

export function Meter({
  label,
  value,
  max = 1,
  detail,
  tone = 'accent',
}: {
  label: string;
  value: number;
  max?: number;
  detail?: string;
  tone?: 'accent' | 'ok' | 'warn' | 'error';
}) {
  const pct = max === 0 ? 0 : Math.max(0, Math.min(1, value / max));
  const bar =
    tone === 'ok'
      ? 'linear-gradient(90deg, rgba(62,207,142,0.85), rgba(62,207,142,0.5))'
      : tone === 'warn'
        ? 'linear-gradient(90deg, rgba(229,181,103,0.85), rgba(229,181,103,0.5))'
        : tone === 'error'
          ? 'linear-gradient(90deg, rgba(242,100,90,0.85), rgba(242,100,90,0.5))'
          : undefined;
  return (
    <div className="metric-bar">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs text-slate-300">{label}</span>
        <span className="font-mono text-xs text-slate-400">{detail ?? `${(pct * 100).toFixed(1)}%`}</span>
      </div>
      <div className="progress-track mt-1.5">
        <div className="progress-fill" style={{ width: `${pct * 100}%`, background: bar }} />
      </div>
    </div>
  );
}

export function ProgressBar({ value, label, indeterminate = false }: { value: number; label?: string; indeterminate?: boolean }) {
  return (
    <div>
      {label && (
        <div className="mb-1.5 flex items-center justify-between text-2xs text-slate-400">
          <span>{label}</span>
          <span className="font-mono">{Math.round(value * 100)}%</span>
        </div>
      )}
      <div className="progress-track">
        <div className={`progress-fill ${indeterminate ? 'animate-shimmer' : ''}`} style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
      </div>
    </div>
  );
}

export function Chip({
  children,
  active = false,
  onClick,
  title,
}: {
  children: React.ReactNode;
  active?: boolean;
  onClick?: () => void;
  title?: string;
}) {
  const Tag = onClick ? 'button' : 'span';
  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      title={title}
      aria-pressed={onClick ? active : undefined}
      className={`chip ${onClick ? 'transition-colors duration-200 hover:text-slate-100' : ''} ${
        active ? 'border-accent-400/50 bg-accent-500/15 text-accent-200' : 'text-slate-400'
      }`}
    >
      {children}
    </Tag>
  );
}

/** Segmented switch used for view tabs inside a workspace. */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: Array<{ id: T; label: string; hint?: string }>;
  value: T;
  onChange: (next: T) => void;
  ariaLabel: string;
}) {
  return (
    <div role="tablist" aria-label={ariaLabel} className="glass-subtle inline-flex flex-wrap gap-1 p-1">
      {options.map((option) => {
        const active = option.id === value;
        return (
          <button
            key={option.id}
            role="tab"
            type="button"
            aria-selected={active}
            title={option.hint}
            onClick={() => onChange(option.id)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-300 ${
              active
                ? 'bg-white/[0.09] text-slate-100 shadow-rise'
                : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-200'
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
  htmlFor?: string;
}) {
  return (
    <label className="block" htmlFor={htmlFor}>
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-2xs leading-relaxed text-slate-500">{hint}</span>}
    </label>
  );
}

export function Select({
  value,
  onChange,
  options,
  ariaLabel,
  disabled = false,
}: {
  value: string;
  onChange: (next: string) => void;
  options: Array<{ value: string; label: string; disabled?: boolean }>;
  ariaLabel: string;
  disabled?: boolean;
}) {
  return (
    <select
      className="input"
      aria-label={ariaLabel}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value} disabled={option.disabled} className="bg-base-850">
          {option.label}
        </option>
      ))}
    </select>
  );
}

/** Before/after bar for one metric, used by the benchmark comparison. */
export function DeltaBar({
  label,
  candidate,
  baseline,
  direction,
  deltaPct,
  regression,
  format,
}: {
  label: string;
  candidate: number;
  baseline: number;
  direction: 'better' | 'worse' | 'same';
  deltaPct: number;
  regression: boolean;
  format?: (value: number) => string;
}) {
  const max = Math.max(Math.abs(candidate), Math.abs(baseline), 1e-9);
  const render = format ?? ((value: number) => value.toFixed(3));
  const tone = regression ? 'error' : direction === 'better' ? 'ok' : direction === 'worse' ? 'warn' : 'accent';
  return (
    <div className="py-2">
      <div className="flex items-baseline justify-between gap-3 text-xs">
        <span className="text-slate-300">{label}</span>
        <span className={`font-mono ${regression ? 'text-signal-error' : direction === 'better' ? 'text-signal-ok' : 'text-slate-400'}`}>
          {render(baseline)} → {render(candidate)}{' '}
          <span className="text-slate-500">({deltaPct >= 0 ? '+' : ''}{deltaPct.toFixed(1)}%)</span>
        </span>
      </div>
      <div className="mt-1.5 space-y-1">
        <div className="progress-track">
          <div className="progress-fill opacity-45" style={{ width: `${(Math.abs(baseline) / max) * 100}%` }} />
        </div>
        <div className="progress-track">
          <div
            className={`progress-fill ${tone === 'error' ? '!bg-signal-error' : tone === 'ok' ? '!bg-signal-ok' : tone === 'warn' ? '!bg-signal-warn' : ''}`}
            style={{ width: `${(Math.abs(candidate) / max) * 100}%` }}
          />
        </div>
      </div>
    </div>
  );
}

/** Tiny inline sparkline for loss/accuracy histories. */
export function Sparkline({
  values,
  width = 160,
  height = 34,
  tone = 'accent',
  ariaLabel,
}: {
  values: number[];
  width?: number;
  height?: number;
  tone?: 'accent' | 'ok' | 'warn';
  ariaLabel: string;
}) {
  if (values.length < 2) {
    return <div className="h-[34px] text-2xs text-slate-600">not enough history</div>;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  const points = values
    .map((value, index) => `${(index * step).toFixed(2)},${(height - ((value - min) / span) * (height - 4) - 2).toFixed(2)}`)
    .join(' ');
  const stroke = tone === 'ok' ? '#3ecf8e' : tone === 'warn' ? '#e5b567' : '#6ea8ff';
  return (
    <svg width={width} height={height} role="img" aria-label={ariaLabel} className="overflow-visible">
      <polyline points={points} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

export function HiddenDescription({ children }: { children: React.ReactNode }) {
  return <span className="sr-only">{children}</span>;
}
