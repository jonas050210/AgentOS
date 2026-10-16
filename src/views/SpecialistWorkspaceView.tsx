import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '@/lib/api';
import { useSystem } from '@/state/SystemContext';
import {
  Badge,
  Card,
  Chip,
  EmptyState,
  ErrorBanner,
  KeyValue,
  Meter,
  ProgressBar,
  SegmentedControl,
  Sparkline,
  Spinner,
  StatTile,
  StatusDot,
  Toggle,
  specialistTone,
  versionTone,
} from '@/components/ui';
import type { SpecialistSummary, SpecialistTestResult } from '@shared/types';
import type { NavTarget } from '@/components/Sidebar';

/**
 * A workspace per specialist.
 *
 * The layout is shared so the platform stays predictable, but the content is
 * specific: a task-head specialist shows decision confidence and tool choices, a
 * retrieval specialist shows ranked passages, a voice specialist shows the
 * normalisation pipeline. Nothing is faked for specialists that have no model.
 */

type Tab = 'overview' | 'bench' | 'training' | 'settings';

function stateSentence(specialist: SpecialistSummary): string {
  if (specialist.state === 'untrained') {
    return 'No trained model version yet. Requests fall back to the deterministic rule planner and are escalated when nothing matches. Train it in the Training Lab to give it a local model.';
  }
  if (specialist.state === 'degraded') {
    return `The active model version needs attention: ${specialist.stateDetail}`;
  }
  if (specialist.state === 'training') return `A training run is in progress: ${specialist.stateDetail}`;
  return specialist.stateDetail;
}

