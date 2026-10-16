import { AgentMessage } from '../../shared/types.js';
import { logger } from '../logger.js';

const MAX_TURNS = 40;
const MAX_CONVERSATIONS = 50;

export interface Conversation {
  id: string;
  title: string;
  capabilityId: string;
  /** Specialist that handled this conversation (set when the first turn routed). */
  specialistId: string | null;
  createdAt: string;
  messages: AgentMessage[];
}

/** In-memory conversation history with a bounded footprint. */
export class ConversationStore {
  private conversations = new Map<string, Conversation>();

  create(capabilityId: string, specialistId: string | null = null, title = 'New conversation'): Conversation {
    const conv: Conversation = {
      id: `conv_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`,
      title,
      capabilityId,
      specialistId,
      createdAt: new Date().toISOString(),
      messages: [],
    };
    this.conversations.set(conv.id, conv);
    if (this.conversations.size > MAX_CONVERSATIONS) {
      const oldest = [...this.conversations.values()].sort((a, b) => a.createdAt.localeCompare(b.createdAt))[0];
      this.conversations.delete(oldest.id);
      logger.debug('conversations', `Evicted conversation ${oldest.id}`);
    }
    return conv;
  }

  get(id: string): Conversation | undefined {
    return this.conversations.get(id);
  }

  list(): Conversation[] {
    return [...this.conversations.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  append(id: string, message: AgentMessage): Conversation {
    const conv = this.conversations.get(id) ?? this.create('general.assistant', 'general-ai');
    conv.messages.push(message);
    if (conv.messages.length > MAX_TURNS) conv.messages.splice(0, conv.messages.length - MAX_TURNS);
    if (conv.messages.length === 1 && message.role === 'user') {
      conv.title = message.content.slice(0, 48) || conv.title;
    }
    return conv;
  }

  reset(): void {
    this.conversations.clear();
  }
}

export const conversationStore = new ConversationStore();
