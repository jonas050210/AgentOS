import path from 'node:path';
import { Settings, ToolInfo } from '../../shared/types.js';
import { BUILTIN_TOOLS } from './builtin.js';
import { MEMORY_TOOLS } from '../memory/tools.js';
import { ToolDefinition, ToolRunOutcome, toToolInfo, validateInput } from './types.js';
import { assertAllowed, isAllowed, PermissionError } from './permissions.js';
import { PathGuardError } from './paths.js';
import { logger } from '../logger.js';
import { settingsStore } from '../settingsStore.js';

export class ToolRegistry {
  private tools = new Map<string, ToolDefinition>();

  constructor(tools: ToolDefinition[] = [...BUILTIN_TOOLS, ...MEMORY_TOOLS]) {
    for (const t of tools) this.register(t);
  }

  register(tool: ToolDefinition) {
    if (this.tools.has(tool.id)) logger.warn('tools', `Overwriting tool ${tool.id}`);
    this.tools.set(tool.id, tool);
  }

  get(id: string): ToolDefinition | undefined {
    return this.tools.get(id);
  }

  all(): ToolDefinition[] {
    return [...this.tools.values()];
  }

  describe(settings: Settings = settingsStore.get()): ToolInfo[] {
    return this.all().map((t) => toToolInfo(t, this.isRunnable(t, settings)));
  }

  isRunnable(tool: ToolDefinition, settings: Settings): boolean {
    return tool.requires.every((p) => isAllowed(p, settings));
  }

  private context() {
    return {
      settings: settingsStore.get(),
      logger: {
        info: (src: string, msg: string, meta?: Record<string, unknown>) => logger.info(src, msg, meta),
      },
      workspaceDir: process.env.AGENTOS_WORKSPACE_DIR || process.cwd(),
      dataDir: process.env.AGENTOS_DATA_DIR || path.join(process.cwd(), 'data'),
    };
  }

  /** Executes a tool with validation, permission checks and structured error reporting. */
  async execute(id: string, input: Record<string, unknown> = {}): Promise<ToolRunOutcome> {
    const started = Date.now();
    const base = { toolId: id, timestamp: new Date().toISOString() };
    const tool = this.tools.get(id);
    if (!tool) {
      return { ...base, success: false, error: `Unknown tool "${id}"`, durationMs: Date.now() - started };
    }
    const settings = settingsStore.get();
    const invalid = validateInput(tool, input);
    if (invalid.length > 0) {
      const error = `Invalid input: ${invalid.map((e) => `${e.path} ${e.message}`).join('; ')}`;
      logger.warn('tools', `Rejected ${id}: ${error}`);
      return { ...base, success: false, error, durationMs: Date.now() - started };
    }
    try {
      for (const p of tool.requires) assertAllowed(p, id, settings);
      const output = await tool.run(input, this.context());
      logger.info('tools', `Executed ${id}`, { durationMs: Date.now() - started });
      return { ...base, success: true, output, durationMs: Date.now() - started };
    } catch (e) {
      const isPermission = e instanceof PermissionError;
      const isPath = e instanceof PathGuardError;
      const message = e instanceof Error ? e.message : String(e);
      logger[isPermission ? 'warn' : 'error']('tools', `Tool ${id} failed: ${message}`);
      return {
        ...base,
        success: false,
        error: message,
        output: isPermission || isPath ? { code: (e as PermissionError).code } : undefined,
        durationMs: Date.now() - started,
      };
    }
  }
}

export const toolRegistry = new ToolRegistry();
