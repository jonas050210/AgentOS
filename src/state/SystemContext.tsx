import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError, PlatformOverview, SettingsResponse, activityStreamUrl, trainingStreamUrl } from '@/lib/api';
import type {
  ActivityEntry,
  CapabilityInfo,
  HealthStatus,
  ModelInfo,
  ModelVersionSummary,
  ProviderInfo,
  Settings,
  SpecialistSummary,
  ToolInfo,
  TrainingEvent,
  TrainingRun,
} from '@shared/types';

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
  /** Platform layer: specialists, their runs, versions and measurements. */
  specialists: SpecialistSummary[];
  runs: TrainingRun[];
  runCounts: PlatformOverview['counts'] | null;
  versions: ModelVersionSummary[];
  scorecards: PlatformOverview['scorecards'];
  recentComparisons: PlatformOverview['recentComparisons'];
  methods: PlatformOverview['methods'];
  datasets: PlatformOverview['datasets'];
  suites: PlatformOverview['suites'];
  trainingAvailable: number;
  overviewLoaded: boolean;
  /** True while a training run is queued or running anywhere in the system. */
  trainingActive: boolean;
  specialist: (id: string) => SpecialistSummary | null;
  refresh: () => Promise<void>;
  refreshPlatform: () => Promise<void>;
  applyTrainingEvent: (event: TrainingEvent) => void;
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
  const [specialists, setSpecialists] = useState<SpecialistSummary[]>([]);
  const [runs, setRuns] = useState<TrainingRun[]>([]);
  const [runCounts, setRunCounts] = useState<PlatformOverview['counts'] | null>(null);
  const [versions, setVersions] = useState<ModelVersionSummary[]>([]);
  const [scorecards, setScorecards] = useState<PlatformOverview['scorecards']>([]);
  const [recentComparisons, setRecentComparisons] = useState<PlatformOverview['recentComparisons']>([]);
  const [methods, setMethods] = useState<PlatformOverview['methods']>([]);
  const [datasets, setDatasets] = useState<PlatformOverview['datasets']>([]);
  const [suites, setSuites] = useState<PlatformOverview['suites']>([]);
  const [trainingAvailable, setTrainingAvailable] = useState(0);
  const [overviewLoaded, setOverviewLoaded] = useState(false);
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
    // The platform layer is additive: if it is unavailable the system views
    // still render, they just have no specialist data to show.
    try {
      const overview = await api.overview();
      if (!mounted.current) return;
      setSpecialists(Array.isArray(overview?.specialists) ? overview.specialists : []);
      setRuns(Array.isArray(overview?.runs) ? overview.runs : []);
      setRunCounts(overview?.counts ?? null);
      setVersions(Array.isArray(overview?.versions) ? overview.versions : []);
      setScorecards(Array.isArray(overview?.scorecards) ? overview.scorecards : []);
      setRecentComparisons(Array.isArray(overview?.recentComparisons) ? overview.recentComparisons : []);
      setMethods(Array.isArray(overview?.methods) ? overview.methods : []);
      setDatasets(Array.isArray(overview?.datasets) ? overview.datasets : []);
      setSuites(Array.isArray(overview?.suites) ? overview.suites : []);
      setTrainingAvailable(Number(overview?.trainingAvailable ?? 0));
      setOverviewLoaded(true);
    } catch {
      /* platform layer unavailable — keep the core system data */
    }
  }, []);

  const refreshPlatform = useCallback(async () => {
    try {
      const overview = await api.overview();
      if (!mounted.current) return;
      setSpecialists(Array.isArray(overview?.specialists) ? overview.specialists : []);
      setRuns(Array.isArray(overview?.runs) ? overview.runs : []);
      setRunCounts(overview?.counts ?? null);
      setVersions(Array.isArray(overview?.versions) ? overview.versions : []);
      setScorecards(Array.isArray(overview?.scorecards) ? overview.scorecards : []);
      setRecentComparisons(Array.isArray(overview?.recentComparisons) ? overview.recentComparisons : []);
      setMethods(Array.isArray(overview?.methods) ? overview.methods : []);
      setDatasets(Array.isArray(overview?.datasets) ? overview.datasets : []);
      setSuites(Array.isArray(overview?.suites) ? overview.suites : []);
      setTrainingAvailable(Number(overview?.trainingAvailable ?? 0));
      setOverviewLoaded(true);
    } catch (e) {
      if (!mounted.current) return;
      setLastError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  /**
   * Applies one live training event. Kept separate from the SSE wiring so the
   * same reducer can be driven by a poll fallback or by tests.
   */
  const applyTrainingEvent = useCallback((event: TrainingEvent) => {
    setRuns((prev) => {
      const runId = 'run' in event ? event.run.id : event.runId;
      const index = prev.findIndex((run) => run.id === runId);
      if (index === -1) return prev;
      const run = prev[index];
      let next = run;
      if (event.type === 'run.status') next = { ...run, status: event.status, progress: event.progress };
      else if (event.type === 'run.epoch') next = { ...run, epochs: [...run.epochs, event.epoch], progress: event.progress };
      else if (event.type === 'run.checkpoint') next = { ...run, checkpoints: [...run.checkpoints, event.checkpoint] };
      else if (event.type === 'run.finished') next = event.run;
      else if (event.type === 'run.created') next = event.run;
      const copy = [...prev];
      copy[index] = next;
      return copy;
    });
    if (event.type === 'run.status' || event.type === 'run.finished') {
      void api
        .overview()
        .then((overview) => {
          if (!mounted.current) return;
          setRuns(Array.isArray(overview?.runs) ? overview.runs : []);
          setRunCounts(overview?.counts ?? null);
          setSpecialists(Array.isArray(overview?.specialists) ? overview.specialists : []);
          setVersions(Array.isArray(overview?.versions) ? overview.versions : []);
          setScorecards(Array.isArray(overview?.scorecards) ? overview.scorecards : []);
          setRecentComparisons(Array.isArray(overview?.recentComparisons) ? overview.recentComparisons : []);
        })
        .catch(() => undefined);
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

  // Live training progress over SSE. The stream is a convenience: the runs list
  // is refreshed from the API on connection and after every status change, so a
  // dropped stream degrades to "updates on refresh" rather than to wrong data.
  useEffect(() => {
    if (typeof EventSource === 'undefined') return;
    const source = new EventSource(trainingStreamUrl());
    const handle = (event: MessageEvent) => {
      try {
        applyTrainingEvent(JSON.parse(event.data) as TrainingEvent);
      } catch {
        /* ignore malformed payloads */
      }
    };
    for (const type of ['run.created', 'run.status', 'run.epoch', 'run.checkpoint', 'run.finished']) {
      source.addEventListener(type, handle as EventListener);
    }
    source.onerror = () => {
      /* EventSource reconnects on its own */
    };
    return () => source.close();
  }, [applyTrainingEvent]);

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

  /**
   * Appearance is applied to the document element rather than through React so
   * the glass, glow and motion layers in index.css can react with plain CSS
   * attribute selectors — including `prefers-reduced-motion`, which the 'auto'
   * setting hands over to entirely.
   */
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    const appearance = settings?.appearance;
    const resolvedMotion = !appearance || appearance.reduceMotion === 'auto' ? 'auto' : appearance.reduceMotion === 'on' ? 'off' : 'on';
    root.dataset.motion = resolvedMotion;
    root.dataset.glow = appearance && !appearance.ambientGlow ? 'off' : 'on';
    if (appearance) root.dataset.glass = appearance.glassIntensity;
  }, [settings?.appearance]);

  const specialist = useCallback((id: string) => specialists.find((entry) => entry.id === id) ?? null, [specialists]);
  const trainingActive = runs.some((run) => run.status === 'running' || run.status === 'queued');

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
      specialists,
      runs,
      runCounts,
      versions,
      scorecards,
      recentComparisons,
      methods,
      datasets,
      suites,
      trainingAvailable,
      overviewLoaded,
      trainingActive,
      specialist,
      refresh,
      refreshPlatform,
      applyTrainingEvent,
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
      specialists,
      runs,
      runCounts,
      versions,
      scorecards,
      recentComparisons,
      methods,
      datasets,
      suites,
      trainingAvailable,
      overviewLoaded,
      trainingActive,
      specialist,
      refresh,
      refreshPlatform,
      applyTrainingEvent,
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
