import { ToolDefinition } from '../tools/types.js';
import { memoryStore } from './store.js';

export const memorySearchTool: ToolDefinition<{ query?: string }, { results: unknown[] }> = {
  id: 'memory.search',
  name: 'Search memory',
  description: 'Searches the persistent AgentOS knowledge store.',
  category: 'knowledge',
  requires: [],
  inputSchema: { type: 'object', properties: { query: { type: 'string' } } },
  async run({ query = '' }) {
    return {
      results: memoryStore.search(query, 10).map((r) => ({
        id: r.id,
        title: r.title,
        snippet: r.content.slice(0, 240),
        tags: r.tags,
        updatedAt: r.updatedAt,
      })),
    };
  },
};

export const memorySaveTool: ToolDefinition<{ title: string; content: string; tags?: string[] }, { id: string }> = {
  id: 'memory.save',
  name: 'Save to memory',
  description: 'Stores a titled entry in the persistent AgentOS knowledge store.',
  category: 'knowledge',
  requires: [],
  inputSchema: {
    type: 'object',
    properties: {
      title: { type: 'string', required: true },
      content: { type: 'string', required: true },
    },
  },
  async run({ title, content, tags }) {
    const record = memoryStore.add({ title, content, tags });
    return { id: record.id };
  },
};

export const MEMORY_TOOLS: ToolDefinition[] = [
  memorySearchTool as unknown as ToolDefinition,
  memorySaveTool as unknown as ToolDefinition,
];
