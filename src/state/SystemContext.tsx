import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError, SettingsResponse, activityStreamUrl } from '@/lib/api';
import type { ActivityEntry, CapabilityInfo, HealthStatus, ModelInfo, ProviderInfo, Settings, ToolInfo } from '@shared/types';

export type ConnectionState = 'connecting' | 'online' | 'offline';

interface SystemContextValue {
  connection: ConnectionState;
  lastError: string | null;
  health: HealthStatus | null;
  providers: ProviderInfo[];
  models: ModelInfo[];
  modelProviders: Array<{ id: string; name: string; available: boolean; configured: boolean; detail?: string; modelCount: number }>;
  anyExternalAvailable: boolean;
  capabilities: CapabilityInfo[];
  tools: ToolInfo[];
  settings: SettingsResponse | null;
  activity: ActivityEntry[];
  loading: boolean;
  refresh: () => Promise<void>;
  saveSettings: (patch: Partial<Settings>) => Promise<{ ok: boolean; error?: string }>;
  resetSettings: () => Promise<{ ok: boolean; error?: string }>;
  dismissError: () => void;
}

const SystemContext = createContext<SystemContextValue | null>(null);

export function SystemProvider({ children }: { children: React.ReactNode }) {
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelProviders, setModelProviders] = useState<SystemContextValue['modelProviders']>([]);
  const [anyExternalAvailable, setAnyExternalAvailable] = useState(false);
  const [capabilities, setCapabilities] = useState<CapabilityInfo[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastError, setLastError] = useState<string | null>(null);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const [h, p, m, c, t, s, a] = await Promise.all([
        api.health(),
        api.providers(),
        api.models(),
        api.capabilities(),
        api.tools(),
        api.settings(),
        api.activity(150),
      ]);
      if (!mounted.current) return;
      // Defensive shape checks: a partial or unexpected backend payload must not
      // crash the interface — the affected section simply stays empty.
      setHealth(isHealth(h) ? h : null);
      setProviders(Array.isArray(p) ? p : []);
      setModels(Array.isArray(m?.models) ? m.models : []);
      setModelProviders(Array.isArray(m?.providers) ? m.providers : []);
      setAnyExternalAvailable(Boolean(m?.anyExternalAvailable));
      setCapabilities(Array.isArray(c?.capabilities) ? c.capabilities : []);
      setTools(Array.isArray(t?.tools) ? t.tools : []);
      setSettings(isSettingsResponse(s) ? s : null);
      setActivity(Array.isArray(a?.entries) ? a.entries : []);
      setConnection('online');
      setLastError(null);
    } catch (e) {
      if (!mounted.current) return;
      setConnection('offline');
      setLastError(e instanceof ApiError ? e.message : String(e));
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const interval = setInterval(() => void refresh(), 20_000);
    return () => {
      mounted.current = false;
      clearInterval(interval);
    };
  }, [refresh]);

  // Live activity feed over SSE; falls back silently to polling if unsupported.
  useEffect(() => {
    if (typeof EventSource === 'undefined') return;
    const source = new EventSource(activityStreamUrl());
    source.addEventListener('activity', (event) => {
      try {
        const entry = JSON.parse((event as MessageEvent).data) as ActivityEntry;
        setActivity((prev) => [...prev.slice(-199), entry]);
      } catch {
        /* ignore malformed payloads */
      }
    });
    source.onerror = () => {
      // EventSource auto-reconnects; surface the outage only through connection state.
    };
    return () => source.close();
  }, []);

  const saveSettings = useCallback(async (patch: Partial<Settings>) => {
    try {
      const saved = await api.updateSettings(patch);
      setSettings(saved);
      void refresh();
      return { ok: true };
    } catch (e) {
      const message = e instanceof ApiError ? e.message : String(e);
      setLastError(message);
      return { ok: false, error: message };
    }
  }, [refresh]);

  const resetSettings = useCallback(async () => {
    try {
      const saved = await api.resetSettings();
      setSettings(saved);
      void refresh();
      return { ok: true };
    } catch (e) {
      const message = e instanceof ApiError ? e.message : String(e);
      setLastError(message);
      return { ok: false, error: message };
    }
  }, [refresh]);

  const value = useMemo<SystemContextValue>(
    () => ({
      connection,
      lastError,
      health,
      providers,
      models,
      modelProviders,
      anyExternalAvailable,
      capabilities,
      tools,
      settings,
      activity,
      loading,
      refresh,
      saveSettings,
      resetSettings,
      dismissError: () => setLastError(null),
    }),
    [
      connection,
      lastError,
      health,
      providers,
      models,
      modelProviders,
      anyExternalAvailable,
      capabilities,
      tools,
      settings,
      activity,
      loading,
      refresh,
      saveSettings,
      resetSettings,
    ],
  );

  return <SystemContext.Provider value={value}>{children}</SystemContext.Provider>;
}

function isHealth(value: unknown): value is HealthStatus {
  return Boolean(value && typeof value === 'object' && Array.isArray((value as HealthStatus).components));
}

function isSettingsResponse(value: unknown): value is SettingsResponse {
  const v = value as SettingsResponse | undefined;
  return Boolean(v && typeof v === 'object' && v.permissions && v.providers && v.logging);
}

export function useSystem(): SystemContextValue {
  const ctx = useContext(SystemContext);
  if (!ctx) throw new Error('useSystem must be used inside <SystemProvider>');
  return ctx;
}
