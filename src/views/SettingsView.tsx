import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useSystem } from '@/state/SystemContext';
import { Badge, Card, ErrorBanner, KeyValue, Meter, SectionTitle, SegmentedControl, Spinner, StatusBadge, Toggle } from '@/components/ui';
import { ModelsView } from '@/views/ModelsView';
import { CapabilitiesView } from '@/views/CapabilitiesView';
import type { Settings } from '@shared/types';

/**
 * Settings.
 *
 * Three groups matter to the platform's behaviour and each is stated in terms of
 * what it actually changes:
 *   • routing — which mechanism answers, and at what confidence;
 *   • training — the promotion gate and whether runs measure themselves;
 *   • appearance — glass intensity, ambient glow and motion, including an
 *     explicit "reduce motion" that is separate from the OS preference.
 */

type Tab = 'system' | 'routing' | 'inventory';

export function SettingsView() {
  const { settings, saveSettings, resetSettings, refresh, lastError, dismissError, connection, specialists, datasets } = useSystem();
  const [tab, setTab] = useState<Tab>('system');
  const [draft, setDraft] = useState<{
    openaiKey: string;
    openaiBase: string;
    openaiModel: string;
    ollamaBase: string;
    ollamaModel: string;
  }>({ openaiKey: '', openaiBase: '', openaiModel: '', ollamaBase: '', ollamaModel: '' });
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [engines, setEngines] = useState<Awaited<ReturnType<typeof api.speechEngines>> | null>(null);

  useEffect(() => {
    if (!settings) return;
    setDraft((current) => ({
      ...current,
      openaiBase: settings.providers.openai.baseUrl,
      openaiModel: settings.providers.openai.model,
      ollamaBase: settings.providers.ollama.baseUrl,
      ollamaModel: settings.providers.ollama.model,
    }));
  }, [settings]);

  useEffect(() => {
    api
      .speechEngines()
      .then(setEngines)
      .catch(() => setEngines(null));
  }, []);

  if (!settings) {
    return (
      <div className="space-y-4">
        {connection === 'offline' && (
          <ErrorBanner
            message={lastError ?? 'The AgentOS backend is unreachable, so settings cannot be loaded.'}
            onRetry={() => void refresh()}
          />
        )}
        {connection !== 'offline' && (
          <Card>
            <Spinner label="Loading settings…" />
          </Card>
        )}
      </div>
    );
  }

  async function persist(patch: Partial<Settings>, label: string) {
    setSaving(true);
    const res = await saveSettings(patch);
    setSaving(false);
    if (res.ok) setSavedAt(label);
  }

  async function saveProviderFields() {
    setSaving(true);
    const res = await saveSettings({
      providers: {
        openai: { apiKey: draft.openaiKey, baseUrl: draft.openaiBase, model: draft.openaiModel },
        ollama: { baseUrl: draft.ollamaBase, model: draft.ollamaModel },
      },
    });
    setSaving(false);
    if (res.ok) {
      setDraft((current) => ({ ...current, openaiKey: '' }));
      setSavedAt('Provider settings saved');
    }
  }

  const routing = settings.routing;
  const training = settings.training;
  const trainedCount = specialists.filter((entry) => entry.activeVersion).length;

  return (
    <div className="space-y-5">
      {lastError && <ErrorBanner message={lastError} onDismiss={dismissError} onRetry={() => void refresh()} />}

      <SegmentedControl<Tab>
        ariaLabel="Settings sections"
        value={tab}
        onChange={setTab}
        options={[
          { id: 'system', label: 'Providers & system' },
          { id: 'routing', label: 'Routing, training & look' },
          { id: 'inventory', label: 'Inventory' },
        ]}
      />

      {tab === 'system' && (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card
            title="Model providers"
            subtitle="Credentials are stored server-side only and never returned to the browser"
            actions={saving ? <Spinner label="Saving…" /> : savedAt ? <span className="text-xs text-signal-ok">{savedAt}</span> : null}
          >
            <div className="space-y-4">
              <div>
                <SectionTitle hint="optional">OpenAI-compatible API</SectionTitle>
                <label className="label" htmlFor="openai-key">
                  API key {settings.providers.openai.hasApiKey && <span className="text-signal-ok">• stored</span>}
                </label>
                <input
                  id="openai-key"
                  type="password"
                  autoComplete="off"
                  className="input font-mono"
                  placeholder={settings.providers.openai.hasApiKey ? '•••••• (unchanged)' : 'sk-…'}
                  value={draft.openaiKey}
                  onChange={(event) => setDraft({ ...draft, openaiKey: event.target.value })}
                />
                <p className="mt-1 text-2xs text-slate-500">
                  Leave empty to keep the stored key. Clearing keys is done via “Reset all settings”.
                </p>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="label" htmlFor="openai-base">
                      Base URL
                    </label>
                    <input
                      id="openai-base"
                      className="input font-mono text-xs"
                      value={draft.openaiBase}
                      onChange={(event) => setDraft({ ...draft, openaiBase: event.target.value })}
                    />
                  </div>
                  <div>
                    <label className="label" htmlFor="openai-model">
                      Default model
                    </label>
                    <input
                      id="openai-model"
                      className="input font-mono text-xs"
                      value={draft.openaiModel}
                      onChange={(event) => setDraft({ ...draft, openaiModel: event.target.value })}
                    />
                  </div>
                </div>
              </div>

              <div className="divider" />

              <div>
                <SectionTitle hint="local inference">Ollama</SectionTitle>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="label" htmlFor="ollama-base">
                      Base URL
                    </label>
                    <input
                      id="ollama-base"
                      className="input font-mono text-xs"
                      value={draft.ollamaBase}
                      onChange={(event) => setDraft({ ...draft, ollamaBase: event.target.value })}
                    />
                  </div>
                  <div>
                    <label className="label" htmlFor="ollama-model">
                      Default model
                    </label>
                    <input
                      id="ollama-model"
                      className="input font-mono text-xs"
                      value={draft.ollamaModel}
                      onChange={(event) => setDraft({ ...draft, ollamaModel: event.target.value })}
                    />
                  </div>
                </div>
              </div>

              <div className="flex gap-2">
                <button type="button" className="btn-primary" onClick={() => void saveProviderFields()} disabled={saving}>
                  Save providers
                </button>
                <button type="button" className="btn-ghost" onClick={() => void refresh()}>
                  Re-probe availability
                </button>
              </div>
            </div>
          </Card>

          <div className="space-y-5">
            <Card title="Permissions" subtitle="Tools and desktop actions are gated by these switches; defaults are restrictive">
              <Toggle
                label="Read workspace files"
                description="Allows read-only tools confined to the AgentOS workspace directory"
                checked={settings.permissions.allowFileRead}
                onChange={(value) => void persist({ permissions: { ...settings.permissions, allowFileRead: value } }, 'Permissions updated')}
              />
              <Toggle
                danger
                label="Write notes & data files"
                description="Allows writing inside the AgentOS data directory only — arbitrary file access is never granted"
                checked={settings.permissions.allowFileWrite}
                onChange={(value) => void persist({ permissions: { ...settings.permissions, allowFileWrite: value } }, 'Permissions updated')}
              />
              <Toggle
                danger
                label="Network requests"
                description="Allows tools such as URL fetching to make outbound requests. With this off, the Research specialist reports the target instead of fetching it."
                checked={settings.permissions.allowNetwork}
                onChange={(value) => void persist({ permissions: { ...settings.permissions, allowNetwork: value } }, 'Permissions updated')}
              />
              <Toggle
                danger
                label="Shell execution"
                description="Reserved for future desktop actions. No shell tool is registered in this build, so enabling it has no effect."
                checked={settings.permissions.allowShell}
                onChange={(value) => void persist({ permissions: { ...settings.permissions, allowShell: value } }, 'Permissions updated')}
              />
            </Card>

            <Card title="Voice" subtitle="Engines are interchangeable; the browser engines are active today">
              <Toggle
                label="Speech-to-text"
                checked={settings.voice.sttEnabled}
                description="Enables the dictate button in the assistant"
                onChange={(value) => void persist({ voice: { ...settings.voice, sttEnabled: value } }, 'Voice settings saved')}
              />
              <Toggle
                label="Text-to-speech"
                checked={settings.voice.ttsEnabled}
                description="Enables read-aloud of assistant replies"
                onChange={(value) => void persist({ voice: { ...settings.voice, ttsEnabled: value } }, 'Voice settings saved')}
              />
              <div className="mt-3 space-y-2">
                {engines ? (
                  engines.engines.map((engine) => (
                    <div key={engine.id} className="flex items-center justify-between gap-3 text-xs">
                      <span className="text-slate-300">{engine.name}</span>
                      <span className="flex items-center gap-2">
                        <span className="text-2xs text-slate-500">{engine.detail}</span>
                        <StatusBadge
                          level={engine.available ? (engine.location === 'client' ? 'ok' : 'warn') : 'error'}
                          label={engine.location === 'client' ? 'client' : 'server'}
                        />
                      </span>
                    </div>
                  ))
                ) : (
                  <p className="text-xs text-slate-500">Speech engine status unavailable (backend offline).</p>
                )}
              </div>
            </Card>

            <Card title="Telemetry & logging">
              <div className="max-w-xs">
                <label className="label" htmlFor="log-level">
                  Backend log level
                </label>
                <select
                  id="log-level"
                  className="input text-xs"
                  value={settings.logging.level}
                  onChange={(event) => void persist({ logging: { level: event.target.value as Settings['logging']['level'] } }, 'Log level updated')}
                >
                  <option value="debug">debug</option>
                  <option value="info">info</option>
                  <option value="warn">warn</option>
                  <option value="error">error</option>
                </select>
              </div>
              <div className="mt-3">
                <KeyValue label="Settings file">
                  <code className="font-mono text-2xs">{settings.settingsPath}</code>
                </KeyValue>
                <KeyValue label="Persistence">Written atomically on every change; reloaded at startup</KeyValue>
                <KeyValue label="Secret handling">API keys never leave the backend and are redacted in logs</KeyValue>
              </div>
              <div className="mt-4">
                <button
                  type="button"
                  className="btn-danger"
                  onClick={() => {
                    if (confirm('Reset all settings, including stored API keys, to defaults?')) {
                      void resetSettings().then(() => setSavedAt('Settings reset to defaults'));
                    }
                  }}
                >
                  Reset all settings
                </button>
              </div>
            </Card>
          </div>
        </div>
      )}

      {tab === 'routing' && (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card title="Routing" subtitle="How a request picks a mechanism. This is the single control that changes every specialist at once.">
            <Toggle
              label="Auto-route by intent"
              description="When off, everything goes to General AI unless the user pins a specialist."
              checked={routing.autoRoute}
              onChange={(value) => void persist({ routing: { ...routing, autoRoute: value } }, 'Routing updated')}
            />
            <Toggle
              label="Prefer trained heads"
              description="Lets a local trained model answer instead of escalating immediately. Turning this off does not delete anything — it just routes around the models."
              checked={routing.preferTrainedHeads}
              onChange={(value) => void persist({ routing: { ...routing, preferTrainedHeads: value } }, 'Routing updated')}
            />
            <Toggle
              label="Allow rule fallback"
              description="The deterministic planner answers when the model is below its threshold. Its confidence is a fixed constant, not a model score, and the trace says so."
              checked={routing.allowRuleFallback}
              onChange={(value) => void persist({ routing: { ...routing, allowRuleFallback: value } }, 'Routing updated')}
            />
            <div className="mt-4">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-sm text-slate-200">Default confidence threshold</span>
                <span className="font-mono text-xs text-slate-300">{(routing.confidenceThreshold * 100).toFixed(0)}%</span>
              </div>
              <input
                className="mt-2 w-full accent-accent-500"
                type="range"
                min={0.05}
                max={0.95}
                step={0.05}
                value={routing.confidenceThreshold}
                aria-label="Confidence threshold"
                onChange={(event) => void persist({ routing: { ...routing, confidenceThreshold: Number(event.target.value) } }, 'Routing updated')}
              />
              <p className="mt-1 text-2xs leading-relaxed text-slate-500">
                Below this confidence a request is delegated to the rule policy (if allowed) and then escalated. A trained head that was
                calibrated stores its own measured threshold in the artefact and takes precedence over this default.
              </p>
            </div>
            <div className="mt-4">
              <Meter
                label={`Specialists with a trained model`}
                value={trainedCount}
                max={Math.max(1, specialists.length)}
                detail={`${trainedCount}/${specialists.length}`}
                tone={trainedCount === specialists.length ? 'ok' : 'warn'}
              />
            </div>
          </Card>

          <div className="space-y-5">
            <Card title="Training & promotion" subtitle="The gate that decides whether a freshly trained model replaces the active one.">
              <Toggle
                label="Auto-promote winners"
                description="When off, every candidate waits in the Training Lab for an explicit promotion."
                checked={training.autoPromote}
                onChange={(value) => void persist({ training: { ...training, autoPromote: value } }, 'Training settings saved')}
              />
              <Toggle
                label="Benchmark after training"
                description="Measures the new version on the matching suite and decides with the evidence. Without it, promotion falls back to the training metric."
                checked={training.autoBenchmark}
                onChange={(value) => void persist({ training: { ...training, autoBenchmark: value } }, 'Training settings saved')}
              />
              <div className="mt-4">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm text-slate-200">Minimum improvement to promote</span>
                  <span className="font-mono text-xs text-slate-300">{(training.minImprovement * 100).toFixed(1)} pts</span>
                </div>
                <input
                  className="mt-2 w-full accent-accent-500"
                  type="range"
                  min={0}
                  max={0.1}
                  step={0.005}
                  value={training.minImprovement}
                  aria-label="Minimum improvement"
                  onChange={(event) => void persist({ training: { ...training, minImprovement: Number(event.target.value) } }, 'Training settings saved')}
                />
                <p className="mt-1 text-2xs leading-relaxed text-slate-500">
                  Quality points over the best known baseline for the same suite. Improvements below this gate keep the current model
                  active; a worse candidate is kept as a version marked as a regression.
                </p>
              </div>
              <div className="mt-4">
                <label className="label" htmlFor="default-dataset">
                  Default dataset
                </label>
                <select
                  id="default-dataset"
                  className="input text-xs"
                  value={training.defaultDatasetId}
                  onChange={(event) => void persist({ training: { ...training, defaultDatasetId: event.target.value } }, 'Training settings saved')}
                >
                  <option value="">none — pick per run</option>
                  {datasets.map((dataset) => (
                    <option key={dataset.id} value={dataset.id} className="bg-base-850">
                      {dataset.name} ({dataset.size})
                    </option>
                  ))}
                </select>
              </div>
            </Card>

            <Card title="Appearance" subtitle="The glass surface responds to these three settings immediately — no reload.">
              <div className="space-y-3">
                <div>
                  <span className="label">Glass intensity</span>
                  <div className="flex gap-2">
                    {(['subtle', 'balanced', 'vivid'] as const).map((option) => (
                      <button
                        key={option}
                        type="button"
                        aria-pressed={settings.appearance.glassIntensity === option}
                        className={`btn-quiet ${settings.appearance.glassIntensity === option ? '!border-accent-400/50 !text-accent-200' : ''}`}
                        onClick={() => void persist({ appearance: { ...settings.appearance, glassIntensity: option } }, 'Appearance updated')}
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                  <p className="mt-1 text-2xs text-slate-500">Controls the translucency of every panel in the interface.</p>
                </div>

                <div>
                  <span className="label">Motion</span>
                  <div className="flex gap-2">
                    {(['auto', 'on', 'off'] as const).map((option) => (
                      <button
                        key={option}
                        type="button"
                        aria-pressed={settings.appearance.reduceMotion === option}
                        className={`btn-quiet ${settings.appearance.reduceMotion === option ? '!border-accent-400/50 !text-accent-200' : ''}`}
                        onClick={() => void persist({ appearance: { ...settings.appearance, reduceMotion: option } }, 'Appearance updated')}
                      >
                        {option === 'auto' ? 'follow system' : option === 'on' ? 'reduce motion' : 'full motion'}
                      </button>
                    ))}
                  </div>
                  <p className="mt-1 text-2xs text-slate-500">
                    “Follow system” honours your operating system preference. Choosing “reduce motion” disables all decorative animation
                    and keeps transitions short.
                  </p>
                </div>

                <Toggle
                  label="Ambient glow"
                  description="The soft coloured light behind the interface. Turning it off keeps the layout but removes the gradient wash."
                  checked={settings.appearance.ambientGlow}
                  onChange={(value) => void persist({ appearance: { ...settings.appearance, ambientGlow: value } }, 'Appearance updated')}
                />
              </div>
            </Card>
          </div>
        </div>
      )}

      {tab === 'inventory' && (
        <div className="space-y-5">
          <ModelsView onOpenSettings={() => setTab('system')} />
          <CapabilitiesView />
          <Card title="Registered specialists" subtitle="What each one currently has, so the inventory never overstates the system.">
            {specialists.length === 0 ? (
              <p className="text-xs text-slate-500">The platform layer is unavailable, so specialists cannot be listed.</p>
            ) : (
              <div className="space-y-2">
                {specialists.map((specialist) => (
                  <div key={specialist.id} className="glass-subtle flex flex-wrap items-center justify-between gap-3 px-3.5 py-2.5">
                    <div>
                      <p className="text-xs text-slate-200">{specialist.name}</p>
                      <p className="mt-0.5 text-2xs text-slate-500">
                        {specialist.category} · {specialist.toolIds.length} tools ·{' '}
                        {specialist.benchmarkSuiteIds.join(', ') || 'no suite'}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge tone={specialist.activeVersion ? 'ok' : 'unknown'}>
                        {specialist.activeVersion ? specialist.activeVersion.label : 'untrained'}
                      </Badge>
                      <Badge tone={specialist.permissionsReady ? 'ok' : 'warn'}>
                        {specialist.permissionsReady ? 'ready' : 'blocked tools'}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
