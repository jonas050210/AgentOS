import {
  AgentChatRequest,
  AgentChatResponse,
  AgentMessage,
  AgentTraceStep,
  SpecialistSummary,
  StatusLevel,
} from '../../shared/types.js';
import { toolRegistry } from '../tools/registry.js';
import { CAPABILITIES, getCapability, routeCapability } from '../capabilities/registry.js';
import { conversationStore } from './conversations.js';
import { settingsStore } from '../settingsStore.js';
import { logger } from '../logger.js';
import { memoryStore } from '../memory/store.js';
import { planForSpecialist } from '../specialists/policy.js';
import { runSpecialist } from '../specialists/inference.js';
import { specialistRegistry, specialistForCapability, CAPABILITY_TO_SPECIALIST } from '../specialists/registry.js';
import { runStore } from '../training/runs.js';
import { benchmarkStore } from '../benchmarks/store.js';

export class OrchestratorError extends Error {
  constructor(message: string, readonly statusCode = 400) {
    super(message);
    this.name = 'OrchestratorError';
  }
}

interface PlannedToolCall {
  toolId: string;
  input: Record<string, unknown>;
  reason: string;
}

/**
 * Deterministic tool planner (kept for compatibility with the pre-specialist
 * API). It delegates to the shared rule policy so chat, the playground and the
 * benchmarks all reason about tool selection in exactly the same way.
 */
export function planTools(message: string, capabilityId: string, allowedToolIds: string[]): PlannedToolCall[] {
  const specialistId = specialistForCapability(capabilityId) ?? 'general-ai';
  const plan = planForSpecialist(specialistId, message, allowedToolIds);
  if (plan) return [{ toolId: plan.toolId, input: plan.input, reason: plan.reason }];
  if (capabilityId === 'coding.assistant' && allowedToolIds.includes('workspace.list') && toolRegistry.get('workspace.list')) {
    return [{ toolId: 'workspace.list', input: { path: '.' }, reason: 'Coding mode: survey workspace' }];
  }
  if (capabilityId === 'research.assistant' && allowedToolIds.includes('memory.search') && toolRegistry.get('memory.search')) {
    return [{ toolId: 'memory.search', input: { query: message }, reason: 'Research mode: consult memory' }];
  }
  return [];
}

export function buildSystemPrompt(capabilityId: string, context: string[]): string {
  const cap = getCapability(capabilityId) ?? CAPABILITIES[0];
  const parts = [cap.systemPrompt];
  if (context.length > 0) {
    parts.push('Retrieved context from local tools (authoritative):');
    parts.push(context.join('\n---\n').slice(0, 6000));
  }
  parts.push('Clearly state when information is missing rather than inventing it.');
  return parts.join('\n\n');
}

export interface RouteDecision {
  specialist: SpecialistSummary;
  reason: string;
  pinned: boolean;
}

/** Chooses the specialist for a request: explicit pin → capability → keywords. */
export function routeToSpecialist(request: AgentChatRequest): RouteDecision {
  const settings = settingsStore.get();

  if (request.specialistId) {
    const specialist = specialistRegistry.get(request.specialistId);
    if (!specialist) throw new OrchestratorError(`Unknown specialist "${request.specialistId}"`, 404);
    if (!specialist.enabled) {
      throw new OrchestratorError(`${specialist.name} is disabled. Enable it in its workspace settings.`, 409);
    }
    return { specialist, reason: `Pinned by the user: ${specialist.name}`, pinned: true };
  }

  if (request.capabilityId) {
    const specialistId = specialistForCapability(request.capabilityId);
    const specialist = specialistId ? specialistRegistry.get(specialistId) : null;
    if (specialist && specialist.enabled) {
      return { specialist, reason: `Capability ${request.capabilityId} maps to ${specialist.name}`, pinned: true };
    }
  }

  const routed = routeCapability(request.message ?? '');
  const specialistId = specialistForCapability(routed.id);
  const specialist = (specialistId ? specialistRegistry.get(specialistId) : null) ?? specialistRegistry.get('general-ai');
  if (!specialist) throw new OrchestratorError('No specialist is available to handle this request.', 503);

  if (!settings.routing.autoRoute && specialist.id !== 'general-ai') {
    const fallback = specialistRegistry.get('general-ai');
    if (fallback) {
      return { specialist: fallback, reason: `Automatic routing is disabled in Settings → Routing — using ${fallback.name}`, pinned: false };
    }
  }

  return {
    specialist,
    reason: `Auto-routed by intent (${routed.name}) to ${specialist.name}`,
    pinned: false,
  };
}

