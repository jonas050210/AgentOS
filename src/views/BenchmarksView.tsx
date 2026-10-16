import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '@/lib/api';
import { useSystem } from '@/state/SystemContext';
import {
  Badge,
  Card,
  Chip,
  DeltaBar,
  EmptyState,
  ErrorBanner,
  Meter,
  SegmentedControl,
  StatTile,
  StatusDot,
  versionTone,
} from '@/components/ui';
import type { BenchmarkComparison, BenchmarkScorecard } from '@shared/types';
import type { NavTarget } from '@/components/Sidebar';

/**
 * Benchmarks.
 *
 * A scorecard is the only thing allowed to justify a promotion, so this view is
 * built around evidence: what suite ran, on which version, what each task did,
 * and how the numbers compare with the previous best. Comparisons that are not
 * meaningful (different suites, or a repeat run of the same version) are labelled
 * as such instead of being dressed up as progress.
 */

function formatMetric(metric: string, value: number): string {
  if (metric === 'quality' || metric === 'taskSuccessRate' || metric === 'reliability') return `${(value * 100).toFixed(1)}%`;
  if (metric === 'cpuMs' || metric.startsWith('latency')) return `${value.toFixed(2)} ms`;
  if (metric === 'heapDeltaMb') return `${value.toFixed(2)} MB`;
  if (metric === 'recallAt5' || metric === 'precisionAt5' || metric === 'hitRate' || metric === 'mrr') return value.toFixed(4);
  return value.toFixed(4);
}

