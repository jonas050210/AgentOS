import { CapabilityInfo, Settings } from '../../shared/types.js';

export interface CapabilityDefinition {
  id: string;
  name: string;
  description: string;
  category: CapabilityInfo['category'];
  /** System directive injected when this capability drives a request. */
  systemPrompt: string;
  /** Tools the capability is allowed to invoke. */
  toolIds: string[];
  /** Keyword hints used by the router for automatic selection. */
  keywords: string[];
}

export const CAPABILITIES: CapabilityDefinition[] = [
  {
    id: 'general.assistant',
    name: 'General assistant',
    description: 'Everyday conversation, drafting and explanations.',
    category: 'general',
    systemPrompt: 'You are AgentOS, a concise and helpful desktop assistant. Answer directly and admit uncertainty.',
    toolIds: ['system.info', 'calc.evaluate', 'memory.search'],
    keywords: ['hello', 'hi', 'help', 'explain', 'write', 'draft', 'summar', 'what is'],
  },
  {
    id: 'coding.assistant',
    name: 'Coding assistant',
    description: 'Code exploration, refactoring guidance and workspace inspection.',
    category: 'coding',
    systemPrompt:
      'You are AgentOS operating in coding mode. Prefer concrete, runnable code and reference real files from the workspace. Never invent file contents.',
    toolIds: ['workspace.list', 'workspace.read', 'system.info', 'calc.evaluate'],
    keywords: ['code', 'bug', 'refactor', 'function', 'typescript', 'python', 'class', 'error', 'test', 'compile'],
  },
  {
    id: 'research.assistant',
    name: 'Research assistant',
    description: 'Gathers external sources and condenses them into structured findings.',
    category: 'research',
    systemPrompt:
      'You are AgentOS in research mode. Structure answers as findings with sources. Only report what the provided material contains.',
    toolIds: ['http.fetch', 'memory.search', 'memory.save'],
    keywords: ['research', 'search', 'source', 'latest', 'compare', 'url', 'http', 'news', 'find'],
  },
  {
    id: 'knowledge.manager',
    name: 'Knowledge manager',
    description: 'Stores, tags and retrieves your notes in the persistent memory store.',
    category: 'knowledge',
    systemPrompt:
      'You are AgentOS in knowledge-management mode. Help the user organise notes and answers; prefer retrieving stored memory over guessing.',
    toolIds: ['memory.search', 'memory.save', 'memory.write'],
    keywords: ['note', 'remember', 'memory', 'save', 'store', 'tag', 'knowledge', 'recall'],
  },
  {
    id: 'speech.interface',
    name: 'Voice interface',
    description: 'Speech-to-text and text-to-speech wrapper around any capability.',
    category: 'speech',
    systemPrompt: 'You are AgentOS in voice mode. Reply with short spoken-style sentences.',
    toolIds: [],
    keywords: ['voice', 'speak', 'listen', 'dictate', 'say'],
  },
];

export function getCapability(id: string): CapabilityDefinition | undefined {
  return CAPABILITIES.find((c) => c.id === id);
}

/** Deterministic keyword router used when the user does not pin a capability. */
export function routeCapability(message: string): CapabilityDefinition {
  const text = message.toLowerCase();
  let best: { cap: CapabilityDefinition; score: number } | null = null;
  for (const cap of CAPABILITIES) {
    const score = cap.keywords.reduce((acc, k) => acc + (text.includes(k) ? 1 : 0), 0);
    if (score > 0 && (!best || score > best.score)) best = { cap, score };
  }
  return best?.cap ?? getCapability('general.assistant')!;
}

export function describeCapabilities(
  settings: Settings,
  toolAvailability: (toolId: string) => boolean,
  hasExternalModel: boolean,
): CapabilityInfo[] {
  return CAPABILITIES.map((cap) => {
    const toolsReady = cap.toolIds.every((t) => toolAvailability(t));
    const isSpeech = cap.category === 'speech';
    let status: CapabilityInfo['status'];
    if (isSpeech) {
      status = settings.voice.sttEnabled || settings.voice.ttsEnabled ? 'partial' : 'unavailable';
    } else if (!toolsReady) {
      status = 'partial';
    } else if (!hasExternalModel) {
      status = 'partial';
    } else {
      status = 'available';
    }
    return {
      id: cap.id,
      name: cap.name,
      description: cap.description,
      category: cap.category,
      status,
      backingModelIds: [],
      toolIds: cap.toolIds,
    };
  });
}
