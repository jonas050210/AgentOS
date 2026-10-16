import {useEffect, useState} from 'react';
import { api } from '@/lib/api';
import { useSystem } from '@/state/SystemContext';
import { Card, ErrorBanner, KeyValue, SectionTitle, Spinner, StatusBadge, Toggle } from '@/components/ui';
import type { Settings } from '@shared/types';

export function SettingsView() {
  const { settings, saveSettings, resetSettings, refresh, lastError, dismissError, connection } = useSystem();
  const [draft, setDraft] = useState<{ openaiKey: string; openaiBase: string; openaiModel: string; ollamaBase: string; ollamaModel: string }>({
    openaiKey: '',
    openaiBase: '',
    openaiModel: '',
    ollamaBase: '',
    ollamaModel: '',
  });
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [engines, setEngines] = useState<Awaited<ReturnType<typeof api.speechEngines>> | null>(null);

  useEffect(() => {
    if (!settings) return;
    setDraft((d) => ({
      ...d,
      openaiBase: settings.providers.openai.baseUrl,
      openaiModel: settings.providers.openai.model,
      ollamaBase: settings.providers.ollama.baseUrl,
      ollamaModel: settings.providers.ollama.model,
    }));
  }, [settings]);

  useEffect(() => {
    api.speechEngines().then(setEngines).catch(() => setEngines(null));
  }, []);

  if (!settings) {
    // Never leave the user staring at an endless spinner: an unreachable
    // backend is reported explicitly with a retry action.
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
      setDraft((d) => ({ ...d, openaiKey: '' }));
      setSavedAt('Provider settings saved');
    }
  }

  return (
    <div className="space-y-5">
      {lastError && <ErrorBanner message={lastError} onDismiss={dismissError} onRetry={() => void refresh()} />}

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
                onChange={(e) => setDraft({ ...draft, openaiKey: e.target.value })}
              />
              <p className="mt-1 text-[11px] text-slate-500">Leave empty to keep the stored key. Clearing keys is done via “Reset all settings”.</p>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="label" htmlFor="openai-base">
                    Base URL
                  </label>
                  <input id="openai-base" className="input font-mono text-xs" value={draft.openaiBase} onChange={(e) => setDraft({ ...draft, openaiBase: e.target.value })} />
                </div>
                <div>
                  <label className="label" htmlFor="openai-model">
                    Default model
                  </label>
                  <input id="openai-model" className="input font-mono text-xs" value={draft.openaiModel} onChange={(e) => setDraft({ ...draft, openaiModel: e.target.value })} />
                </div>
              </div>
            </div>

            <div className="border-t border-white/[0.05] pt-4">
              <SectionTitle hint="local inference">Ollama</SectionTitle>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="label" htmlFor="ollama-base">
                    Base URL
                  </label>
                  <input id="ollama-base" className="input font-mono text-xs" value={draft.ollamaBase} onChange={(e) => setDraft({ ...draft, ollamaBase: e.target.value })} />
                </div>
                <div>
                  <label className="label" htmlFor="ollama-model">
                    Default model
                  </label>
                  <input id="ollama-model" className="input font-mono text-xs" value={draft.ollamaModel} onChange={(e) => setDraft({ ...draft, ollamaModel: e.target.value })} />
                </div>
              </div>
            </div>

            <div className="flex gap-2">
              <button type="button" className="btn-primary text-xs" onClick={() => void saveProviderFields()} disabled={saving}>
                Save providers
              </button>
              <button type="button" className="btn-ghost text-xs" onClick={() => void refresh()}>
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
              onChange={(v) => void persist({ permissions: { ...settings.permissions, allowFileRead: v } }, 'Permissions updated')}
            />
            <Toggle
              danger
              label="Write notes & data files"
              description="Allows writing inside the AgentOS data directory only — arbitrary file access is never granted"
              checked={settings.permissions.allowFileWrite}
              onChange={(v) => void persist({ permissions: { ...settings.permissions, allowFileWrite: v } }, 'Permissions updated')}
            />
            <Toggle
              danger
              label="Network requests"
              description="Allows tools such as URL fetching to make outbound requests"
              checked={settings.permissions.allowNetwork}
              onChange={(v) => void persist({ permissions: { ...settings.permissions, allowNetwork: v } }, 'Permissions updated')}
            />
            <Toggle
              danger
              label="Shell execution"
              description="Reserved for future desktop actions. No shell tool is registered in this build, so enabling it has no effect."
              checked={settings.permissions.allowShell}
              onChange={(v) => void persist({ permissions: { ...settings.permissions, allowShell: v } }, 'Permissions updated')}
            />
          </Card>

          <Card title="Voice" subtitle="Engines are interchangeable; the browser engines are active today">
            <Toggle
              label="Speech-to-text"
              checked={settings.voice.sttEnabled}
              description="Enables the dictate button in chat"
              onChange={(v) => void persist({ voice: { ...settings.voice, sttEnabled: v } }, 'Voice settings saved')}
            />
            <Toggle
              label="Text-to-speech"
              checked={settings.voice.ttsEnabled}
              description="Enables read-aloud of assistant replies"
              onChange={(v) => void persist({ voice: { ...settings.voice, ttsEnabled: v } }, 'Voice settings saved')}
            />
            <div className="mt-3 space-y-2">
              {engines ? (
                engines.engines.map((e) => (
                  <div key={e.id} className="flex items-center justify-between gap-3 text-xs">
                    <span className="text-slate-300">{e.name}</span>
                    <span className="flex items-center gap-2">
                      <span className="text-[11px] text-slate-500">{e.detail}</span>
                      <StatusBadge level={e.available ? (e.location === 'client' ? 'ok' : 'warn') : 'error'} label={e.location === 'client' ? 'client' : 'server'} />
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
                onChange={(e) => void persist({ logging: { level: e.target.value as Settings['logging']['level'] } }, 'Log level updated')}
              >
                <option value="debug">debug</option>
                <option value="info">info</option>
                <option value="warn">warn</option>
                <option value="error">error</option>
              </select>
            </div>
            <div className="mt-3">
              <KeyValue label="Settings file">
                <code className="font-mono text-[11px]">{settings.settingsPath}</code>
              </KeyValue>
              <KeyValue label="Persistence">Written atomically on every change; reloaded at startup</KeyValue>
              <KeyValue label="Secret handling">API keys never leave the backend and are redacted in logs</KeyValue>
            </div>
            <div className="mt-4 flex gap-2">
              <button
                type="button"
                className="btn-danger text-xs"
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
    </div>
  );
}