function ScorecardCard({
  card,
  comparison,
  onSelect,
  selected,
}: {
  card: BenchmarkScorecard;
  comparison: BenchmarkComparison | null;
  onSelect: () => void;
  selected: boolean;
}) {
  const verdict = comparison?.verdict ?? null;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`glass-subtle tile-hover w-full p-3.5 text-left transition-shadow ${selected ? 'ring-1 ring-accent-400/60' : ''}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-xs font-medium text-slate-200">{card.suiteName}</p>
          <p className="mt-0.5 truncate text-2xs text-slate-500">
            <span className="text-slate-400">{card.specialistName}</span> · {card.versionLabel} ·{' '}
            {new Date(card.createdAt).toLocaleString()}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {verdict && verdict !== 'no_baseline' && (
            <Badge tone={verdict === 'improved' ? 'ok' : verdict === 'regressed' ? 'error' : 'unknown'}>{verdict}</Badge>
          )}
          {card.versionId.endsWith('@untrained') && <Badge tone="unknown">baseline</Badge>}
        </div>
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-4 text-2xs text-slate-400">
        <span className="font-mono text-sm text-slate-100">{(card.quality * 100).toFixed(1)}%</span>
        <span>
          {card.tasksPassed}/{card.tasksTotal} tasks
        </span>
        <span>p50 {card.latencyMsP50.toFixed(2)} ms</span>
        <span>p95 {card.latencyMsP95.toFixed(2)} ms</span>
        <span>reliability {(card.reliability * 100).toFixed(0)}%</span>
        <span className="text-slate-600">{card.engine}</span>
      </div>
    </button>
  );
}

export function BenchmarksView({ onOpen }: { onOpen: (target: NavTarget) => void }) {
  const { specialists, suites, scorecards: contextCards, refreshPlatform, versions } = useSystem();
  const [tab, setTab] = useState<'run' | 'results'>('results');
  const [suiteId, setSuiteId] = useState('');
  const [specialistId, setSpecialistId] = useState('');
  const [versionId, setVersionId] = useState('');
  const [repeats, setRepeats] = useState(1);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cards, setCards] = useState<BenchmarkScorecard[]>(contextCards);
  const [comparisons, setComparisons] = useState<Record<string, BenchmarkComparison>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<BenchmarkScorecard | null>(null);

  useEffect(() => {
    setCards(contextCards);
    if (!selectedId && contextCards.length > 0) setSelectedId(contextCards[0].id);
  }, [contextCards, selectedId]);

  // Load the full scorecard history together with the baseline each card was
  // measured against, so the comparison badges and the counters below reflect
  // recorded evidence rather than a card sitting on its own.
  useEffect(() => {
    let cancelled = false;
    void api
      .scorecards({ limit: 100 })
      .then((list) => {
        if (cancelled) return;
        if (list.scorecards.length > 0) setCards(list.scorecards);
        if (list.comparisons) setComparisons((prev) => ({ ...list.comparisons, ...prev }));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [contextCards]);

  useEffect(() => {
    if (!suiteId && suites.length > 0) setSuiteId(suites[0].id);
  }, [suites, suiteId]);

  const suite = suites.find((entry) => entry.id === suiteId) ?? null;
  const eligible = useMemo(
    () => specialists.filter((entry) => (suite ? entry.benchmarkSuiteIds.includes(suite.id) : true)),
    [specialists, suite],
  );

  useEffect(() => {
    if (eligible.length === 0) return;
    if (!eligible.some((entry) => entry.id === specialistId)) setSpecialistId(eligible[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suiteId, eligible.length]);

  const specialist = specialists.find((entry) => entry.id === specialistId) ?? null;
  const specialistVersions = versions.filter((version) => version.specialistId === specialistId);

  // Load the comparison for whichever scorecard is selected.
  useEffect(() => {
    if (!selectedId) return;
    if (comparisons[selectedId]) return;
    void api
      .compare(selectedId)
      .then((comparison) => setComparisons((prev) => ({ ...prev, [selectedId]: comparison })))
      .catch(() => undefined);
  }, [selectedId, comparisons]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    void api
      .scorecard(selectedId)
      .then(setDetail)
      .catch(() => setDetail(null));
  }, [selectedId]);

  async function run() {
    if (!suite || !specialistId || running) return;
    setRunning(true);
    setError(null);
    setNotice(null);
    try {
      const card = await api.runBenchmark({
        suiteId: suite.id as never,
        specialistId,
        versionId: versionId || undefined,
        repeats,
      });
      setCards((prev) => [card, ...prev.filter((entry) => entry.id !== card.id)]);
      setSelectedId(card.id);
      setTab('results');
      setNotice(
        `${suite.name} measured ${card.versionLabel}: ${(card.quality * 100).toFixed(1)}% quality, ${card.tasksPassed}/${card.tasksTotal} tasks passed in ${card.durationMs} ms.`,
      );
      void refreshPlatform();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  const comparison = selectedId ? comparisons[selectedId] ?? null : null;
  const bestBySuite = useMemo(() => {
    const map = new Map<string, number>();
    for (const card of cards) {
      if (card.versionId.endsWith('@untrained')) continue;
      map.set(card.suiteId, Math.max(map.get(card.suiteId) ?? 0, card.quality));
    }
    return map;
  }, [cards]);

  const improved = cards.filter((card) => comparisons[card.id]?.verdict === 'improved').length;
  const regressed = cards.filter((card) => comparisons[card.id]?.verdict === 'regressed').length;

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Suites" value={suites.length} hint="Held-out task sets, one per specialist capability." />
        <StatTile label="Scorecards" value={cards.length} hint="Every recorded measurement, including untrained baselines." />
        <StatTile
          label="Improvements"
          value={improved}
          tone={improved > 0 ? 'ok' : 'default'}
          hint="Comparisons where a candidate beat the version it replaced."
        />
        <StatTile
          label="Regressions"
          value={regressed}
          tone={regressed > 0 ? 'error' : 'default'}
          hint="Candidates that came out worse — they are kept as versions and never promoted silently."
        />
      </div>

      <SegmentedControl<'run' | 'results'>
        ariaLabel="Benchmark sections"
        value={tab}
        onChange={setTab}
        options={[
          { id: 'run', label: 'Run a benchmark' },
          { id: 'results', label: 'Results & comparisons' },
        ]}
      />

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {notice && <p className="rounded-xl border border-accent-400/25 bg-accent-400/[0.08] px-4 py-3 text-xs text-accent-200">{notice}</p>}

      {tab === 'run' && (
        <div className="grid gap-5 lg:grid-cols-[1.1fr_1fr]">
          <Card title="Measure a model version" subtitle="Runs the suite on your machine and records a scorecard.">
            <div className="space-y-3">
              <label className="block">
                <span className="label">Suite</span>
                <select className="input" value={suiteId} onChange={(event) => setSuiteId(event.target.value)} aria-label="Suite">
                  {suites.map((entry) => (
                    <option key={entry.id} value={entry.id} className="bg-base-850">
                      {entry.name} — {entry.taskCount} tasks
                    </option>
                  ))}
                </select>
              </label>

              {suite && (
                <div className="glass-subtle p-3 text-2xs leading-relaxed text-slate-400">
                  <p>{suite.description}</p>
                  <p className="mt-1.5 text-slate-500">
                    <span className="text-slate-400">Scorer:</span> {suite.scorer}
                  </p>
                  <p className="mt-1 text-slate-500">
                    <span className="text-slate-400">Provenance:</span> {suite.provenance}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {suite.metrics.map((metric) => (
                      <Chip key={metric}>{metric}</Chip>
                    ))}
                  </div>
                </div>
              )}

              <label className="block">
                <span className="label">Specialist</span>
                <select className="input" value={specialistId} onChange={(event) => setSpecialistId(event.target.value)} aria-label="Benchmark specialist">
                  {eligible.map((entry) => (
                    <option key={entry.id} value={entry.id} className="bg-base-850">
                      {entry.name} — {entry.activeVersion ? entry.activeVersion.label : 'untrained'}
                    </option>
                  ))}
                </select>
                {eligible.length === 0 && <span className="mt-1 block text-2xs text-signal-warn">No specialist declares this suite.</span>}
              </label>

              <label className="block">
                <span className="label">Model version</span>
                <select className="input" value={versionId} onChange={(event) => setVersionId(event.target.value)} aria-label="Model version">
                  <option value="" className="bg-base-850">
                    Active version {specialist?.activeVersion ? `(${specialist.activeVersion.label})` : '(none — measures the untrained baseline)'}
                  </option>
                  {specialistVersions.map((version) => (
                    <option key={version.id} value={version.id} className="bg-base-850">
                      {version.label} — {version.status} · {version.methodName}
                    </option>
                  ))}
                </select>
                <span className="mt-1 block text-2xs leading-relaxed text-slate-500">
                  Measuring a candidate directly is how a version is checked before promotion.
                </span>
              </label>

              <label className="block">
                <span className="label">Repeats</span>
                <input
                  type="number"
                  min={1}
                  max={5}
                  className="input"
                  value={repeats}
                  onChange={(event) => setRepeats(Math.max(1, Math.min(5, Number(event.target.value) || 1)))}
                  aria-label="Repeats"
                />
                <span className="mt-1 block text-2xs leading-relaxed text-slate-500">
                  More repeats average out timing noise. Quality is measured on the same held-out tasks each time.
                </span>
              </label>

              <button type="button" className="btn-primary w-full" onClick={() => void run()} disabled={running || !suite || !specialistId}>
                {running ? 'Measuring…' : 'Run benchmark'}
              </button>
            </div>
          </Card>

          <Card title="How a promotion is decided" subtitle="The gate is a comparison, not a threshold on a single number.">
            <ol className="space-y-3 text-xs leading-relaxed text-slate-400">
              <li className="glass-subtle p-3">
                <span className="text-slate-200">1 · Baseline</span>
                <p className="mt-1">
                  The active version's latest scorecard for this suite is the baseline. With nothing active, the untrained baseline
                  scorecard (rule policy only) is used — that is why training always measures the current state first.
                </p>
              </li>
              <li className="glass-subtle p-3">
                <span className="text-slate-200">2 · Comparison</span>
                <p className="mt-1">
                  Quality, task success, reliability, latency and resource use are compared row by row. A regression has to be larger
                  than the noise thresholds (2 quality points, 25% latency, 50% resource) before it counts as one.
                </p>
              </li>
              <li className="glass-subtle p-3">
                <span className="text-slate-200">3 · Decision</span>
                <p className="mt-1">
                  Better by the configured margin → candidate becomes active. Worse → the better model stays active and the candidate is
                  kept as a version, marked as a regression. Between the two → it waits for review.
                </p>
              </li>
            </ol>
          </Card>
        </div>
      )}

      {tab === 'results' && (
        <div className="grid gap-5 lg:grid-cols-[1fr_1.15fr]">
          <div className="space-y-3">
            {cards.length === 0 ? (
              <EmptyState
                title="No measurements yet"
                description="Run a benchmark to create the first scorecard. Training runs also record one automatically when the matching suite exists."
                action={
                  <button type="button" className="btn-primary" onClick={() => setTab('run')}>
                    Run a benchmark
                  </button>
                }
              />
            ) : (
              cards.map((card) => (
                <ScorecardCard
                  key={card.id}
                  card={card}
                  comparison={comparisons[card.id] ?? null}
                  selected={card.id === selectedId}
                  onSelect={() => setSelectedId(card.id)}
                />
              ))
            )}
          </div>

          <div className="space-y-5">
            {detail && (
              <>
                <Card
                  title={detail.suiteName}
                  subtitle={`${detail.specialistName} · ${detail.versionLabel} · ${new Date(detail.createdAt).toLocaleString()}`}
                  actions={
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => onOpen({ view: 'specialist', specialistId: detail.specialistId })}
                    >
                      Open workspace
                    </button>
                  }
                >
                  <div className="grid gap-3 sm:grid-cols-3">
                    <StatTile
                      label="Quality"
                      value={`${(detail.quality * 100).toFixed(1)}%`}
                      progress={detail.quality}
                      tone={detail.quality >= 0.7 ? 'ok' : detail.quality >= 0.5 ? 'warn' : 'error'}
                      hint={`best recorded for this suite: ${(((bestBySuite.get(detail.suiteId) ?? detail.quality) * 100)).toFixed(1)}%`}
                    />
                    <StatTile label="Tasks passed" value={`${detail.tasksPassed}/${detail.tasksTotal}`} hint="Failed tasks and their errors are listed below, task by task." />
                    <StatTile
                      label="Latency p50 / p95"
                      value={`${detail.latencyMsP50.toFixed(2)} / ${detail.latencyMsP95.toFixed(2)} ms`}
                      hint={`CPU ${detail.cpuMs.toFixed(1)} ms · heap ${detail.heapDeltaMb.toFixed(2)} MB`}
                    />
                  </div>

                  <div className="mt-4 space-y-2">
                    <Meter label="Task success rate" value={detail.taskSuccessRate} />
                    <Meter label="Reliability (no errors)" value={detail.reliability} tone={detail.reliability < 1 ? 'warn' : 'ok'} />
                    {Object.entries(detail.metrics)
                      .filter(([key]) =>
                        ['accuracy', 'macroF1', 'recallAt5', 'mrr', 'precisionAt5', 'hitRate', 'holdoutAccuracy', 'validationExactMatch', 'coverage', 'consistency'].includes(key),
                      )
                      .map(([key, value]) => (
                        <Meter key={key} label={key} value={value} />
                      ))}
                    <div className="flex flex-wrap gap-2 pt-1 text-2xs">
                      <Chip>head answered {detail.metrics.headAnswered ?? 0}</Chip>
                      <Chip>rule policy {detail.metrics.policyAnswered ?? 0}</Chip>
                      <Chip>escalated {detail.metrics.escalated ?? 0}</Chip>
                      <Chip>errors {detail.failures}</Chip>
                    </div>
                    <p className="text-2xs leading-relaxed text-slate-500">Engine: {detail.engine}</p>
                  </div>
                </Card>

                <Card
                  title="Comparison"
                  subtitle={
                    comparison
                      ? comparison.summary
                      : 'No baseline scorecard for this suite yet — this result becomes the reference.'
                  }
                >
                  {!comparison && <p className="text-xs text-slate-500">Loading comparison…</p>}
                  {comparison && comparison.rows.length === 0 && (
                    <p className="text-xs leading-relaxed text-slate-400">
                      There is nothing to compare against yet. Train this specialist or run the suite on another version, then measure
                      again — the platform keeps every scorecard so the pair can always be reconstructed.
                    </p>
                  )}
                  {comparison && comparison.rows.length > 0 && (
                    <div className="divide-y divide-white/[0.05]">
                      {comparison.rows.map((row) => (
                        <DeltaBar
                          key={row.metric}
                          label={row.label}
                          candidate={row.candidate}
                          baseline={row.baseline}
                          direction={row.direction}
                          deltaPct={row.deltaPct}
                          regression={row.regression}
                          format={(value) => formatMetric(row.metric, value)}
                        />
                      ))}
                    </div>
                  )}
                  {comparison && comparison.tradeoffs.length > 0 && (
                    <p className="mt-3 text-2xs leading-relaxed text-signal-warn">
                      Quality decides the verdict. This version pays for its score on {comparison.tradeoffs.join(', ')} — cheaper to keep
                      only if that cost is acceptable for the task.
                    </p>
                  )}
                </Card>

                <Card title="Task-by-task" subtitle="Every held-out task, what the specialist answered and which mechanism answered it.">
                  <div className="max-h-[420px] space-y-1.5 overflow-y-auto pr-1">
                    {detail.taskResults.map((task) => (
                      <div key={task.taskId} className="glass-subtle px-3 py-2">
                        <div className="flex items-start justify-between gap-3">
                          <p className="min-w-0 flex-1 truncate text-2xs text-slate-300">{task.inputPreview}</p>
                          <div className="flex shrink-0 items-center gap-2">
                            <span className="font-mono text-[10px] text-slate-500">{task.latencyMs.toFixed(2)} ms</span>
                            <span className="flex items-center gap-1 text-[10px] text-slate-400">
                              <StatusDot level={task.correct ? 'ok' : task.error ? 'error' : 'warn'} />
                              {task.source}
                            </span>
                          </div>
                        </div>
                        <p className="mt-1 grid grid-cols-2 gap-2 font-mono text-[10px]">
                          <span className="truncate text-slate-500">expected: <span className="text-slate-400">{task.expected}</span></span>
                          <span className={`truncate ${task.correct ? 'text-signal-ok' : 'text-signal-warn'}`}>
                            got: {task.error ? task.error : task.actual || '—'}
                          </span>
                        </p>
                      </div>
                    ))}
                  </div>
                </Card>
              </>
            )}

            {!detail && cards.length > 0 && <p className="text-xs text-slate-500">Select a scorecard to inspect it.</p>}
          </div>
        </div>
      )}

      {contextCards.some((card) => versionTone(versionOf(versions, card.versionId)) === 'warn') && (
        <p className="text-2xs text-slate-500">
          Versions marked <span className="text-signal-warn">candidate</span> were produced by training but have not been active yet.
        </p>
      )}
    </div>
  );
}

function versionOf(versions: Array<{ id: string; status: 'candidate' | 'active' | 'archived' | 'rejected' }>, versionId: string) {
  return versions.find((version) => version.id === versionId)?.status ?? 'archived';
}