export async function handleChat(request: AgentChatRequest): Promise<AgentChatResponse> {
  const started = Date.now();
  const trace: AgentTraceStep[] = [];
  const message = (request.message ?? '').trim();
  if (!message) throw new OrchestratorError('Message must not be empty');
  if (message.length > 8000) throw new OrchestratorError('Message exceeds 8000 characters');

  const route = routeToSpecialist(request);
  trace.push({
    step: 'route',
    detail: route.reason,
    status: route.pinned ? 'ok' : 'ok',
  });

  const conversation = request.conversationId ? conversationStore.get(request.conversationId) : undefined;
  if (request.conversationId && !conversation) {
    trace.push({ step: 'conversation', detail: `Unknown conversation ${request.conversationId}; started a new one`, status: 'warn' });
  }
  const conv = conversation ?? conversationStore.create(route.specialist.capabilityIds[0] ?? 'general.assistant', route.specialist.id);

  const userMessage: AgentMessage = {
    id: `msg_${Date.now().toString(36)}`,
    role: 'user',
    content: message,
    timestamp: new Date().toISOString(),
  };
  conversationStore.append(conv.id, userMessage);

  // Memory recall is injected as grounding for whatever answers next.
  const contextBlocks: string[] = [];
  if (route.specialist.toolIds.includes('memory.search') && memoryStore.count() > 0) {
    const hits = memoryStore.search(message, 3);
    if (hits.length > 0) {
      contextBlocks.push(`[memory]\n${hits.map((h) => `- ${h.title}: ${h.content.slice(0, 200)}`).join('\n')}`);
      trace.push({ step: 'memory', detail: `Recalled ${hits.length} relevant note(s)`, status: 'ok' });
    }
  }

  const result = await runSpecialist(route.specialist.id, message, { mode: 'chat', context: contextBlocks });
  for (const step of result.trace) trace.push(step);

  // Reporting follows the run that actually happened. The specialist result names
  // the provider (if any) that produced the answer, so the orchestrator does not
  // probe availability a second time — a second probe costs latency and could
  // describe a different provider than the one that answered.
  const usedOfflineFallback = result.answeredBy === 'offline-engine';
  const providerLabel = result.providerName ?? result.providerId ?? 'The model provider';
  if (result.providerError) {
    // Honest reporting: the provider was unreachable, so nothing answered.
    trace.push({
      step: 'model',
      detail: `${providerLabel} was unreachable: ${result.providerError}`,
      status: 'error',
    });
  } else if (usedOfflineFallback) {
    trace.push({
      step: 'model',
      detail: 'No external provider available — the offline engine answered instead',
      status: 'warn',
    });
  } else if (result.providerId) {
    trace.push({
      step: 'model',
      detail: `${providerLabel} supplied the language answer${result.model ? ` (${result.model})` : ''}`,
      status: 'ok',
    });
  } else if (result.escalated) {
    trace.push({
      step: 'model',
      detail: `Escalation skipped — ${result.escalationReason ?? 'no provider was contacted'}`,
      status: 'skipped',
    });
  } else {
    trace.push({
      step: 'execution',
      detail: `${route.specialist.name} handled this locally via ${result.engine}`,
      status: 'ok',
    });
  }

  const assistantMessage: AgentMessage = {
    id: `msg_${Date.now().toString(36)}_a`,
    role: 'assistant',
    content: result.answer,
    timestamp: new Date().toISOString(),
    meta: {
      // `capabilityId` is retained for backwards compatibility with earlier clients.
      capabilityId: route.specialist.capabilityIds[0] ?? CAPABILITY_TO_SPECIALIST[route.specialist.id] ?? null,
      specialistId: route.specialist.id,
      specialistName: route.specialist.name,
      engine: result.engine,
      versionId: result.versionId,
      versionLabel: result.versionLabel,
      predicted: result.predicted,
      confidence: result.confidence,
      escalated: result.escalated,
      escalationReason: result.escalationReason,
      // `providerId` names the provider this turn involved — the one that answered,
      // or (with `providerError`) the one that failed. `local` when none was called.
      providerId: result.providerId ?? 'local',
      providerName: result.providerName,
      // The model that produced the answer; a failed provider produced nothing,
      // so reporting an engine string as a "model" there would be a fiction.
      model: result.model ?? (result.providerError ? null : result.engine),
      degraded: usedOfflineFallback,
      answeredBy: result.answeredBy,
      providerError: result.providerError,
      tools: result.toolCalls.map((t) => ({ toolId: t.toolId, success: t.success })),
      latencyMs: result.latencyMs,
      durationMs: Date.now() - started,
    },
  };
  conversationStore.append(conv.id, assistantMessage);

  logger.info('orchestrator', `Completed turn with ${route.specialist.name}`, {
    escalated: result.escalated,
    predicted: result.predicted,
    durationMs: Date.now() - started,
  });

  return { conversationId: conv.id, message: assistantMessage, trace };
}

export function orchestrationStatus(): { status: StatusLevel; detail: string; capabilities: number; tools: number } {
  const specialists = specialistRegistry.list();
  const running = runStore.counts().running;
  const tools = toolRegistry.all().length;
  return {
    status: 'ok',
    detail: `${specialists.length} specialists · ${tools} tools · ${running} training run(s) active`,
    capabilities: CAPABILITIES.length,
    tools,
  };
}

export function platformStats() {
  const specialists = specialistRegistry.list();
  return {
    specialists: specialists.length,
    trained: specialists.filter((s) => s.activeVersion).length,
    training: runStore.counts(),
    scorecards: benchmarkStore.list({ limit: 100 }).length,
  };
}
