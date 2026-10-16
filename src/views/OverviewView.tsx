import { useMemo } from 'react';
import { useSystem } from '@/state/SystemContext';
import { Badge, Card, EmptyState, Meter, ProgressBar, SectionTitle, StatTile, StatusDot, specialistTone } from '@/components/ui';
import type { SpecialistSummary } from '@shared/types';
import type { NavTarget } from '@/components/Sidebar';

/**
 * System overview.
 *
 * The landing view answers one question honestly: what can this machine do
 * right now? Every number below comes from a stored artefact or a recorded
 * measurement — no placeholder metrics, and an untrained specialist is shown as
 * untrained rather than as "0% score".
 */

function relativeTime(iso: string | null): string {
  if (!iso) return 'never';
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return 'unknown';
  const minutes = Math.round(diff / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

function SpecialistCard({
  specialist,
  onOpen,
}: {
  specialist: SpecialistSummary;
  onOpen: (target: NavTarget) => void;
}) {
  const active = specialist.activeVersion;
  const quality = specialist.benchmarkScore;
  return (
    <button
      type="button"
      onClick={() => onOpen({ view: 'specialist', specialistId: specialist.id })}
      className="glass reflective lift group w-full p-4 text-left"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span
            className="flex h-9 w-9 items-center justify-center rounded-xl text-xs font-semibold"
            style={{ background: `${specialist.accent}22`, color: specialist.accent, boxShadow: `inset 0 0 0 1px ${specialist.accent}44` }}
            aria-hidden
          >
            {specialist.glyph || specialist.shortName.slice(0, 2)}
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-slate-100">{specialist.name}</p>
            <p className="truncate text-2xs text-slate-500">{specialist.tagline}</p>
          </div>
        </div>
        <Badge tone={specialistTone(specialist.state)}>
          <StatusDot level={specialistTone(specialist.state)} pulse={specialist.state === 'training'} />
          {specialist.state}
        </Badge>
      </div>

      <p className="mt-3 line-clamp-2 text-xs leading-relaxed text-slate-400">{specialist.stateDetail}</p>

      <div className="mt-3 grid grid-cols-2 gap-2 text-2xs">
        <div className="glass-subtle px-2.5 py-2">
          <p className="text-slate-500">Active model</p>
          <p className="mt-0.5 font-mono text-slate-200">{active ? active.label : 'untrained'}</p>
        </div>
        <div className="glass-subtle px-2.5 py-2">
          <p className="text-slate-500">Benchmark</p>
          <p className={`mt-0.5 font-mono ${quality === null ? 'text-slate-500' : 'text-slate-200'}`}>
            {quality === null ? 'not measured' : `${(quality * 100).toFixed(1)}%`}
          </p>
        </div>
      </div>

      {quality !== null && (
        <div className="mt-3">
          <Meter label="Measured quality" value={quality} tone={quality >= 0.7 ? 'ok' : quality >= 0.5 ? 'warn' : 'error'} />
        </div>
      )}

      <div className="mt-3 flex items-center justify-between text-2xs text-slate-500">
        <span>
          {specialist.versions.length} version{specialist.versions.length === 1 ? '' : 's'} · {specialist.runCount} run
          {specialist.runCount === 1 ? '' : 's'}
        </span>
        <span className="text-accent-300 opacity-0 transition-opacity duration-300 group-hover:opacity-100">Open workspace →</span>
      </div>
    </button>
  );
}

export function OverviewView({ onOpen }: { onOpen: (target: NavTarget) => void }) {
  const { specialists, runs, runCounts, scorecards, versions, recentComparisons, trainingAvailable, methods, anyExternalAvailable, providers } =
    useSystem();

  const trained = specialists.filter((s) => s.activeVersion).length;
  const measured = useMemo(() => scorecards.filter((card) => !card.versionId.endsWith('@untrained')), [scorecards]);
  const bestQuality = useMemo(
    () => scorecards.reduce((best, card) => (card.quality > best ? card.quality : best), 0),
    [scorecards],
  );
  const provider = providers.find((p) => p.id !== 'builtin' && p.available) ?? null;

  const improvement = useMemo(() => {
    // Average quality gain of the untrained baseline vs the best measured card
    // per suite — the honest headline for "does training help here?".
    const bySuite = new Map<string, { untrained: number[]; trained: number[] }>();
    for (const card of scorecards) {
      const entry = bySuite.get(card.suiteId) ?? { untrained: [], trained: [] };
      if (card.versionId.endsWith('@untrained')) entry.untrained.push(card.quality);
      else entry.trained.push(card.quality);
      bySuite.set(card.suiteId, entry);
    }
    const deltas: number[] = [];
    for (const entry of bySuite.values()) {
      if (entry.untrained.length === 0 || entry.trained.length === 0) continue;
      deltas.push(Math.max(...entry.trained) - Math.max(...entry.untrained));
    }
    if (deltas.length === 0) return null;
    return deltas.reduce((sum, value) => sum + value, 0) / deltas.length;
  }, [scorecards]);

  const activeRun = runs.find((run) => run.status === 'running' || run.status === 'queued') ?? null;

  return (
    <div className="space-y-6">
      <section className="glass reflective relative overflow-hidden p-6">
        <div className="pointer-events-none absolute -right-24 -top-28 h-64 w-64 rounded-full bg-accent-500/20 blur-3xl animate-breathe" aria-hidden />
        <div className="relative flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-2xl">
            <p className="eyebrow">Live system</p>
            <h2 className="display-lg mt-2 text-slate-50">
              {trained} of {specialists.length || '—'} specialists trained
              {measured.length > 0 ? ` · ${measured.length} measured version(s)` : ' · nothing measured yet'}
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">
              Specialists are trained here, on this machine, and kept only when a benchmark says they are better. The numbers below
              are recorded measurements — an untrained specialist is labelled untrained rather than given a score.
            </p>
          </div>
          <div className="grid w-full max-w-md grid-cols-2 gap-3">
            <StatTile
              label="Training methods"
              value={`${trainingAvailable}/${methods.length || '—'}`}
              hint="In-process methods usable now; the external adapter path needs a Python runtime."
            />
            <StatTile
              label="Model versions"
              value={versions.length}
              hint={`${versions.filter((v) => v.status === 'candidate').length} candidate · ${versions.filter((v) => v.status === 'active').length} active`}
            />
            <StatTile
              label="Best measured quality"
              value={scorecards.length === 0 ? 'not measured' : `${(bestQuality * 100).toFixed(1)}%`}
              hint="Highest suite score across every recorded scorecard."
              tone={scorecards.length === 0 ? 'default' : bestQuality >= 0.7 ? 'ok' : 'warn'}
            />
            <StatTile
              label="Training vs baseline"
              value={improvement === null ? 'not measured' : `${improvement >= 0 ? '+' : ''}${(improvement * 100).toFixed(1)} pts`}
              hint="Average gain over the untrained baseline, per suite with both numbers."
              tone={improvement === null ? 'default' : improvement > 0 ? 'ok' : 'error'}
            />
          </div>
        </div>

        {activeRun && (
          <div className="relative mt-5 rounded-xl border border-signal-warn/25 bg-signal-warn/[0.07] px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="text-signal-warn">
                {activeRun.status === 'queued' ? 'Queued' : 'Training'}: {activeRun.methodName} · {activeRun.datasetName}
              </span>
              <span className="text-slate-400">
                {activeRun.specialistName} · {activeRun.epochs.length} epoch(s) · {Math.round(activeRun.progress * 100)}%
              </span>
            </div>
            <div className="mt-2">
              <ProgressBar value={activeRun.progress} indeterminate={activeRun.status === 'queued'} />
            </div>
          </div>
        )}
      </section>

      <div>
        <SectionTitle hint={`${specialists.length} registered`}>Specialists</SectionTitle>
        {specialists.length === 0 ? (
          <EmptyState
            title="No specialists registered"
            description="The platform ships with Coding, Research, Knowledge, Voice and General specialists. If none are listed, the backend platform layer is unavailable."
            action={
              <button type="button" className="btn-primary" onClick={() => onOpen({ view: 'add-ai' })}>
                Add a specialist
              </button>
            }
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {specialists.map((specialist) => (
              <SpecialistCard key={specialist.id} specialist={specialist} onOpen={onOpen} />
            ))}
          </div>
        )}
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Recent measurements"
          subtitle="Every scorecard records quality, task success, latency, CPU time and reliability for one model version."
          actions={
            <button type="button" className="btn-ghost" onClick={() => onOpen({ view: 'benchmarks' })}>
              Open benchmarks
            </button>
          }
        >
          {scorecards.length === 0 ? (
            <EmptyState
              title="Nothing measured yet"
              description="Run a benchmark to establish the baseline. Training runs measure the current state first, so the before/after pair is always comparable."
              action={
                <button type="button" className="btn-primary" onClick={() => onOpen({ view: 'benchmarks' })}>
                  Run a benchmark
                </button>
              }
            />
          ) : (
            <div className="space-y-2">
              {scorecards.slice(0, 6).map((card) => {
                const comparison = recentComparisons.find((entry) => entry.scorecardId === card.id);
                return (
                  <div key={card.id} className="glass-subtle tile-hover flex flex-wrap items-center justify-between gap-3 px-3.5 py-2.5">
                    <div className="min-w-0">
                      <p className="truncate text-xs font-medium text-slate-200">
                        {card.suiteName} <span className="text-slate-500">· {card.versionLabel}</span>
                      </p>
                      <p className="mt-0.5 truncate text-2xs text-slate-500">
                        {card.engine} · {relativeTime(card.createdAt)}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm text-slate-100">{(card.quality * 100).toFixed(1)}%</span>
                      <span className="text-2xs text-slate-500">
                        {card.tasksPassed}/{card.tasksTotal} tasks · p50 {card.latencyMsP50.toFixed(2)} ms
                      </span>
                      {comparison && comparison.verdict !== 'no_baseline' && (
                        <span className="flex items-center gap-1.5">
                          <Badge tone={comparison.verdict === 'improved' ? 'ok' : comparison.verdict === 'regressed' ? 'error' : 'unknown'}>
                            {comparison.verdict}
                          </Badge>
                          {comparison.baselineLabel && (
                            <span className="text-2xs text-slate-500">vs {comparison.baselineLabel}</span>
                          )}
                        </span>
                      )}
                      {card.versionId.endsWith('@untrained') && <Badge tone="unknown">baseline</Badge>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card title="Runtime honesty" subtitle="What is really available on this machine right now.">
          <div className="space-y-3 text-xs">
            <div className="glass-subtle px-3 py-2.5">
              <p className="flex items-center justify-between gap-2">
                <span className="text-slate-400">External model provider</span>
                <Badge tone={anyExternalAvailable ? 'ok' : 'warn'}>{anyExternalAvailable ? provider?.name ?? 'ready' : 'unreachable'}</Badge>
              </p>
              <p className="mt-1.5 leading-relaxed text-slate-500">
                {anyExternalAvailable
                  ? 'Escalated requests can reach a real language model.'
                  : 'No provider answered. Escalations are served by the built-in offline engine and labelled as such.'}
              </p>
            </div>
            <div className="glass-subtle px-3 py-2.5">
              <p className="flex items-center justify-between gap-2">
                <span className="text-slate-400">Training runs</span>
                <Badge tone={runCounts && runCounts.failed > 0 ? 'warn' : 'ok'}>
                  {runCounts ? `${runCounts.completed} completed · ${runCounts.failed} failed` : 'unknown'}
                </Badge>
              </p>
              <p className="mt-1.5 leading-relaxed text-slate-500">
                Runs execute in this process. Checkpoints are written to disk as real parameter files and can be promoted or rejected
                individually.
              </p>
            </div>
            <div className="glass-subtle px-3 py-2.5">
              <p className="flex items-center justify-between gap-2">
                <span className="text-slate-400">External fine-tuning</span>
                <Badge tone="warn">not configured</Badge>
              </p>
              <p className="mt-1.5 leading-relaxed text-slate-500">
                LoRA/PEFT adapter training is implemented as a path but needs Python with torch and transformers, plus a provider. It is
                reported as unavailable rather than simulated.
              </p>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
