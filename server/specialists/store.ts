import fs from 'node:fs';
import path from 'node:path';
import { SpecialistDefinition, SpecialistPatch, CreateSpecialistRequest } from '../../shared/types.js';
import { logger } from '../logger.js';

export interface SpecialistOverride extends SpecialistPatch {
  enabled?: boolean;
}

interface SpecialistFile {
  overrides: Record<string, SpecialistOverride>;
  custom: SpecialistDefinition[];
}

const BUILT_IN: SpecialistDefinition[] = [
  {
    id: 'coding-ai',
    name: 'Coding AI',
    shortName: 'Coding',
    tagline: 'Reads your workspace and turns requests into concrete answers',
    description:
      'Understands code-related requests, inspects real files in the workspace and routes arithmetic or file questions to the right tool. Trains a decision head so common requests are resolved locally instead of being escalated.',
    category: 'coding',
    accent: '#6ea8ff',
    glyph: 'code',
    capabilityIds: ['coding.assistant'],
    toolIds: ['workspace.list', 'workspace.read', 'calc.evaluate', 'system.info', 'memory.search', 'memory.save'],
    systemPrompt:
      'You are AgentOS Coding AI. Prefer concrete, runnable answers and reference real files from the workspace. Never invent file contents. If a request needs a tool you do not have, say so.',
    params: { temperature: 0.2, maxTokens: 900, topP: 0.9 },
    trainingMethodIds: ['task_head_sgd', 'llm_adapter_finetune'],
    benchmarkSuiteIds: ['coding.routing', 'coding.expression'],
    outputs: ['Tool routing', 'Workspace answers', 'Code explanations'],
    builtIn: true,
    createdAt: '2026-01-01T00:00:00.000Z',
  },
  {
    id: 'research-ai',
    name: 'Research AI',
    shortName: 'Research',
    tagline: 'Finds sources, extracts targets and condenses findings',
    description:
      'Handles research requests: recognises URLs to retrieve, consults the local knowledge base and builds a tuned retrieval index over technical corpora so it can answer from sources rather than guessing.',
    category: 'research',
    accent: '#63d2bb',
    glyph: 'compass',
    capabilityIds: ['research.assistant'],
    toolIds: ['http.fetch', 'memory.search', 'memory.save'],
    systemPrompt:
      'You are AgentOS Research AI. Structure answers as findings. Only report what the provided material contains and name the source you used.',
    params: { temperature: 0.3, maxTokens: 1100, topP: 0.9 },
    trainingMethodIds: ['retrieval_index', 'task_head_sgd', 'llm_adapter_finetune'],
    benchmarkSuiteIds: ['research.extraction', 'research.retrieval'],
    outputs: ['Source extraction', 'Retrieval over corpora', 'Condensed findings'],
    builtIn: true,
    createdAt: '2026-01-01T00:00:00.000Z',
  },
  {
    id: 'knowledge-ai',
    name: 'Knowledge AI',
    shortName: 'Knowledge',
    tagline: 'Keeps your notes searchable and answers from them',
    description:
      'Owns the persistent knowledge base: saves notes, rebuilds a BM25 index over your corpus and answers with retrieved passages. Index quality is measured with recall@k and MRR on held-out queries.',
    category: 'knowledge',
    accent: '#b98cff',
    glyph: 'book',
    capabilityIds: ['knowledge.manager'],
    toolIds: ['memory.search', 'memory.save', 'memory.write'],
    systemPrompt:
      'You are AgentOS Knowledge AI. Prefer retrieved notes over recollection, quote the note titles you used and say when nothing was found.',
    params: { temperature: 0.2, maxTokens: 900, topP: 0.9 },
    trainingMethodIds: ['retrieval_index', 'task_head_sgd', 'llm_adapter_finetune'],
    benchmarkSuiteIds: ['knowledge.retrieval'],
    outputs: ['Note capture', 'Semantic recall', 'Grounded answers'],
    builtIn: true,
    createdAt: '2026-01-01T00:00:00.000Z',
  },
  {
    id: 'voice-ai',
    name: 'Voice AI',
    shortName: 'Voice',
    tagline: 'Prepares text for speech and drives the voice interface',
    description:
      'Normalises text for spoken output: expands abbreviations with a learned pronunciation lexicon and applies deterministic rules for numbers, currency, symbols and markdown. Speech itself runs in the client through the browser engines.',
    category: 'voice',
    accent: '#ffb45f',
    glyph: 'wave',
    capabilityIds: ['speech.interface'],
    toolIds: [],
    systemPrompt:
      'You are AgentOS Voice AI. Produce short, natural spoken-style sentences without markdown or symbols.',
    params: { temperature: 0.4, maxTokens: 500, topP: 0.95 },
    trainingMethodIds: ['lexicon_induction', 'llm_adapter_finetune'],
    benchmarkSuiteIds: ['voice.normalization'],
    outputs: ['Speech-ready text', 'Pronunciation lexicon', 'Dictation bridge'],
    builtIn: true,
    createdAt: '2026-01-01T00:00:00.000Z',
  },
  {
    id: 'general-ai',
    name: 'Concierge AI',
    shortName: 'Concierge',
    tagline: 'Routes work to the right specialist and handles general requests',
    description:
      'The default specialist behind the central assistant. It selects tools across the whole registry, escalates to a model provider when a request needs language reasoning, and can be trained on tool selection like any other specialist.',
    category: 'general',
    accent: '#4c8dff',
    glyph: 'spark',
    capabilityIds: ['general.assistant'],
    toolIds: ['system.info', 'calc.evaluate', 'workspace.list', 'workspace.read', 'memory.save', 'memory.search', 'http.fetch'],
    systemPrompt:
      'You are AgentOS Concierge. Answer directly and concisely, admit uncertainty, and state when information is missing instead of inventing it.',
    params: { temperature: 0.6, maxTokens: 1000, topP: 0.95 },
    trainingMethodIds: ['task_head_sgd', 'llm_adapter_finetune'],
    benchmarkSuiteIds: ['general.tool_selection'],
    outputs: ['Tool selection', 'General answers', 'Routing to specialists'],
    builtIn: true,
    createdAt: '2026-01-01T00:00:00.000Z',
  },
];

