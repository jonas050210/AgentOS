/**
 * Node-side test setup (loaded for every test file before it is imported).
 *
 * The server modules create their stores at import time, so a test file that
 * never provides its own `AGENTOS_DATA_DIR` would make those stores read and
 * write the developer's real `data/` directory. Pointing the variable at a
 * throwaway directory keeps the suites hermetic: they never touch the working
 * tree — and never read settings or memory that happen to be on this machine.
 *
 * Tests that want their own isolated directory still set the variable in their
 * own hooks and re-import the modules after `vi.resetModules()`.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

if (!process.env.AGENTOS_TEST_DATA_DIR) {
  process.env.AGENTOS_TEST_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'agentos-vitest-'));
}
process.env.AGENTOS_DATA_DIR = process.env.AGENTOS_TEST_DATA_DIR;
process.env.AGENTOS_WORKSPACE_DIR ??= process.cwd();
