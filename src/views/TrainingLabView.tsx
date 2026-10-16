import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '@/lib/api';
import { useSystem } from '@/state/SystemContext';
import {
  Badge,
  Card,
  Chip,
  EmptyState,
  ErrorBanner,
  ProgressBar,
  SegmentedControl,
  Sparkline,
  StatTile,
  StatusDot,
  Toggle,
  versionTone,
} from '@/components/ui';
import type { HyperparameterSpec, TrainingMethodInfo, TrainingRun } from '@shared/types';
import type { NavTarget } from '@/components/Sidebar';

/**
 * Training Lab.
 *
 * Everything here is a real training run on this machine: choose the specialist,
 * the method that suits it, the dataset, and the hyperparameters the method
 * actually consumes. Progress, epoch curves and parameter checkpoints come from
 * the run itself. When a method cannot execute here it is shown as unavailable
 * with its requirement, never as a queued job that will never start.
 */

const RUNTIME_LABEL: Record<TrainingMethodInfo['kind'], string> = {
  gradient: 'gradient descent',
  statistical: 'statistical induction',
  index: 'index construction',
  external: 'external runtime',
};

function HyperparameterInput({
  spec,
  value,
  onChange,
}: {
  spec: HyperparameterSpec;
  value: number | boolean | string;
  onChange: (next: number | boolean | string) => void;
}) {
  if (spec.type === 'boolean') {
    return (
      <Toggle
        label={spec.label}
        description={spec.description}
        checked={Boolean(value)}
        onChange={(next) => onChange(next)}
      />
    );
  }
  if (spec.type === 'enum') {
    return (
      <label className="block py-2">
        <span className="label">{spec.label}</span>
        <select className="input" value={String(value)} onChange={(event) => onChange(event.target.value)}>
          {(spec.options ?? []).map((option) => (
            <option key={option} value={option} className="bg-base-850">
              {option}
            </option>
          ))}
        </select>
        <span className="mt-1 block text-2xs leading-relaxed text-slate-500">{spec.description}</span>
      </label>
    );
  }
  const numeric = Number(value);
  return (
    <div className="py-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm text-slate-200">{spec.label}</span>
        <input
          className="w-24 rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1 text-right font-mono text-xs text-slate-100"
          type="number"
          value={Number.isFinite(numeric) ? numeric : 0}
          min={spec.min}
          max={spec.max}
          step={spec.step ?? (spec.type === 'int' ? 1 : 0.01)}
          onChange={(event) => onChange(Number(event.target.value))}
          aria-label={`${spec.label} value`}
        />
      </div>
      <input
        className="mt-2 w-full accent-accent-500"
        type="range"
        min={spec.min ?? 0}
        max={spec.max ?? 1}
        step={spec.step ?? (spec.type === 'int' ? 1 : 0.01)}
        value={Number.isFinite(numeric) ? numeric : 0}
        onChange={(event) => onChange(Number(event.target.value))}
        aria-label={spec.label}
      />
      <span className="mt-1 block text-2xs leading-relaxed text-slate-500">{spec.description}</span>
    </div>
  );
}

