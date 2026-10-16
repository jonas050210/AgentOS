import path from 'node:path';
import fs from 'node:fs';

export class PathGuardError extends Error {
  readonly code = 'PATH_OUTSIDE_SANDBOX';
  constructor(public readonly requested: string) {
    super(`Path "${requested}" resolves outside the permitted directory.`);
    this.name = 'PathGuardError';
  }
}

/**
 * Resolves a user-supplied relative path inside `root` and refuses anything
 * that escapes it (via `..`, absolute paths or symlinks). This is the single
 * choke point for filesystem tools.
 */
export function resolveInside(root: string, relative: string): string {
  if (typeof relative !== 'string' || relative.trim() === '') {
    throw new PathGuardError(String(relative));
  }
  if (path.isAbsolute(relative)) throw new PathGuardError(relative);
  let realRoot: string;
  try {
    realRoot = fs.realpathSync(path.resolve(root));
  } catch {
    throw new Error(`Sandbox root "${root}" does not exist or is not accessible.`);
  }
  const candidate = path.resolve(realRoot, relative);
  const normalized = path.normalize(candidate);
  if (normalized !== realRoot && !normalized.startsWith(realRoot + path.sep)) {
    throw new PathGuardError(relative);
  }
  // Resolve symlinks for existing files to prevent escaping through links.
  if (fs.existsSync(normalized)) {
    const real = fs.realpathSync(normalized);
    if (real !== realRoot && !real.startsWith(realRoot + path.sep)) {
      throw new PathGuardError(relative);
    }
    return real;
  }
  return normalized;
}