function root(): string {
  return path.join(process.env.AGENTOS_DATA_DIR || path.join(process.cwd(), 'data'), 'specialists');
}

function file(): string {
  return path.join(root(), 'specialists.json');
}

function read(): SpecialistFile {
  try {
    if (fs.existsSync(file())) {
      const parsed = JSON.parse(fs.readFileSync(file(), 'utf-8')) as SpecialistFile;
      return { overrides: parsed.overrides ?? {}, custom: Array.isArray(parsed.custom) ? parsed.custom : [] };
    }
  } catch (e) {
    logger.warn('specialists', 'Could not read specialist configuration', { error: String(e) });
  }
  return { overrides: {}, custom: [] };
}

function write(state: SpecialistFile): void {
  fs.mkdirSync(root(), { recursive: true });
  const target = file();
  const tmp = `${target}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2), 'utf-8');
  fs.renameSync(tmp, target);
}

const ACCENTS = ['#6ea8ff', '#63d2bb', '#b98cff', '#ffb45f', '#ff8fa3', '#7ee787', '#8ab4f8'];
const GLYPHS = ['spark', 'code', 'compass', 'book', 'wave', 'flask', 'shield'];

export const specialistStore = {
  builtIns(): SpecialistDefinition[] {
    return BUILT_IN.map((d) => ({ ...d }));
  },

  all(): SpecialistDefinition[] {
    const state = read();
    const builtIns = BUILT_IN.map((definition) => applyOverride(definition, state.overrides[definition.id]));
    return [...builtIns, ...state.custom.map((c) => c)];
  },

  get(id: string): SpecialistDefinition | null {
    return this.all().find((s) => s.id === id) ?? null;
  },

  isEnabled(id: string): boolean {
    const state = read();
    const override = state.overrides[id];
    return override?.enabled ?? true;
  },

  update(id: string, patch: SpecialistPatch & { enabled?: boolean }): SpecialistDefinition | null {
    const definition = this.get(id);
    if (!definition) return null;
    const state = read();
    if (definition.builtIn) {
      state.overrides[id] = { ...(state.overrides[id] ?? {}), ...patch };
    } else {
      const index = state.custom.findIndex((c) => c.id === id);
      if (index === -1) return null;
      const merged: SpecialistDefinition = {
        ...state.custom[index],
        ...sanitisePatch(patch),
        params: { ...state.custom[index].params, ...(patch.params ?? {}) },
      };
      state.custom[index] = merged;
      if (patch.enabled !== undefined) state.overrides[id] = { ...(state.overrides[id] ?? {}), enabled: patch.enabled };
    }
    write(state);
    logger.info('specialists', `Updated specialist ${id}`);
    return this.get(id);
  },

  create(request: CreateSpecialistRequest): SpecialistDefinition {
    const state = read();
    const templates = request.templateId ? this.get(request.templateId) : null;
    const baseName = request.name.trim().slice(0, 60) || 'New Specialist';
    const slug = baseName
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '')
      .slice(0, 40);
    let id = `${slug || 'specialist'}-ai`;
    let counter = 2;
    while (this.get(id)) {
      id = `${slug || 'specialist'}-${counter++}-ai`;
    }

    const definition: SpecialistDefinition = {
      id,
      name: baseName,
      shortName: request.shortName?.trim() || baseName.split(' ')[0],
      tagline: request.tagline?.trim() || templates?.tagline || 'Custom AgentOS specialist',
      description: request.description?.trim() || templates?.description || 'A specialist you created in AgentOS. Configure its tools, prompt and training below.',
      category: request.category ?? templates?.category ?? 'custom',
      accent: request.accent ?? templates?.accent ?? ACCENTS[state.custom.length % ACCENTS.length],
      glyph: request.glyph ?? templates?.glyph ?? GLYPHS[state.custom.length % GLYPHS.length],
      capabilityIds: templates?.capabilityIds ?? [],
      toolIds: request.toolIds ?? templates?.toolIds ?? ['calc.evaluate', 'system.info', 'memory.search'],
      systemPrompt:
        request.systemPrompt?.trim() ||
        templates?.systemPrompt ||
        `You are ${baseName}, a focused AgentOS specialist. Answer within your scope and state when a request is outside it.`,
      params: { temperature: 0.5, maxTokens: 900, topP: 0.9, ...(templates?.params ?? {}), ...(request.params ?? {}) },
      trainingMethodIds: templates?.trainingMethodIds ?? ['task_head_sgd', 'retrieval_index', 'llm_adapter_finetune'],
      benchmarkSuiteIds: templates?.benchmarkSuiteIds ?? [],
      outputs: templates?.outputs ?? ['Custom responses'],
      builtIn: false,
      createdAt: new Date().toISOString(),
    };

    state.custom.push(definition);
    write(state);
    logger.info('specialists', `Created specialist ${definition.id}`);
    return definition;
  },

  remove(id: string): boolean {
    const state = read();
    const index = state.custom.findIndex((c) => c.id === id);
    if (index === -1) return false;
    state.custom.splice(index, 1);
    delete state.overrides[id];
    write(state);
    logger.warn('specialists', `Deleted specialist ${id}`);
    return true;
  },
};

function applyOverride(definition: SpecialistDefinition, override?: SpecialistOverride): SpecialistDefinition {
  if (!override) return { ...definition };
  return {
    ...definition,
    ...sanitisePatch(override),
    params: { ...definition.params, ...(override.params ?? {}) },
    toolIds: override.toolIds ?? definition.toolIds,
    capabilityIds: override.capabilityIds ?? definition.capabilityIds,
  };
}

function sanitisePatch(patch: SpecialistPatch | SpecialistOverride): Partial<SpecialistDefinition> {
  const out: Partial<SpecialistDefinition> = {};
  if (typeof patch.name === 'string') out.name = patch.name.slice(0, 60);
  if (typeof patch.shortName === 'string') out.shortName = patch.shortName.slice(0, 30);
  if (typeof patch.tagline === 'string') out.tagline = patch.tagline.slice(0, 160);
  if (typeof patch.description === 'string') out.description = patch.description.slice(0, 600);
  if (typeof patch.accent === 'string' && /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(patch.accent)) out.accent = patch.accent;
  if (typeof patch.glyph === 'string') out.glyph = patch.glyph.slice(0, 24);
  if (typeof patch.systemPrompt === 'string') out.systemPrompt = patch.systemPrompt.slice(0, 4000);
  return out;
}
