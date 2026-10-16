import { AgentChatRequest, AgentChatResponse, AgentMessage, AgentTraceStep, StatusLevel } from '../../shared/types.js';
import { providerRegistry } from '../providers/registry.js';
import { toolRegistry } from '../tools/registry.js';
import { CAPABILITIES, getCapability, routeCapability } from '../capabilities/registry.js';
import { conversationStore } from './conversations.js';
import { settingsStore } from '../settingsStore.js';
import { logger } from '../logger.js';
import { memoryStore } from '../memory/store.js';

interface PlannedToolCall {
  toolId: string;
  input: Record<string, unknown>;
  reason: string;
}

export class OrchestratorError extends Error {
  constructor(message: string, readonly statusCode = 400) {
    super(message);
    this.name = 'OrchestratorError';
  }
}

/**
 * Deterministic tool planner. It recognises a small set of explicit intents so
 * that the orchestration pipeline is genuinely exercised today; a model-backed
 * planner can replace `plan()` without touching the rest of the system.
 */
export function planTools(message: string, capabilityId: string, allowedToolIds: string[]): PlannedToolCall[] {
  const calls: PlannedToolCall[] = [];
  const allow = (id: string) => allowedToolIds.includes(id) && toolRegistry.get(id);

  const urlMatch = message.match(/https?:\/\/[^\s"'<>]+/);
  if (urlMatch && allow('http.fetch')) {
    calls.push({ toolId: 'http.fetch', input: { url: urlMatch[0] }, reason: 'Message contains a URL to retrieve' });
  }

  const calcMatch = message.match(/(?:calc(?:ulate)?|compute|evaluate|=)\s*([0-9+\-*/%^().\s]{3,})/i);
  if (calcMatch && allow('calc.evaluate')) {
    calls.push({ toolId: 'calc.evaluate', input: { expression: calcMatch[1].trim() }, reason: 'Arithmetic expression detected' });
  }

  if (/\b(read|open|cat)\b.*\b(file|src|server|readme)\b/i.test(message) && allow('workspace.read')) {
    const pathMatch = message.match(/[\w./-]+\.(?:ts|tsx|js|jsx|json|md|css|html|py)/i);
    if (pathMatch) {
      calls.push({ toolId: 'workspace.read', input: { path: pathMatch[0] }, reason: `File reference ${pathMatch[0]}` });
    }
  }

  if (/\b(remember|save|note|store)\b/i.test(message) && allow('memory.save')) {
    calls.push({
      toolId: 'memory.save',
      input: { title: message.slice(0, 60), content: message },
      reason: 'User asked to persist this information',
    });
  }

  if (capabilityId === 'coding.assistant' && allow('workspace.list') && calls.length === 0) {
    calls.push({ toolId: 'workspace.list', input: { path: '.' }, reason: 'Coding mode: survey workspace' });
  }

  if (capabilityId === 'research.assistant' && allow('memory.search') && calls.length === 0) {
    calls.push({ toolId: 'memory.search', input: { query: message }, reason: 'Research mode: consult memory' });
  }

  return calls.slice(0, 4);
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

export async function handleChat(request: AgentChatRequest): Promise<AgentChatResponse> {
  const started = Date.now();
  const trace: AgentTraceStep[] = [];
  const message = (request.message ?? '').trim();
  if (!message) throw new OrchestratorError('Message must not be empty');
  if (message.length > 8000) throw new OrchestratorError('Message exceeds 8000 characters');

  const settings = settingsStore.get();
  const capability = request.capabilityId ? getCapability(request.capabilityId) : undefined;
  const selected = capability ?? routeCapability(message);
  trace.push({
    step: 'route',
    detail: capability
      ? `Capability pinned by user: ${selected.name}`
      : `Auto-routed to ${selected.name} (${selected.id})`,
    status: 'ok',
  });

  const conversation = request.conversationId
    ? conversationStore.get(request.conversationId)
    : conversationStore.create(selected.id);
  if (request.conversationId && !conversation) {
    trace.push({ step: 'conversation', detail: `Unknown conversation ${request.conversationId}; started a new one`, status: 'warn' });
  }
  const conv = conversation ?? conversationStore.create(selected.id);

  const userMessage: AgentMessage = {
    id: `msg_${Date.now().toString(36)}`,
    role: 'user',
    content: message,
    timestamp: new Date().toISOString(),
  };
  conversationStore.append(conv.id, userMessage);

  // 1. Tool planning + execution
  const plans = planTools(message, selected.id, selected.toolIds);
  const contextBlocks: string[] = [];
  const toolResults: Array<{ toolId: string; success: boolean; output?: unknown; error?: string; durationMs: number }> = [];

  if (plans.length === 0) {
    trace.push({ step: 'plan', detail: 'No tool invocation required', status: 'skipped' });
  } else {
    for (const plan of plans) {
      const outcome = await toolRegistry.execute(plan.toolId, plan.input);
      toolResults.push({
        toolId: outcome.toolId,
        success: outcome.success,
        output: outcome.output,
        error: outcome.error,
        durationMs: outcome.durationMs,
      });
      trace.push({
        step: `tool:${plan.toolId}`,
        detail: outcome.success
          ? `${plan.reason} — succeeded in ${outcome.durationMs} ms`
          : `${plan.reason} — failed: ${outcome.error ?? 'unknown error'}`,
        status: outcome.success ? 'ok' : 'warn',
        durationMs: outcome.durationMs,
      });
      if (outcome.success) {
        contextBlocks.push(`[${plan.toolId}]\n${JSON.stringify(outcome.output).slice(0, 2000)}`);
      }
    }
  }

  // 2. Memory recall for grounding
  if (selected.toolIds.includes('memory.search') && memoryStore.count() > 0) {
    const hits = memoryStore.search(message, 3);
    if (hits.length > 0) {
      contextBlocks.push(
        `[memory]\n${hits.map((h) => `- ${h.title}: ${h.content.slice(0, 200)}`).join('\n')}`,
      );
      trace.push({ step: 'memory', detail: `Recalled ${hits.length} relevant note(s)`, status: 'ok' });
    }
  }

  // 3. Model selection + inference
  const provider = await providerRegistry.preferred();
  const providerInfo = provider.info();
  const usingFallback = provider.id === 'builtin';
  trace.push({
    step: 'model',
    detail: usingFallback
      ? 'No external provider available — using built-in offline engine'
      : `Using ${providerInfo.name}`,
    status: usingFallback ? 'warn' : 'ok',
  });

  const settingsModel = provider.id === 'openai' ? settings.providers.openai.model : settings.providers.ollama.model;
  const modelName = usingFallback ? 'offline-reasoner' : settingsModel;

  let answerText: string;
  let usage: { promptTokens?: number; completionTokens?: number } | undefined;
  const inferStarted = Date.now();
  try {
    const result = await provider.generate({
      model: modelName,
      prompt: message,
      system: buildSystemPrompt(selected.id, contextBlocks),
      temperature: 0.7,
    });
    answerText = result.text;
    usage = result.usage;
    trace.push({ step: 'inference', detail: `${modelName} responded in ${Date.now() - inferStarted} ms`, status: 'ok', durationMs: Date.now() - inferStarted });
  } catch (e) {
    const detail = e instanceof Error ? e.message : String(e);
    logger.error('orchestrator', 'Inference failed', { error: detail });
    trace.push({ step: 'inference', detail: `Provider failure: ${detail}`, status: 'error', durationMs: Date.now() - inferStarted });
    answerText = [
      'The configured model provider could not be reached, so this turn could not be completed.',
      `Details: ${detail}`,
      'You can retry, or switch provider in Settings → Providers.',
    ].join('\n');
  }

  const assistantMessage: AgentMessage = {
    id: `msg_${Date.now().toString(36)}_a`,
    role: 'assistant',
    content: answerText,
    timestamp: new Date().toISOString(),
    meta: {
      capabilityId: selected.id,
      providerId: provider.id,
      model: modelName,
      degraded: usingFallback,
      tools: toolResults.map((t) => ({ toolId: t.toolId, success: t.success })),
      usage,
      durationMs: Date.now() - started,
    },
  };
  conversationStore.append(conv.id, assistantMessage);

  logger.info('orchestrator', `Completed turn via ${provider.id}`, {
    capability: selected.id,
    tools: toolResults.length,
    durationMs: Date.now() - started,
  });

  return { conversationId: conv.id, message: assistantMessage, trace };
}

export function orchestrationStatus(): { status: StatusLevel; detail: string; capabilities: number; tools: number } {
  const tools = toolRegistry.all().length;
  return {
    status: 'ok',
    detail: `Orchestrator ready — ${CAPABILITIES.length} capabilities, ${tools} tools registered`,
    capabilities: CAPABILITIES.length,
    tools,
  };
}
