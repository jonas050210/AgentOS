import { Settings } from '../../shared/types.js';
import { logger } from '../logger.js';

export type Permission = 'read' | 'write' | 'execute' | 'network';

export class PermissionError extends Error {
  readonly code = 'PERMISSION_DENIED';
  constructor(public readonly permission: Permission, public readonly toolId: string) {
    super(`Permission "${permission}" is required by tool "${toolId}" but is disabled in Settings.`);
    this.name = 'PermissionError';
  }
}

/**
 * Maps declared tool permissions onto user-controlled settings switches.
 * Every tool action must pass through this gate before touching the system.
 */
export function isAllowed(permission: Permission, settings: Settings): boolean {
  switch (permission) {
    case 'read':
      return settings.permissions.allowFileRead;
    case 'write':
      return settings.permissions.allowFileWrite;
    case 'network':
      return settings.permissions.allowNetwork;
    case 'execute':
      return settings.permissions.allowShell;
  }
}

export function assertAllowed(permission: Permission, toolId: string, settings: Settings): void {
  if (!isAllowed(permission, settings)) {
    logger.warn('permissions', `Denied "${permission}" for tool ${toolId}`);
    throw new PermissionError(permission, toolId);
  }
}
