import { Settings, ToolInfo } from '../../shared/types.js';
import type { Permission } from './permissions.js';

export interface ToolContext {
  settings: Settings;
  logger: { info: (src: string, msg: string, meta?: Record<string, unknown>) => void };
  /** Directory tools are allowed to read/write inside. */
  workspaceDir: string;
  dataDir: string;
}

export interface ToolDefinition<Input = Record<string, unknown>, Output = unknown> {
  id: string;
  name: string;
  description: string;
  category: string;
  /** Permissions that must be granted by the user before the tool may run. */
  requires: Permission[];
  /** JSON-schema-ish description of the structured input, surfaced in the UI. */
  inputSchema: Record<string, unknown>;
  run(input: Input, ctx: ToolContext): Promise<Output>;
}

export interface ToolRunOutcome {
  toolId: string;
  success: boolean;
  output?: unknown;
  error?: string;
  durationMs: number;
  timestamp: string;
}

export function toToolInfo(def: ToolDefinition, available: boolean): ToolInfo {
  return {
    id: def.id,
    name: def.name,
    description: def.description,
    category: def.category,
    permission: (def.requires[0] ?? 'read') as ToolInfo['permission'],
    available,
    inputSchema: def.inputSchema,
  };
}

export interface ToolValidationError {
  path: string;
  message: string;
}

export function validateInput(
  def: ToolDefinition,
  input: Record<string, unknown>,
): ToolValidationError[] {
  const errors: ToolValidationError[] = [];
  const schema = def.inputSchema as {
    properties?: Record<string, { type?: string; required?: boolean; enum?: string[] }>;
  };
  const props = schema.properties ?? {};
  for (const [key, spec] of Object.entries(props)) {
    const value = input[key];
    if (value === undefined || value === null || value === '') {
      if (spec.required) errors.push({ path: key, message: 'is required' });
      continue;
    }
    if (spec.type === 'string' && typeof value !== 'string') {
      errors.push({ path: key, message: 'must be a string' });
    }
    if (spec.type === 'number' && typeof value !== 'number') {
      errors.push({ path: key, message: 'must be a number' });
    }
    if (spec.enum && typeof value === 'string' && !spec.enum.includes(value)) {
      errors.push({ path: key, message: `must be one of ${spec.enum.join(', ')}` });
    }
  }
  return errors;
}