function RunCard({ run, onOpen }: { run: TrainingRun; onOpen: (target: NavTarget) => void }) {
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = run.status === 'running' || run.status === 'queued';
  const lastEpoch = run.epochs[run.epochs.length - 1] ?? null;

  return (
    <div className={`glass p-4 ${active ? 'ring-1 ring-signal-warn/25' : ''}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-medium text-slate-100">{run.methodName}</p>
            <Badge
              tone={run.status === 'completed' ? 'ok' : run.status === 'failed' ? 'error' : run.status === 'cancelled' ? 'unknown' : 'warn'}
            >
              {active && <StatusDot level="warn" pulse />}
              {run.status}
            </Badge>
            {run.promotion && (
              <Badge tone={run.promotion.action === 'promoted' ? 'ok' : run.promotion.action === 'kept_active' ? 'warn' : 'unknown'}>
                {run.promotion.action.replace('_', ' ')}
              </Badge>
            )}
          </div>
          <p className="mt-1 text-2xs text-slate-400">
            <button type="button" className="hover:text-accent-300" onClick={() => onOpen({ view: 'specialist', specialistId: run.specialistId })}>
              {run.specialistName}
            </button>{' '}
            · {run.datasetName} ({run.datasetSize} items) · started {run.startedAt ? new Date(run.startedAt).toLocaleTimeString() : 'queued'}
            {run.durationMs !== null ? ` · ${(run.durationMs / 1000).toFixed(1)} s` : ''}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {run.versionId && (
            <button
              type="button"
              className="btn-ghost"
              onClick={() => onOpen({ view: 'training' })}
              title={`Produced model version ${run.versionId}`}
            >
              {run.versionId.slice(-14)}
            </button>
          )}
          {active && (
            <button
              type="button"
              className="btn-danger"
              disabled={cancelling}
              onClick={async () => {
                setCancelling(true);
                try {
                  await api.cancelRun(run.id);
                } catch (e) {
                  setError(e instanceof ApiError ? e.message : String(e));
                } finally {
                  setCancelling(false);
                }
              }}
            >
              {cancelling ? 'Stopping…' : 'Cancel'}
            </button>
          )}
        </div>
      </div>

      {active && (
        <div className="mt-3">
          <ProgressBar
            value={run.progress}
            label={
              run.status === 'queued'
                ? 'waiting for the worker'
                : `${run.epochs.length} epoch(s)${lastEpoch ? ` · val acc ${(lastEpoch.valAccuracy * 100).toFixed(1)}% · loss ${lastEpoch.valLoss.toFixed(4)}` : ''}`
            }
            indeterminate={run.status === 'queued'}
          />
        </div>
      )}

      {run.epochs.length > 2 && (
        <div className="mt-3 flex flex-wrap items-start gap-6">
          <div>
            <p className="eyebrow mb-1">Validation loss</p>
            <Sparkline values={run.epochs.map((epoch) => epoch.valLoss)} ariaLabel="Validation loss per epoch" tone="warn" />
          </div>
          <div>
            <p className="eyebrow mb-1">Validation accuracy</p>
            <Sparkline values={run.epochs.map((epoch) => epoch.valAccuracy)} ariaLabel="Validation accuracy per epoch" tone="ok" />
          </div>
          <div>
            <p className="eyebrow mb-1">Checkpoints</p>
            <div className="flex flex-wrap gap-1.5">
              {run.checkpoints.length === 0 && <span className="text-2xs text-slate-500">none written yet</span>}
              {run.checkpoints.map((checkpoint) => (
                <span
                  key={checkpoint.id}
                  className={`chip ${checkpoint.best ? 'border-signal-ok/40 text-signal-ok' : 'text-slate-400'}`}
                  title={`${checkpoint.path} · ${(checkpoint.sizeBytes / 1024).toFixed(1)} kB`}
                >
                  ep {checkpoint.epoch} · {(checkpoint.valAccuracy * 100).toFixed(0)}%
                  {checkpoint.best ? ' · best' : ''}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      {run.metrics && (
        <div className="mt-3 grid grid-cols-2 gap-2 text-2xs md:grid-cols-4">
          {Object.entries(run.metrics)
            .slice(0, 8)
            .map(([key, value]) => (
              <div key={key} className="glass-subtle px-2.5 py-1.5">
                <p className="truncate text-slate-500">{key}</p>
                <p className="font-mono text-slate-200">{typeof value === 'number' ? value.toFixed(4) : String(value)}</p>
              </div>
            ))}
        </div>
      )}

      {run.promotion && (
        <p
          className={`mt-3 rounded-lg px-3 py-2 text-2xs leading-relaxed ${
            run.promotion.action === 'promoted'
              ? 'border border-signal-ok/25 bg-signal-ok/[0.07] text-signal-ok'
              : run.promotion.action === 'kept_active'
                ? 'border border-signal-warn/25 bg-signal-warn/[0.06] text-signal-warn'
                : 'border border-white/[0.06] bg-white/[0.02] text-slate-400'
          }`}
        >
          {run.promotion.reason}
        </p>
      )}
      {run.error && <p className="mt-2 text-2xs leading-relaxed text-signal-error">{run.error}</p>}
      {error && <p className="mt-2 text-2xs text-signal-error">{error}</p>}
    </div>
  );
}

export function TrainingLabView({ onOpen }: { onOpen: (target: NavTarget) => void }) {
  const { specialists, runs, runCounts, versions, methods, datasets, trainingAvailable, refreshPlatform, settings, saveSettings } =
    useSystem();

  const [tab, setTab] = useState<'configure' | 'runs' | 'versions'>('configure');
  const [specialistId, setSpecialistId] = useState<string>('');
  const [methodId, setMethodId] = useState<string>('');
  const [datasetId, setDatasetId] = useState<string>('');
  const [hyperparameters, setHyperparameters] = useState<Record<string, number | boolean | string>>({});
  const [autoPromote, setAutoPromote] = useState<boolean>(Boolean(settings?.training.autoPromote));
  const [runBenchmarkAfter, setRunBenchmarkAfter] = useState<boolean>(settings?.training.autoBenchmark ?? true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    if (!specialistId && specialists.length > 0) setSpecialistId(specialists[0].id);
  }, [specialists, specialistId]);

  const specialist = specialists.find((entry) => entry.id === specialistId) ?? null;

  const applicable = useMemo(() => {
    if (!specialist) return { methods: [] as TrainingMethodInfo[], datasets: [] as typeof datasets };
    return {
      methods: methods.filter((method) => specialist.trainingMethodIds.includes(method.id)),
      datasets: datasets.filter((dataset) => dataset.specialistId === specialist.id),
    };
  }, [specialist, methods, datasets]);

  // Keep the method/dataset choice valid whenever the specialist changes.
  useEffect(() => {
    if (!specialist) return;
    const method = applicable.methods.find((entry) => entry.id === methodId) ?? applicable.methods[0] ?? null;
    setMethodId(method ? method.id : '');
    const dataset = applicable.datasets.find((entry) => entry.id === datasetId) ?? applicable.datasets.find((entry) => entry.kind === methodKind(method)) ?? applicable.datasets[0] ?? null;
    setDatasetId(dataset ? dataset.id : '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [specialist?.id, applicable.methods.length, applicable.datasets.length]);

  const method = applicable.methods.find((entry) => entry.id === methodId) ?? null;
  const dataset = applicable.datasets.find((entry) => entry.id === datasetId) ?? null;

  // Reset hyperparameters to the method's declared defaults whenever it changes.
  useEffect(() => {
    if (!method) {
      setHyperparameters({});
      return;
    }
    const defaults: Record<string, number | boolean | string> = {};
    for (const spec of method.hyperparameters) defaults[spec.key] = spec.default;
    setHyperparameters(defaults);
  }, [method?.id]);

  const compatible = useMemo(() => {
    if (!method || !dataset) return false;
    return method.datasetKinds.includes(dataset.kind);
  }, [method, dataset]);

  const busy = runs.some((run) => run.specialistId === specialistId && (run.status === 'running' || run.status === 'queued'));

  async function start() {
    if (!method || !dataset || starting) return;
    setStarting(true);
    setError(null);
    setNotice(null);
    try {
      const run = await api.startRun({
        specialistId,
        methodId: method.id as never,
        datasetId: dataset.id,
        hyperparameters,
        autoPromote,
        runBenchmarkAfter,
      });
      setNotice(
        `Run ${run.id} queued. It will measure the current state on the matching suite first, train, then benchmark the new version and decide on promotion.`,
      );
      setTab('runs');
      void refreshPlatform();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  }

  const liveRuns = runs.filter((run) => run.status === 'running' || run.status === 'queued');
  const finishedRuns = runs.filter((run) => run.status !== 'running' && run.status !== 'queued');

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Methods usable"
          value={`${trainingAvailable}/${methods.length || '—'}`}
          hint="In-process methods run here; the external adapter path needs Python + a provider."
          tone={trainingAvailable > 0 ? 'ok' : 'warn'}
        />
        <StatTile
          label="Runs"
          value={runCounts?.total ?? 0}
          hint={runCounts ? `${runCounts.completed} completed · ${runCounts.failed} failed · ${runCounts.cancelled} cancelled` : 'no data'}
        />
        <StatTile
          label="Live now"
          value={liveRuns.length}
          tone={liveRuns.length > 0 ? 'warn' : 'default'}
          hint={liveRuns.length > 0 ? liveRuns.map((run) => run.specialistName).join(', ') : 'nothing training'}
        />
        <StatTile
          label="Model versions"
          value={versions.length}
          hint={`${versions.filter((v) => v.status === 'active').length} active · ${versions.filter((v) => v.status === 'candidate').length} candidate`}
        />
      </div>

      <SegmentedControl<'configure' | 'runs' | 'versions'>
        ariaLabel="Training Lab sections"
        value={tab}
        onChange={setTab}
        options={[
          { id: 'configure', label: 'Configure a run' },
          { id: 'runs', label: `Runs${liveRuns.length > 0 ? ` (${liveRuns.length} live)` : ''}` },
          { id: 'versions', label: 'Model versions' },
        ]}
      />

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {notice && <p className="rounded-xl border border-accent-400/25 bg-accent-400/[0.08] px-4 py-3 text-xs text-accent-200">{notice}</p>}

      {tab === 'configure' && (
        <div className="grid gap-5 lg:grid-cols-[1.15fr_1fr]">
          <Card title="What to train" subtitle="Choose the specialist, the method suited to it, and the data it learns from.">
            <label className="block">
              <span className="label">Specialist</span>
              <select className="input" value={specialistId} onChange={(event) => setSpecialistId(event.target.value)} aria-label="Specialist">
                {specialists.map((entry) => (
                  <option key={entry.id} value={entry.id} className="bg-base-850">
                    {entry.name} — {entry.activeVersion ? entry.activeVersion.label : 'untrained'} ({entry.state})
                  </option>
                ))}
              </select>
            </label>

            {specialist && (
              <div className="mt-3 space-y-2">
                <p className="text-2xs leading-relaxed text-slate-500">{specialist.stateDetail}</p>
                <div className="flex flex-wrap gap-2">
                  {applicable.methods.map((entry) => (
                    <button
                      key={entry.id}
                      type="button"
                      onClick={() => setMethodId(entry.id)}
                      aria-pressed={entry.id === methodId}
                      title={entry.description}
                      className={`glass-subtle tile-hover px-3 py-2 text-left text-2xs transition-colors ${
                        entry.id === methodId ? 'ring-1 ring-accent-400/60' : ''
                      } ${entry.available ? '' : 'opacity-60'}`}
                    >
                      <span className="flex items-center gap-1.5 text-xs text-slate-200">
                        {entry.name}
                        {!entry.available && <Badge tone="warn">unavailable</Badge>}
                      </span>
                      <span className="mt-0.5 block text-slate-500">{RUNTIME_LABEL[entry.kind]} · {entry.runtime}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {method && (
              <div className="mt-4 space-y-3">
                <div className="glass-subtle p-3">
                  <p className="text-xs leading-relaxed text-slate-300">{method.description}</p>
                  <p className="mt-2 text-2xs leading-relaxed text-slate-500">
                    <span className="text-slate-400">Artifact:</span> {method.artifactKind}
                  </p>
                  <p className="mt-1 text-2xs leading-relaxed text-slate-500">
                    <span className="text-slate-400">Honesty note:</span> {method.honesty}
                  </p>
                  {method.requirements.length > 0 && (
                    <p className="mt-1 text-2xs leading-relaxed text-slate-500">
                      <span className="text-slate-400">Requirements:</span> {method.requirements.join('; ')}
                    </p>
                  )}
                </div>

                <label className="block">
                  <span className="label">Dataset</span>
                  <select className="input" value={datasetId} onChange={(event) => setDatasetId(event.target.value)} aria-label="Dataset">
                    {applicable.datasets.map((entry) => (
                      <option key={entry.id} value={entry.id} className="bg-base-850">
                        {entry.name} — {entry.size} items · {entry.labels.length} labels
                      </option>
                    ))}
                  </select>
                </label>

                {dataset && (
                  <div className="glass-subtle p-3 text-2xs leading-relaxed text-slate-400">
                    <p>{dataset.description}</p>
                    <p className="mt-1.5 text-slate-500">
                      {dataset.provenanceDetail} · {dataset.kind}
                      {!method.datasetKinds.includes(dataset.kind) && (
                        <span className="text-signal-warn"> · this dataset does not fit the selected method</span>
                      )}
                    </p>
                  </div>
                )}

                <div>
                  <p className="eyebrow mb-1">Hyperparameters</p>
                  <div className="divide-y divide-white/[0.05]">
                    {method.hyperparameters
                      .filter((spec) => showAdvanced || !spec.advanced)
                      .map((spec) => (
                        <HyperparameterInput
                          key={spec.key}
                          spec={spec}
                          value={hyperparameters[spec.key] ?? spec.default}
                          onChange={(next) => setHyperparameters((prev) => ({ ...prev, [spec.key]: next }))}
                        />
                      ))}
                  </div>
                  {method.hyperparameters.some((spec) => spec.advanced) && (
                    <button type="button" className="btn-quiet mt-2" onClick={() => setShowAdvanced((prev) => !prev)}>
                      {showAdvanced ? 'Hide advanced settings' : `Show ${method.hyperparameters.filter((s) => s.advanced).length} advanced settings`}
                    </button>
                  )}
                </div>
              </div>
            )}
          </Card>

          <div className="space-y-5">
            <Card title="After training" subtitle="What happens when the run finishes.">
              <Toggle
                label="Measure the state before training"
                description="Runs the matching suite on the current model first, so before/after are directly comparable."
                checked={runBenchmarkAfter}
                onChange={setRunBenchmarkAfter}
              />
              <Toggle
                label="Benchmark the new version"
                description="Measures the candidate on the matching suite and records a scorecard."
                checked={runBenchmarkAfter}
                onChange={setRunBenchmarkAfter}
              />
              <Toggle
                label="Auto-promote if it wins"
                description={
                  settings
                    ? `Promotes automatically when quality improves by at least ${(settings.training.minImprovement * 100).toFixed(1)} points. Otherwise the version waits for review.`
                    : 'Promotes when the candidate beats the active version.'
                }
                checked={autoPromote}
                onChange={(next) => {
                  setAutoPromote(next);
                  if (settings) void saveSettings({ training: { ...settings.training, autoPromote: next } });
                }}
              />
              {settings && (
                <p className="mt-2 text-2xs leading-relaxed text-slate-500">
                  Promotion gate: quality must improve by {(settings.training.minImprovement * 100).toFixed(1)} points against the best
                  known baseline for this suite. A candidate that loses is kept as a version and marked a regression — it is never
                  silently moved to production.
                </p>
              )}
            </Card>

            <Card title="Start" subtitle="Runs execute in this process and stream progress live.">
              <div className="space-y-3">
                <div className="glass-subtle space-y-1 px-3 py-2.5 text-2xs text-slate-400">
                  <p className="flex items-center justify-between">
                    <span>Specialist</span>
                    <span className="text-slate-200">{specialist?.name ?? '—'}</span>
                  </p>
                  <p className="flex items-center justify-between">
                    <span>Method</span>
                    <span className="text-slate-200">{method?.name ?? '—'}</span>
                  </p>
                  <p className="flex items-center justify-between">
                    <span>Dataset</span>
                    <span className="text-slate-200">{dataset ? `${dataset.name} (${dataset.size})` : '—'}</span>
                  </p>
                  <p className="flex items-center justify-between">
                    <span>Estimated artifact</span>
                    <span className="text-slate-200">{method?.artifactKind.split('·')[0].trim() ?? '—'}</span>
                  </p>
                </div>
                {!compatible && method && dataset && (
                  <p className="rounded-lg border border-signal-warn/25 bg-signal-warn/[0.07] px-3 py-2 text-2xs text-signal-warn">
                    This dataset kind ({dataset.kind}) is not what {method.name} consumes ({method.datasetKinds.join(', ')}). The backend
                    would reject the run — pick a matching pair.
                  </p>
                )}
                {method && !method.available && (
                  <p className="rounded-lg border border-signal-warn/25 bg-signal-warn/[0.07] px-3 py-2 text-2xs text-signal-warn">
                    {method.name} cannot run in this environment. {method.requirements.join('; ')}.
                  </p>
                )}
                <button
                  type="button"
                  className="btn-primary w-full"
                  disabled={!method || !dataset || !compatible || !method.available || starting || busy}
                  onClick={() => void start()}
                >
                  {busy ? 'This specialist is already training' : starting ? 'Starting…' : 'Start training run'}
                </button>
                {busy && <p className="text-center text-2xs text-signal-warn">Wait for the current run to finish or cancel it.</p>}
              </div>
            </Card>
          </div>
        </div>
      )}

      {tab === 'runs' && (
        <div className="space-y-4">
          {runs.length === 0 ? (
            <EmptyState
              title="No training runs yet"
              description="Configure a run on the previous tab. Training is the only way a specialist gets a local model — until then it answers through the rule planner and escalates when unsure."
              action={
                <button type="button" className="btn-primary" onClick={() => setTab('configure')}>
                  Configure a run
                </button>
              }
            />
          ) : (
            <>
              {liveRuns.length > 0 && (
                <div>
                  <p className="eyebrow mb-2">Running now</p>
                  <div className="space-y-3">
                    {liveRuns.map((run) => (
                      <RunCard key={run.id} run={run} onOpen={onOpen} />
                    ))}
                  </div>
                </div>
              )}
              <div>
                <p className="eyebrow mb-2">History</p>
                <div className="space-y-3">
                  {finishedRuns.length === 0 && <p className="text-xs text-slate-500">No finished runs yet.</p>}
                  {finishedRuns.map((run) => (
                    <RunCard key={run.id} run={run} onOpen={onOpen} />
                  ))}
                </div>
              </div>
              {deleteError && <ErrorBanner message={deleteError} onDismiss={() => setDeleteError(null)} />}
            </>
          )}
        </div>
      )}

      {tab === 'versions' && (
        <Card
          title="Model versions"
          subtitle="Active versions answer requests; candidates and rejected versions are kept so the evidence trail stays intact."
        >
          {versions.length === 0 ? (
            <EmptyState title="No model versions" description="Train a specialist to create the first version." />
          ) : (
            <div className="space-y-2">
              {versions.map((version) => {
                const spec = specialists.find((entry) => entry.id === version.specialistId);
                const unusable = spec ? !spec.permissionsReady : false;
                return (
                  <div key={version.id} className="glass-subtle tile-hover p-3.5">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-xs font-medium text-slate-200">
                          {spec?.shortName ?? version.specialistId} · {version.label}{' '}
                          <span className="text-slate-500">{version.methodName}</span>
                        </p>
                        <p className="mt-0.5 truncate font-mono text-2xs text-slate-500">
                          {version.artifactKind} · {(version.sizeBytes / 1024).toFixed(1)} kB · {version.checksum.slice(0, 12)} ·{' '}
                          {new Date(version.createdAt).toLocaleString()}
                        </p>
                        {version.notes && <p className="mt-1 text-2xs leading-relaxed text-slate-400">{version.notes}</p>}
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={versionTone(version.status)}>{version.status}</Badge>
                        <button
                          type="button"
                          className="btn-ghost"
                          disabled={version.status === 'active'}
                          onClick={async () => {
                            try {
                              await api.promoteVersion(version.id, { verify: true });
                              setDeleteError(null);
                              void refreshPlatform();
                            } catch (e) {
                              setDeleteError(e instanceof ApiError ? e.message : String(e));
                            }
                          }}
                          title={
                            version.status === 'active'
                              ? 'Already active'
                              : 'Runs a verification benchmark first; a regression blocks the promotion'
                          }
                        >
                          Promote
                        </button>
                        <button
                          type="button"
                          className="btn-quiet"
                          disabled={version.status === 'active'}
                          onClick={async () => {
                            try {
                              await api.rejectVersion(version.id);
                              void refreshPlatform();
                            } catch (e) {
                              setDeleteError(e instanceof ApiError ? e.message : String(e));
                            }
                          }}
                        >
                          Reject
                        </button>
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {Object.entries(version.metrics)
                        .slice(0, 6)
                        .map(([key, value]) => (
                          <Chip key={key}>
                            {key} {typeof value === 'number' ? value.toFixed(3) : String(value)}
                          </Chip>
                        ))}
                      {unusable && <Chip title="A tool this version needs is blocked by permissions">needs permissions</Chip>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

/** Which dataset kind a method consumes, for the default selection. */
function methodKind(method: TrainingMethodInfo | null): string {
  if (!method) return '';
  return method.datasetKinds[0] ?? '';
}
