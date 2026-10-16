import {
  CreateSpecialistRequest,
  SpecialistDefinition,
  SpecialistPatch,
  SpecialistState,
  SpecialistSummary,
} from '../../shared/types.js';
import { toolRegistry } from '../tools/registry.js';
import { settingsStore } from '../settingsStore.js';
import { versionStore } from '../models/versions.js';
import { runStore } from '../training/runs.js';
import { specialistStore } from './store.js';
import { benchmarkStore } from '../benchmarks/store.js';

/** Maps the legacy capability ids onto specialists so both APIs stay consistent. */
export const CAPABILITY_TO_SPECIALIST: Record<string, string> = {
  'general.assistant': 'general-ai',
  'coding.assistant': 'coding-ai',
  'research.assistant': 'research-ai',
  'knowledge.manager': 'knowledge-ai',
  'speech.interface': 'voice-ai',
};

export function specialistForCapability(capabilityId: string): string | null {
  return CAPABILITY_TO_SPECIALIST[capabilityId] ?? null;
}

export function summarize(definition: SpecialistDefinition): SpecialistSummary {
  const settings = settingsStore.get();
  const enabled = specialistStore.isEnabled(definition.id);
  const versions = versionStore.list(definition.id);
  const activeVersion = versions.find((v) => v.status === 'active') ?? null;
  const runs = runStore.list({ specialistId: definition.id, limit: 200 });
  const running = runs.find((r) => r.status === 'running' || r.status === 'queued') ?? null;
  const blockedTools = definition.toolIds.filter((toolId) => {
    const tool = toolRegistry.get(toolId);
    return !tool || !toolRegistry.isRunnable(tool, settings);
  });
  const scorecards = benchmarkStore.list({ specialistId: definition.id, limit: 20 });
  const benchmarkScore = scorecards.length > 0 ? scorecards[0].quality : null;

  let state: SpecialistState = 'ready';
  let stateDetail = activeVersion
    ? `Active version ${activeVersion.label} · ${activeVersion.primaryMetric ?? 'metric'} ${formatMetric(activeVersion.metrics[activeVersion.primaryMetric ?? ''])}`
    : 'No trained version yet';

  if (!enabled) {
    state = 'disabled';
    stateDetail = 'Disabled — hidden from automatic routing';
  } else if (running) {
    state = 'training';
    stateDetail = `${running.methodName} · ${Math.round(running.progress * 100)}% complete`;
  } else if (!activeVersion) {
    // No active version, but a trained candidate may still exist: it was measured
    // and did not clear the promotion gate. Saying "add a training run" then would
    // be wrong, so the state names the candidate and why it is not live.
    const candidate = versions.find((version) => version.status === 'candidate') ?? null;
    state = 'untrained';
    stateDetail = candidate
      ? `Untrained — ${candidate.label} was trained but did not clear the promotion gate; the current rule policy still scores at least as well. Review it under Training & versions.`
      : `Untrained — ${definition.trainingMethodIds.length} compatible method(s); add a training run to give it a local head`;
  } else if (blockedTools.length > 0) {
    state = 'degraded';
    stateDetail = `${blockedTools.length} bound tool(s) blocked by permissions: ${blockedTools.join(', ')}`;
  }

  return {
    ...definition,
    enabled,
    state,
    stateDetail,
    activeVersion,
    versions,
    runCount: runs.length,
    lastRunAt: runs[0]?.startedAt ?? null,
    benchmarkScore,
    permissionsReady: blockedTools.length === 0,
    blockedTools,
  };
}

function formatMetric(value: number | undefined): string {
  if (value === undefined) return 'n/a';
  return value <= 1 ? `${(value * 100).toFixed(1)}%` : value.toFixed(2);
}

export const specialistRegistry = {
  list(): SpecialistSummary[] {
    return specialistStore.all().map(summarize);
  },

  listEnabled(): SpecialistSummary[] {
    return this.list().filter((s) => s.enabled);
  },

  get(id: string): SpecialistSummary | null {
    const definition = specialistStore.get(id);
    return definition ? summarize(definition) : null;
  },

  definition(id: string): SpecialistDefinition | null {
    return specialistStore.get(id);
  },

  create(request: CreateSpecialistRequest): SpecialistSummary {
    const definition = specialistStore.create(request);
    return summarize(definition);
  },

  update(id: string, patch: SpecialistPatch & { enabled?: boolean }): SpecialistSummary | null {
    const updated = specialistStore.update(id, patch);
    return updated ? summarize(updated) : null;
  },

  remove(id: string): boolean {
    const definition = specialistStore.get(id);
    if (!definition || definition.builtIn) return false;
    return specialistStore.remove(id);
  },
};

export function getSpecialist(id: string): SpecialistSummary | null {
  return specialistRegistry.get(id);
}