function Bench({ specialist, onChanged }: { specialist: SpecialistSummary; onChanged: () => void }) {
  const [input, setInput] = useState('');
  const [result, setResult] = useState<SpecialistTestResult | null>(null);
  const [voiceText, setVoiceText] = useState('');
  const [normalized, setNormalized] = useState<{ text: string; engine: string; lexiconEntries: number; applied: Array<{ from: string; to: string }> } | null>(null);
  const [normalizing, setNormalizing] = useState(false);
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const isVoice = specialist.category === 'voice';

  async function run(saveAsExample: boolean) {
    const text = input.trim();
    if (!text || running) return;
    setRunning(true);
    setError(null);
    setNotice(null);
    try {
      const response = await api.testSpecialist(specialist.id, { input: text, saveAsExample });
      setResult(response);
      if (saveAsExample) {
        setNotice(
          response.savedExampleId
            ? `Saved as a training example for ${specialist.shortName || specialist.name}. It will be included in the next run.`
            : 'The input was not saved — the specialist could not produce a label to learn from.',
        );
        onChanged();
      }
      if (isVoice) {
        setNormalized(null);
        setVoiceText(text);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setRunning(false);
      setSaving(false);
    }
  }

  async function normalize() {
    if (!voiceText.trim() || normalizing) return;
    setNormalizing(true);
    setError(null);
    try {
      setNormalized(await api.normalize(specialist.id, voiceText));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setNormalizing(false);
    }
  }

  return (
    <div className="space-y-5">
      <Card
        title="Test bench"
        subtitle="Run one input through the active model and inspect the decision — the same code path the assistant and the benchmarks use."
        actions={
          <Badge tone={specialist.activeVersion ? 'ok' : 'unknown'}>
            {specialist.activeVersion ? `active ${specialist.activeVersion.label}` : 'no active version'}
          </Badge>
        }
      >
        <div className="flex flex-col gap-2 sm:flex-row">
          <textarea
            className="input min-h-[44px] flex-1 resize-none"
            rows={2}
            value={input}
            placeholder={
              isVoice
                ? 'Type text to route through the voice pipeline, e.g. “remember that the API is on port 4317”'
                : 'Ask the specialist something, e.g. “what is 128 * 4” or “read package.json”'
            }
            onChange={(event) => setInput(event.target.value)}
            aria-label="Test input"
          />
          <div className="flex gap-2">
            <button type="button" className="btn-primary" onClick={() => void run(false)} disabled={running || !input.trim()}>
              {running ? 'Running…' : 'Run'}
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setSaving(true);
                void run(true);
              }}
              disabled={running || !input.trim()}
              title="Run the input and store it as a labelled training example"
            >
              {saving ? 'Saving…' : 'Run + save example'}
            </button>
          </div>
        </div>

        {error && <div className="mt-3">{<ErrorBanner message={error} onDismiss={() => setError(null)} />}</div>}
        {notice && <p className="mt-3 rounded-lg border border-accent-400/25 bg-accent-400/[0.08] px-3 py-2 text-xs text-accent-200">{notice}</p>}

        {result && (
          <div className="mt-4 space-y-3">
            <div className="grid gap-3 md:grid-cols-4">
              <StatTile label="Engine" value={result.versionLabel ?? 'untrained'} hint={result.engine} />
              <StatTile
                label="Decision"
                value={result.predicted ?? 'no local answer'}
                hint={result.confidence === null ? 'no model confidence for this path' : 'model confidence'}
              />
              <StatTile
                label="Confidence"
                value={result.confidence === null ? '—' : `${(result.confidence * 100).toFixed(1)}%`}
                tone={result.confidence !== null && result.confidence < 0.5 ? 'warn' : 'default'}
                hint="Below the calibrated threshold the request escalates instead of guessing."
                progress={result.confidence ?? null}
              />
              <StatTile
                label="Latency"
                value={`${result.latencyMs.toFixed(1)} ms`}
                hint={`answered by ${result.answeredBy}`}
              />
            </div>

            {result.probabilities.length > 1 && (
              <div className="glass-subtle p-3">
                <p className="eyebrow mb-2">Decision probabilities</p>
                <div className="space-y-2">
                  {result.probabilities.slice(0, 6).map((entry) => (
                    <Meter key={entry.label} label={entry.label} value={entry.probability} tone={entry.label === result.predicted ? 'accent' : 'ok'} />
                  ))}
                </div>
              </div>
            )}

            {result.retrieved.length > 0 && (
              <div className="glass-subtle p-3">
                <p className="eyebrow mb-2">Retrieved passages</p>
                <ol className="space-y-1.5 text-xs">
                  {result.retrieved.map((hit, index) => (
                    <li key={hit.id} className="flex items-center justify-between gap-3">
                      <span className="truncate text-slate-300">
                        {index + 1}. {hit.title}
                      </span>
                      <span className="font-mono text-slate-500">{hit.id} · {hit.score.toFixed(2)}</span>
                    </li>
                  ))}
                </ol>
              </div>
            )}

            <div className="glass-subtle p-3">
              <p className="eyebrow mb-2">Answer</p>
              <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-slate-200">{result.answer}</pre>
              {result.providerError && (
                <p className="mt-2 text-2xs text-signal-error">
                  The provider could not be reached ({result.providerError}) — no model answered.
                </p>
              )}
            </div>

            <div className="glass-subtle p-3">
              <p className="eyebrow mb-2">Execution trace</p>
              <div className="space-y-1">
                {result.trace.map((step, index) => (
                  <div key={`${step.step}_${index}`} className="flex items-start gap-2 text-2xs">
                    <span className="mt-1">
                      <StatusDot level={step.status === 'ok' ? 'ok' : step.status === 'skipped' ? 'unknown' : step.status} />
                    </span>
                    <span className="w-28 font-mono text-slate-500">{step.step}</span>
                    <span className="flex-1 text-slate-400">{step.detail}</span>
                    {step.durationMs !== undefined && <span className="font-mono text-slate-600">{step.durationMs.toFixed(1)} ms</span>}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </Card>

      {isVoice && (
        <Card
          title="Speech normalisation"
          subtitle="The output path for spoken text: learned abbreviations expand first, then the deterministic rules handle numbers, symbols and cleanup."
        >
          <textarea
            className="input min-h-[80px] resize-none"
            value={voiceText}
            placeholder="Paste text to normalise, e.g. “The API responds in 250 ms for $15 — see README.md”"
            onChange={(event) => setVoiceText(event.target.value)}
            aria-label="Text to normalise"
          />
          <div className="mt-2 flex items-center gap-3">
            <button type="button" className="btn-primary" onClick={() => void normalize()} disabled={normalizing || !voiceText.trim()}>
              {normalizing ? 'Normalising…' : 'Normalise'}
            </button>
            {normalized && <span className="text-2xs text-slate-500">{normalized.engine}</span>}
          </div>
          {normalized && (
            <div className="mt-4 space-y-3">
              <div className="glass-subtle p-3">
                <p className="eyebrow mb-1.5">Spoken form</p>
                <p className="text-sm leading-relaxed text-slate-200">{normalized.text}</p>
              </div>
              <div className="glass-subtle p-3">
                <p className="eyebrow mb-1.5">Learned expansions applied ({normalized.applied.length})</p>
                {normalized.applied.length === 0 ? (
                  <p className="text-xs text-slate-500">
                    None of the {normalized.lexiconEntries} learned terms appear in this text — the rules did the work.
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {normalized.applied.map((entry) => (
                      <span key={entry.from} className="font-mono text-2xs text-slate-300">
                        {entry.from} → {entry.to}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

function TrainingTab({ specialist, onOpen }: { specialist: SpecialistSummary; onOpen: (target: NavTarget) => void }) {
  const { runs, scorecards, datasets, methods } = useSystem();
  const specialistRuns = runs.filter((run) => run.specialistId === specialist.id);
  const cards = scorecards.filter((card) => card.specialistId === specialist.id);
  const applicable = datasets.filter((dataset) => dataset.specialistId === specialist.id);
  const methodNames = new Map(methods.map((method) => [method.id, method]));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-slate-400">
          {applicable.length} dataset{applicable.length === 1 ? '' : 's'} ·{' '}
          {specialist.trainingMethodIds.map((id) => methodNames.get(id as never)?.name ?? id).join(', ') || 'no method'}
        </p>
        <button type="button" className="btn-primary" onClick={() => onOpen({ view: 'training' })}>
          Open Training Lab
        </button>
      </div>

      <Card title="Training history" subtitle="Every run keeps its epochs, checkpoints, metrics and promotion decision.">
        {specialistRuns.length === 0 ? (
          <EmptyState
            title="This specialist has never been trained here"
            description="Use the Training Lab to start a run. The platform measures the current state first, so the before/after comparison exists from the first run."
            action={
              <button type="button" className="btn-primary" onClick={() => onOpen({ view: 'training' })}>
                Start a training run
              </button>
            }
          />
        ) : (
          <div className="space-y-3">
            {specialistRuns.map((run) => (
              <div key={run.id} className="glass-subtle p-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium text-slate-200">
                      {run.methodName} <span className="text-slate-500">· {run.datasetName}</span>
                    </p>
                    <p className="mt-0.5 font-mono text-2xs text-slate-500">
                      {run.id} · {new Date(run.startedAt ?? run.finishedAt ?? Date.now()).toLocaleString()}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge
                      tone={
                        run.status === 'completed' ? 'ok' : run.status === 'failed' ? 'error' : run.status === 'cancelled' ? 'unknown' : 'warn'
                      }
                    >
                      {run.status}
                    </Badge>
                    {run.promotion && <Badge tone={run.promotion.action === 'promoted' ? 'ok' : 'unknown'}>{run.promotion.action}</Badge>}
                  </div>
                </div>

                {(run.status === 'running' || run.status === 'queued') && (
                  <div className="mt-2.5">
                    <ProgressBar value={run.progress} label={`${run.epochs.length} epoch(s)`} indeterminate={run.status === 'queued'} />
                  </div>
                )}

                {run.epochs.length > 2 && (
                  <div className="mt-3 flex flex-wrap items-center gap-6">
                    <div>
                      <p className="eyebrow mb-1">Validation loss</p>
                      <Sparkline values={run.epochs.map((epoch) => epoch.valLoss)} ariaLabel="Validation loss per epoch" tone="warn" />
                    </div>
                    <div>
                      <p className="eyebrow mb-1">Validation accuracy</p>
                      <Sparkline values={run.epochs.map((epoch) => epoch.valAccuracy)} ariaLabel="Validation accuracy per epoch" tone="ok" />
                    </div>
                    <div className="text-2xs text-slate-500">
                      <p>{run.checkpoints.length} checkpoint(s)</p>
                      <p>{run.hyperparameters.epochs ? `${run.epochs.length}/${run.hyperparameters.epochs} epochs` : `${run.epochs.length} epochs`}</p>
                    </div>
                  </div>
                )}

                {run.metrics && (
                  <div className="mt-3 grid grid-cols-2 gap-2 text-2xs md:grid-cols-4">
                    {Object.entries(run.metrics)
                      .filter(([key]) => !['trainSize', 'validationSize', 'holdoutSize', 'labels', 'parameters'].includes(key))
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
                        : 'border border-white/[0.06] bg-white/[0.02] text-slate-400'
                    }`}
                  >
                    {run.promotion.reason}
                  </p>
                )}
                {run.error && <p className="mt-2 text-2xs text-signal-error">{run.error}</p>}
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="Versions & measurements" subtitle="A version only becomes active when the evidence supports it.">
        {specialist.versions.length === 0 ? (
          <p className="text-xs text-slate-500">No stored model versions for this specialist.</p>
        ) : (
          <div className="space-y-2">
            {specialist.versions.map((version) => {
              const card = cards.find((entry) => entry.versionId === version.id);
              return (
                <div key={version.id} className="glass-subtle tile-hover flex flex-wrap items-center justify-between gap-3 px-3.5 py-2.5">
                  <div className="min-w-0">
                    <p className="truncate text-xs text-slate-200">
                      {version.label} <span className="text-slate-500">· {version.methodName}</span>
                    </p>
                    <p className="mt-0.5 truncate font-mono text-2xs text-slate-500">
                      {version.artifactKind} · {(version.sizeBytes / 1024).toFixed(1)} kB · {version.checksum.slice(0, 12)}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge tone={versionTone(version.status)}>{version.status}</Badge>
                    {card && <span className="font-mono text-2xs text-slate-300">{(card.quality * 100).toFixed(1)}% quality</span>}
                    {!card && <span className="text-2xs text-slate-500">not measured</span>}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

function SettingsTab({ specialist, onChanged }: { specialist: SpecialistSummary; onChanged: () => void }) {
  const { saveSettings, settings } = useSystem();
  const [draft, setDraft] = useState({
    name: specialist.name,
    tagline: specialist.tagline,
    description: specialist.description,
    shortName: specialist.shortName,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setDraft({ name: specialist.name, tagline: specialist.tagline, description: specialist.description, shortName: specialist.shortName });
  }, [specialist.id, specialist.name, specialist.tagline, specialist.description, specialist.shortName]);

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await api.updateSpecialist(specialist.id, draft);
      setSaved(true);
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card title="Identity" subtitle="How this specialist presents itself across the platform.">
        <div className="space-y-3">
          <label className="block">
            <span className="label">Display name</span>
            <input className="input" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
          </label>
          <label className="block">
            <span className="label">Short name</span>
            <input className="input" value={draft.shortName} onChange={(event) => setDraft({ ...draft, shortName: event.target.value })} />
          </label>
          <label className="block">
            <span className="label">Tagline</span>
            <input className="input" value={draft.tagline} onChange={(event) => setDraft({ ...draft, tagline: event.target.value })} />
          </label>
          <label className="block">
            <span className="label">Description</span>
            <textarea
              className="input min-h-[90px] resize-none"
              value={draft.description}
              onChange={(event) => setDraft({ ...draft, description: event.target.value })}
            />
          </label>
          {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
          {saved && <p className="text-2xs text-signal-ok">Saved. The workspace and sidebar pick the change up on the next refresh.</p>}
          <button type="button" className="btn-primary" onClick={() => void save()} disabled={saving}>
            {saving ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </Card>

      <div className="space-y-5">
        <Card title="Model configuration" subtitle="Applied when this specialist escalates to a model provider.">
          <KeyValue label="System prompt">{specialist.systemPrompt}</KeyValue>
          <KeyValue label="Temperature">{specialist.params.temperature}</KeyValue>
          <KeyValue label="Max tokens">{specialist.params.maxTokens}</KeyValue>
          <KeyValue label="Top-p">{specialist.params.topP}</KeyValue>
          <KeyValue label="Base model for fine-tuning">{specialist.activeVersion?.baseModel ?? 'none (trained locally)'}</KeyValue>
        </Card>

        <Card title="Global routing applies here too" subtitle="Set once in Settings; it changes how every specialist decides.">
          <Toggle
            label="Prefer trained heads"
            description="When off, requests skip the local model and escalate."
            checked={Boolean(settings?.routing.preferTrainedHeads)}
            onChange={(next) => void saveSettings({ routing: { ...settings!.routing, preferTrainedHeads: next } })}
          />
          <Toggle
            label="Allow rule fallback"
            description="Lets the deterministic planner answer when the model is unsure."
            checked={Boolean(settings?.routing.allowRuleFallback)}
            onChange={(next) => void saveSettings({ routing: { ...settings!.routing, allowRuleFallback: next } })}
          />
          <Toggle
            label="Auto-benchmark after training"
            description="Measures the new version on the matching suite and decides on promotion."
            checked={Boolean(settings?.training.autoBenchmark)}
            onChange={(next) => void saveSettings({ training: { ...settings!.training, autoBenchmark: next } })}
          />
        </Card>
      </div>
    </div>
  );
}

export function SpecialistWorkspaceView({ specialistId, onOpen }: { specialistId: string; onOpen: (target: NavTarget) => void }) {
  const { specialists, specialist, refreshPlatform, scorecards, datasets, runs } = useSystem();
  const [tab, setTab] = useState<Tab>('overview');
  const [error, setError] = useState<string | null>(null);
  const [examples, setExamples] = useState<Array<{ id: string; text: string; label: string; createdAt: string; source: string }>>([]);

  const current = specialist(specialistId) ?? specialists.find((entry) => entry.id === specialistId) ?? null;

  useEffect(() => {
    if (!current) return;
    void api
      .examples(current.id)
      .then((response) => setExamples(response.examples))
      .catch(() => setExamples([]));
  }, [current?.id, runs.length]);

  const metrics = useMemo(() => {
    if (!current) return null;
    const cards = scorecards.filter((card) => card.specialistId === current.id);
    const measured = cards.filter((card) => !card.versionId.endsWith('@untrained'));
    const best = measured.reduce<number | null>((acc, card) => (acc === null || card.quality > acc ? card.quality : acc), null);
    const baseline = cards.find((card) => card.versionId.endsWith('@untrained')) ?? null;
    return { cards, best, baseline };
  }, [current, scorecards]);

  if (!current) {
    return (
      <EmptyState
        title="Specialist not found"
        description={`No specialist with the id "${specialistId}" is registered. It may have been deleted — pick another from the sidebar.`}
        action={
          <button type="button" className="btn-primary" onClick={() => onOpen({ view: 'overview' })}>
            Back to overview
          </button>
        }
      />
    );
  }

  const specialistDatasets = datasets.filter((dataset) => dataset.specialistId === current.id);

  return (
    <div className="space-y-5">
      <section className="glass reflective relative overflow-hidden p-5">
        <div
          className="pointer-events-none absolute -right-20 -top-24 h-56 w-56 rounded-full blur-3xl animate-breathe"
          style={{ background: `${current.accent}33` }}
          aria-hidden
        />
        <div className="relative flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-4">
            <span
              className="flex h-12 w-12 items-center justify-center rounded-2xl text-sm font-semibold"
              style={{ background: `${current.accent}22`, color: current.accent, boxShadow: `inset 0 0 0 1px ${current.accent}44` }}
              aria-hidden
            >
              {current.glyph || current.shortName.slice(0, 2)}
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="display text-lg text-slate-50">{current.name}</h2>
                <Badge tone={specialistTone(current.state)}>
                  <StatusDot level={specialistTone(current.state)} pulse={current.state === 'training'} />
                  {current.state}
                </Badge>
                <Badge tone="unknown">{current.category}</Badge>
                {!current.enabled && <Badge tone="warn">disabled</Badge>}
              </div>
              <p className="mt-1 max-w-2xl text-xs leading-relaxed text-slate-400">{stateSentence(current)}</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-ghost" onClick={() => setTab('bench')}>
              Test it
            </button>
            <button type="button" className="btn-primary" onClick={() => onOpen({ view: 'training' })}>
              Train
            </button>
          </div>
        </div>

        <div className="relative mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile
            label="Active model"
            value={current.activeVersion?.label ?? 'untrained'}
            hint={current.activeVersion ? `${current.activeVersion.methodName} · ${current.activeVersion.artifactKind}` : 'no local artefact yet'}
          />
          <StatTile
            label="Best measured quality"
            value={metrics?.best === null || metrics?.best === undefined ? 'not measured' : `${(metrics.best * 100).toFixed(1)}%`}
            tone={metrics?.best ? 'ok' : 'default'}
            hint={metrics?.baseline ? `rule baseline ${(metrics.baseline.quality * 100).toFixed(1)}%` : 'no baseline recorded yet'}
            progress={metrics?.best ?? null}
          />
          <StatTile label="Training runs" value={current.runCount} hint={current.lastRunAt ? `last ${new Date(current.lastRunAt).toLocaleDateString()}` : 'run history empty'} />
          <StatTile
            label="Capabilities ready"
            value={current.permissionsReady ? 'yes' : 'blocked'}
            tone={current.permissionsReady ? 'ok' : 'warn'}
            hint={current.permissionsReady ? `${current.toolIds.length} tools usable` : `blocked: ${current.blockedTools.join(', ')}`}
          />
        </div>
      </section>

      <SegmentedControl<Tab>
        ariaLabel="Specialist workspace sections"
        value={tab}
        onChange={setTab}
        options={[
          { id: 'overview', label: 'Overview' },
          { id: 'bench', label: 'Test bench' },
          { id: 'training', label: 'Training & versions' },
          { id: 'settings', label: 'Settings' },
        ]}
      />

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      {tab === 'overview' && (
        <div className="grid gap-5 lg:grid-cols-3">
          <Card className="lg:col-span-2" title="What it can do" subtitle="Tools this specialist may call, and what it is designed to produce.">
            <div className="flex flex-wrap gap-2">
              {current.toolIds.map((toolId) => (
                <Chip key={toolId} title={current.blockedTools.includes(toolId) ? 'Currently blocked by permissions' : 'Available'}>
                  {current.blockedTools.includes(toolId) ? `${toolId} · blocked` : toolId}
                </Chip>
              ))}
              {current.toolIds.length === 0 && <p className="text-xs text-slate-500">No tools assigned.</p>}
            </div>
            <div className="mt-4 space-y-1">
              <p className="eyebrow">Outputs</p>
              {current.outputs.map((output) => (
                <p key={output} className="text-xs leading-relaxed text-slate-400">
                  · {output}
                </p>
              ))}
            </div>
            <div className="mt-4">
              <p className="eyebrow mb-2">Training data available</p>
              {specialistDatasets.length === 0 ? (
                <p className="text-xs text-slate-500">No dataset is registered for this specialist yet.</p>
              ) : (
                <div className="space-y-2">
                  {specialistDatasets.map((dataset) => (
                    <div key={dataset.id} className="glass-subtle flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-2xs">
                      <span className="text-slate-300">{dataset.name}</span>
                      <span className="text-slate-500">
                        {dataset.size} items · {dataset.provenance} · {dataset.labels.length} labels
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Card>

          <Card title="Captured examples" subtitle="Inputs you saved from the test bench become training data for the next run.">
            {examples.length === 0 ? (
              <p className="text-xs leading-relaxed text-slate-500">
                Nothing captured yet. In the test bench, use “Run + save example” to record the input together with the label the
                specialist chose — that pair is what trains the head.
              </p>
            ) : (
              <div className="space-y-2">
                {examples.slice(-8).reverse().map((example) => (
                  <div key={example.id} className="glass-subtle px-3 py-2">
                    <p className="truncate text-2xs text-slate-300">{example.text}</p>
                    <p className="mt-0.5 flex items-center justify-between font-mono text-[10px] text-slate-500">
                      <span>{example.label}</span>
                      <button
                        type="button"
                        className="hover:text-signal-error"
                        onClick={async () => {
                          try {
                            await api.removeExample(current.id, example.id);
                            setExamples((prev) => prev.filter((entry) => entry.id !== example.id));
                            void refreshPlatform();
                          } catch (e) {
                            setError(e instanceof ApiError ? e.message : String(e));
                          }
                        }}
                      >
                        remove
                      </button>
                    </p>
                  </div>
                ))}
                <p className="text-2xs text-slate-500">
                  {examples.length} example{examples.length === 1 ? '' : 's'} stored. They are added to the next training run for this
                  specialist.
                </p>
              </div>
            )}
          </Card>
        </div>
      )}

      {tab === 'bench' && <Bench specialist={current} onChanged={() => void refreshPlatform()} />}
      {tab === 'training' && <TrainingTab specialist={current} onOpen={onOpen} />}
      {tab === 'settings' && (
        <SettingsTab
          specialist={current}
          onChanged={() => {
            void refreshPlatform();
          }}
        />
      )}

      {(current.state === 'training' || runs.some((run) => run.specialistId === current.id && run.status === 'running')) && (
        <p className="flex items-center gap-2 text-2xs text-signal-warn">
          <Spinner label="Training is running — this workspace updates as epochs arrive." />
        </p>
      )}
    </div>
  );
}
